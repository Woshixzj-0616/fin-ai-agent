import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import main as cli_main

import onsite_demo
from llm_http import LLMHttp, TransportError
from materials import validate_pdf
from runtime.run_manager import RunManager, RunStatus


ROOT = Path(__file__).resolve().parents[1]


class OnsiteBundleTests(unittest.TestCase):
    def test_bundle_contains_four_required_claim_outcomes(self):
        report = onsite_demo.inspect_bundle(ROOT)

        self.assertTrue(report["ready"], report["problems"])
        self.assertEqual(report["claim_count"], 8)
        self.assertEqual(
            report["claim_outcomes"],
            {
                "missing_evidence": 1,
                "scope_conflict": 1,
                "supported": 4,
                "wrong_value": 2,
            },
        )
        self.assertGreaterEqual(report["material_count"], 5)

    def test_failure_playbook_covers_required_offline_drills(self):
        drills = onsite_demo.failure_playbook()

        self.assertEqual(
            [drill["id"] for drill in drills],
            ["network_failure", "model_timeout", "material_format_error"],
        )
        self.assertTrue(all(drill["offline_only"] for drill in drills))
        self.assertTrue(all(drill["visible_signal"] for drill in drills))

    def test_network_failure_has_an_explicit_local_error(self):
        transport = LLMHttp(
            "https://127.0.0.1:1/v1/chat/completions",
            "offline-drill",
            timeout=0.2,
            max_retries=0,
            request_deadline=1,
        )
        try:
            with self.assertRaisesRegex(TransportError, "连接失败或超时|总时限已用尽"):
                transport.post_json({"model": "offline-drill", "messages": []})
        finally:
            transport.close()

    def test_model_timeout_keeps_checkpoint_without_final_result(self):
        with tempfile.TemporaryDirectory(prefix="onsite_timeout_") as directory:
            manager = RunManager(Path(directory), shutdown_timeout=0.2)
            started = threading.Event()

            def slow_target(run):
                run.output("checkpoint.json").write_text(
                    '{"completed_chunks": 1}', encoding="utf-8")
                started.set()
                run.cancelled_event.wait(2)
                run.raise_if_cancelled()

            try:
                run = manager.create_run("onsite-drill", "model_timeout", {})
                manager.start(run.id, slow_target, timeout=0.05)
                self.assertTrue(started.wait(1))
                completed = manager.wait(run.id, timeout=2)
                snapshot = completed.snapshot()

                self.assertEqual(completed.status, RunStatus.CANCELLED)
                self.assertEqual(snapshot["cancel_reason"], "timeout")
                self.assertTrue((run.folder / "checkpoint.json").is_file())
                self.assertFalse((run.folder / "result.json").exists())
                self.assertIn("\"reason\": \"timeout\"", run.events_path.read_text(encoding="utf-8"))
            finally:
                manager.close()

    def test_material_format_error_is_rejected_not_wrapped_as_success(self):
        with self.assertRaisesRegex(ValueError, "不是PDF文件"):
            validate_pdf(b"<html>Access denied</html>")

    def test_cli_demo_uses_isolated_run_root(self):
        command = onsite_demo.cli_demo_command(
            sys.executable,
            ROOT,
            ROOT / "work" / "p4-onsite-cli",
        )

        self.assertEqual(command[:3], [sys.executable, "-B", "agent/main.py"])
        run_root_index = command.index("--run-root")
        self.assertEqual(command[run_root_index + 1], str(ROOT / "work" / "p4-onsite-cli"))
        self.assertLess(run_root_index, command.index("demo"))
        self.assertIn("--render", command)

    def test_cli_launcher_runs_demo_without_starting_web(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        result = onsite_demo.run_launcher(
            mode="cli",
            host="127.0.0.1",
            port=8765,
            root=ROOT,
            python=sys.executable,
            run_root=ROOT / "work" / "p4-onsite-cli",
            runner=fake_runner,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("demo", calls[0][0])
        self.assertEqual(calls[0][1]["cwd"], ROOT)

    def test_cli_parser_accepts_run_root_before_command(self):
        args = cli_main.parser().parse_args([
            "--run-root",
            str(ROOT / "work" / "isolated"),
            "demo",
            "--render",
        ])

        self.assertEqual(args.run_root, ROOT / "work" / "isolated")
        self.assertEqual(args.command, "demo")
        self.assertTrue(args.render)


if __name__ == "__main__":
    unittest.main()
