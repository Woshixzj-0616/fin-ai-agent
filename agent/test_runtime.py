"""B1 runtime, event bus and session isolation tests."""
from __future__ import annotations

import json
import queue
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from runtime import (
        EventBus,
        LegacyRunAdapter,
        RunEvent,
        RunManager,
        RunNotVisible,
        RunStateError,
        RunStatus,
        phase_for_event,
    )
except ModuleNotFoundError:
    from agent.runtime import (
        EventBus,
        LegacyRunAdapter,
        RunEvent,
        RunManager,
        RunNotVisible,
        RunStateError,
        RunStatus,
        phase_for_event,
    )

try:
    from materials import Run as LegacyRun
except ModuleNotFoundError:
    from agent.materials import Run as LegacyRun


def event_kwargs(run_id: str, number: int = 1) -> dict:
    return {
        "run_id": run_id,
        "phase": "run",
        "event_type": f"test.{number}",
        "actor": "test",
        "title": f"测试事件 {number}",
        "summary": "测试事件摘要",
        "status": "completed",
        "payload": {"number": number},
    }


class RunEventTests(unittest.TestCase):
    def test_roundtrip_preserves_contract(self):
        event = RunEvent.create(seq=7, timestamp="2026-10-11T20:00:00+08:00", **event_kwargs("r1", 7))
        restored = RunEvent.from_dict(event.to_dict())
        self.assertEqual(restored.to_dict(), event.to_dict())

    def test_seq_must_be_positive(self):
        with self.assertRaises(ValueError):
            RunEvent.create(seq=0, timestamp="2026-10-11T20:00:00+08:00", **event_kwargs("r1"))

    def test_phase_for_legacy_event(self):
        self.assertEqual(phase_for_event("llm_response"), "model")
        self.assertEqual(phase_for_event("agent_tool"), "tools")
        self.assertEqual(phase_for_event("calculation"), "calculation")
        self.assertEqual(phase_for_event("unknown_event"), "system")


class EventBusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime_bus_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bus = EventBus(self.root)

    def test_publish_assigns_seq_and_persists_jsonl(self):
        first = self.bus.publish(**event_kwargs("r1", 1))
        second = self.bus.publish(**event_kwargs("r1", 2))
        self.assertEqual((first.seq, second.seq), (1, 2))
        lines = (self.root / "r1" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["seq"] for line in lines], [1, 2])

    def test_subscribe_receives_live_event_and_can_replay_after_seq(self):
        first = self.bus.publish(**event_kwargs("r1", 1))
        subscription = self.bus.subscribe("r1", after_seq=first.seq)
        second = self.bus.publish(**event_kwargs("r1", 2))
        received = subscription.get(timeout=1)
        self.assertEqual(received.seq, second.seq)
        self.assertEqual([item.seq for item in self.bus.replay("r1", after_seq=1)], [2])

    def test_new_bus_replays_from_disk(self):
        self.bus.publish(**event_kwargs("r1", 1))
        self.bus.publish(**event_kwargs("r1", 2))
        other = EventBus(self.root)
        self.assertEqual([item.seq for item in other.replay("r1")], [1, 2])

    def test_run_sequences_are_independent(self):
        self.assertEqual(self.bus.publish(**event_kwargs("a", 1)).seq, 1)
        self.assertEqual(self.bus.publish(**event_kwargs("b", 1)).seq, 1)
        self.assertEqual(self.bus.publish(**event_kwargs("a", 2)).seq, 2)
        self.assertEqual(self.bus.publish(**event_kwargs("b", 2)).seq, 2)

    def test_concurrent_publish_has_unique_contiguous_sequence(self):
        def publish(number):
            return self.bus.publish(**event_kwargs("r1", number))

        with ThreadPoolExecutor(max_workers=8) as pool:
            events = list(pool.map(publish, range(1, 51)))
        seqs = sorted(event.seq for event in events)
        self.assertEqual(seqs, list(range(1, 51)))
        self.assertEqual(len(set(seqs)), 50)

    def test_disk_sequence_regression_is_rejected(self):
        path = self.root / "bad" / "events.jsonl"
        path.parent.mkdir(parents=True)
        first = RunEvent.create(seq=2, timestamp="2026-10-11T20:00:00+08:00", **event_kwargs("bad", 2))
        second = RunEvent.create(seq=1, timestamp="2026-10-11T20:00:01+08:00", **event_kwargs("bad", 1))
        path.write_text(first.to_json() + "\n" + second.to_json() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "事件序号不递增"):
            EventBus(self.root).replay("bad")

    def test_subscription_close_and_timeout(self):
        subscription = self.bus.subscribe("r1")
        with self.assertRaises(queue.Empty):
            subscription.get(timeout=0.01)
        subscription.close()
        self.assertTrue(subscription.closed)


class RunContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime_context_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = RunManager(self.root)

    def tearDown(self):
        self.manager.close()

    def test_output_is_isolated_per_run(self):
        first = self.manager.create_run("s1", "first", {})
        second = self.manager.create_run("s1", "second", {})
        first.output("answer.txt").write_text("first", encoding="utf-8")
        second.output("answer.txt").write_text("second", encoding="utf-8")
        self.assertNotEqual(first.folder, second.folder)
        self.assertEqual(first.output("answer.txt").read_text(encoding="utf-8"), "first")
        self.assertEqual(second.output("answer.txt").read_text(encoding="utf-8"), "second")

    def test_output_rejects_nested_paths(self):
        run = self.manager.create_run("s1", "paths", {})
        for name in ("../escape.txt", "pages/image.png", "pages\\image.png", "C:escape", ".."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                run.output(name)

    def test_read_emits_file_read_event(self):
        run = self.manager.create_run("s1", "read", {})
        path = run.output("input.txt")
        path.write_text("hello", encoding="utf-8")
        self.assertEqual(run.read(path), b"hello")
        event = self.manager.bus.replay(run.id)[-1]
        self.assertEqual(event.event_type, "file_read")
        self.assertEqual(event.payload["size_bytes"], 5)

    def test_cancelled_is_terminal_and_finish_cannot_override(self):
        run = self.manager.create_run("s1", "cancel", {})
        run.start()
        self.assertTrue(run.cancel("user"))
        self.assertEqual(run.status, RunStatus.CANCELLED)
        self.assertFalse(run.finish("succeeded"))
        self.assertEqual(run.status, RunStatus.CANCELLED)

    def test_start_after_terminal_raises(self):
        run = self.manager.create_run("s1", "terminal", {})
        run.start()
        run.finish("succeeded")
        with self.assertRaises(RunStateError):
            run.start()


class RunManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime_manager_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = RunManager(self.root)

    def tearDown(self):
        self.manager.close()

    def test_session_isolation_and_listing(self):
        first = self.manager.create_run("session-a", "a", {})
        second = self.manager.create_run("session-b", "b", {})
        self.assertEqual([run.id for run in self.manager.list_runs("session-a")], [first.id])
        self.assertEqual([run.id for run in self.manager.list_runs("session-b")], [second.id])
        with self.assertRaises(RunNotVisible):
            self.manager.get_run(first.id, session_id="session-b")

    def test_submit_success_records_terminal_state(self):
        def target(run):
            run.event("calculation", formula="1+1", result=2)
            return "ok"

        run = self.manager.create_run("s1", "success", {})
        self.manager.start(run.id, target)
        completed = self.manager.wait(run.id, timeout=2)
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        self.assertEqual(completed.snapshot()["result"], "ok")
        self.assertEqual(self.manager.bus.replay(run.id)[-1].event_type, "run_finished")

    def test_worker_failure_records_failed_state(self):
        def target(_run):
            raise ValueError("boom")

        run = self.manager.create_run("s1", "failure", {})
        self.manager.start(run.id, target)
        completed = self.manager.wait(run.id, timeout=2)
        snapshot = completed.snapshot()
        self.assertEqual(completed.status, RunStatus.FAILED)
        self.assertEqual(snapshot["error_type"], "ValueError")
        self.assertIn("boom", snapshot["error"])

    def test_cooperative_cancel_stops_run_and_preserves_cancelled(self):
        started = threading.Event()

        def target(run):
            started.set()
            run.cancelled_event.wait(2)
            run.raise_if_cancelled()

        run = self.manager.create_run("s1", "cancel", {})
        self.manager.start(run.id, target)
        self.assertTrue(started.wait(1))
        self.assertTrue(self.manager.cancel(run.id, session_id="s1", reason="user"))
        completed = self.manager.wait(run.id, timeout=2)
        self.assertEqual(completed.status, RunStatus.CANCELLED)
        self.assertEqual(completed.snapshot()["cancel_reason"], "user")

    def test_timeout_cancels_cooperative_run(self):
        started = threading.Event()

        def target(run):
            started.set()
            run.cancelled_event.wait(2)
            run.raise_if_cancelled()

        run = self.manager.create_run("s1", "timeout", {})
        self.manager.start(run.id, target, timeout=0.05)
        self.assertTrue(started.wait(1))
        completed = self.manager.wait(run.id, timeout=2)
        self.assertEqual(completed.status, RunStatus.CANCELLED)
        self.assertEqual(completed.snapshot()["cancel_reason"], "timeout")

    def test_non_json_result_is_summarized_without_losing_terminal_event(self):
        run = self.manager.create_run("s1", "non-json", {})
        self.manager.start(run.id, lambda _run: object())
        completed = self.manager.wait(run.id, timeout=2)
        self.assertEqual(completed.status, RunStatus.SUCCEEDED)
        final_event = self.manager.bus.replay(run.id)[-1]
        self.assertEqual(final_event.event_type, "run_finished")
        self.assertEqual(final_event.payload["result"]["type"], "object")

    def test_session_required_and_unknown_run_is_not_visible(self):
        with self.assertRaises(ValueError):
            self.manager.create_run("", "bad", {})
        with self.assertRaises(RunNotVisible):
            self.manager.get_run("missing", session_id="s1")


class LegacyRunAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime_legacy_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = RunManager(self.root)

    def tearDown(self):
        self.manager.close()

    def test_adapter_mirrors_legacy_events_and_can_detach(self):
        legacy = LegacyRun(self.root, "legacy", {})
        adapter = LegacyRunAdapter(legacy, self.manager, session_id="s1")
        legacy.event("calculation", formula="1+1", result=2)
        mirrored = [event for event in self.manager.bus.replay(legacy.id)
                    if event.event_type == "calculation"]
        self.assertEqual(len(mirrored), 1)
        self.assertGreaterEqual(mirrored[0].seq, 1)
        adapter.detach()
        legacy.event("calculation", formula="2+2", result=4)
        mirrored = [event for event in self.manager.bus.replay(legacy.id)
                    if event.event_type == "calculation"]
        self.assertEqual(len(mirrored), 1)

    def test_without_adapter_legacy_run_does_not_create_runtime_mirror(self):
        legacy = LegacyRun(self.root, "legacy-only", {})
        legacy.event("calculation", formula="1+1", result=2)
        self.assertFalse((self.root / "results" / "runs").exists())


if __name__ == "__main__":
    unittest.main()
