"""比赛交付回归：完整产物、可靠结束、输入边界和缓存来源核验。"""
from __future__ import annotations

import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pymupdf

import extract_cache
import webui_http
from api.routes import ApiApplication, ApiError
from api.runtime_bridge import RuntimeRunBridge
from api.artifacts import build_explanation, capture_sources
from materials import sha256
from runtime import RunManager
from test_b4 import fake_workflow, tiny_pdf


def large_result():
    return {
        "ok": True,
        "counts": {"match": 257},
        "evidence": [{"evidence_id": f"E{i}", "value": str(i)} for i in range(257)],
        "rows": [{"metric": f"M{i}"} for i in range(125)],
        "checks": {"checks": [{"claim_id": f"C{i}"} for i in range(135)]},
        "report_md": "# 完整报告\n" + "营业收入须以原文证据核验。\n" * 400,
        "raw_response": "不应公开的模型响应",
        "note": "Authorization: Bearer " + "sk-" + "x" * 24,
    }


class ResultIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = RunManager(self.root)
        self.addCleanup(self.manager.close)
        self.session = "a" * 32

    def completed(self, payload):
        run = self.manager.create_run(self.session, "financial", {})
        self.manager.start(run.id, lambda context: payload)
        self.manager.wait(run.id, timeout=3)
        return run

    def test_result_and_persisted_copy_keep_all_records_and_report_text(self):
        expected = large_result()
        run = self.completed(expected)
        app = ApiApplication(self.root, manager=self.manager)
        actual = app.get_run(self.session, run.id, include_result=True)["result"]
        saved = json.loads(run.output("result.json").read_text())
        for result in (actual, saved):
            with self.subTest(storage=result is saved):
                self.assertEqual(len(result["evidence"]), 257)
                self.assertEqual(len(result["rows"]), 125)
                self.assertEqual(len(result["checks"]["checks"]), 135)
                self.assertEqual(result["report_md"], expected["report_md"])
                self.assertNotIn("raw_response", result)
                self.assertNotIn("sk-" + "x" * 24, json.dumps(result))

    def test_list_summary_counts_actual_evidence(self):
        run = self.completed(large_result())
        app = ApiApplication(self.root, manager=self.manager)
        summary = app.get_run(self.session, run.id)
        self.assertEqual(summary["result_summary"]["evidence_count"], 257)
        self.assertNotIn("result", summary)

    def test_long_draft_survives_restart_and_summary_counts_full_input(self):
        draft = "待核查营业收入。" * 800
        run = self.manager.create_run(self.session, "financial", {"draft":draft})
        run.finish("succeeded", result=large_result())
        restored = RunManager(self.root)
        try:
            reopened = restored.get_run(run.id, session_id=self.session)
            self.assertEqual(reopened.parameters["draft"], draft)
            app = ApiApplication(self.root, manager=restored)
            self.assertEqual(app.get_run(self.session, run.id)["input_summary"]["draft_length"], len(draft))
        finally:
            restored.close()

    def test_http_response_does_not_truncate_results_again(self):
        run = self.completed(large_result())
        app = ApiApplication(self.root, manager=self.manager)
        server = ThreadingHTTPServer(("127.0.0.1", 0), webui_http.Handler)
        server.fin_api = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection(*server.server_address, timeout=3)
            conn.request("GET", f"/api/runs/{run.id}?include_result=1",
                         headers={"Cookie": f"fin_session={self.session}"})
            response = conn.getresponse()
            actual = json.loads(response.read())
            conn.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(len(actual["result"]["evidence"]), 257)
            self.assertEqual(actual["result"]["report_md"], large_result()["report_md"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_pipeline_finish_waits_for_actual_result(self):
        for status, expected_status in (("ok", "succeeded"), ("partial_failure", "partial_failed")):
            with self.subTest(status=status):
                prepared, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)

                def workflow(context):
                    bridge = RuntimeRunBridge(context)
                    bridge.finish(status=status, evidence_count=257)
                    prepared.set()
                    release.wait(3)
                    return large_result()

                run = self.manager.create_run(self.session, "financial", {})
                self.manager.start(run.id, workflow)
                try:
                    self.assertTrue(prepared.wait(2))
                    self.assertEqual(run.status.value, "running")
                    self.assertFalse(any(e.event_type == "run_finished"
                                         for e in self.manager.bus.replay(run.id)))
                finally:
                    release.set()
                self.manager.wait(run.id, timeout=3)
                self.assertEqual(run.status.value, expected_status)
                self.assertEqual(len(run.snapshot()["result"]["evidence"]), 257)
                self.assertEqual(self.manager.bus.replay(run.id)[-1].status, expected_status)

    def test_bridge_records_actor_identity_separately_from_summary(self):
        run = self.manager.create_run(self.session, "financial", {})
        bridge = RuntimeRunBridge(run)
        bridge.event("calculation", operation="yoy", result="9.10")
        event = self.manager.bus.replay(run.id)[-1]
        self.assertEqual(event.actor, "program")
        self.assertEqual(event.phase, "calculation")
        bridge.finish(status="ok")
        event = self.manager.bus.replay(run.id)[-1]
        self.assertEqual(event.actor, "runtime")
        self.assertEqual(event.phase, "report")

    def test_complete_explanation_keeps_model_plan_tool_inputs_and_results(self):
        run = self.manager.create_run(self.session, "financial", {})
        bridge = RuntimeRunBridge(run)
        plan = [{"claim_id": f"C{i}", "metric": "revenue", "period_year": 2024,
                 "value": str(i), "unit": "元", "quote": "原始草稿句子"} for i in range(135)]
        bridge.event("model_parse", parsed={"items": plan}, sentences=[{"sentence_id":1,"text":"原始草稿句子"}])
        bridge.event("agent_tool", tool="find_evidence", arguments={"metric":"revenue"},
                     result={"items":[{"id":i} for i in range(140)], "reasoning_content":"不可显示的思维字段"})
        bridge.event("calculation", metric_name="营业收入", comparison_label="同比",
                     change={"status":"ok", "value":"9.10324", "unit":"%", "formula":"(current-previous)/previous*100", "operands":{"current":"89389354416.84","previous":"81930977667.75"}},
                     reported_change_check={"status":"match","calculated_rounded":"9.10","reported":"9.10","unit":"%"})
        run.finish("succeeded", result=large_result())
        explanation = build_explanation(run, self.manager.bus.replay(run.id))
        planned = next(step for step in explanation["steps"] if step["event_type"] == "model_parse")
        called = next(step for step in explanation["steps"] if step["event_type"] == "agent_tool")
        self.assertEqual(len(planned["payload"]["plan"]), 135)
        self.assertEqual(planned["payload"]["plan"][-1]["value"], "134")
        self.assertEqual(len(called["payload"]["result"]["items"]), 140)
        self.assertEqual(called["payload"]["arguments"], {"metric":"revenue"})
        self.assertNotIn("reasoning_content", json.dumps(explanation))
        calculated = next(step for step in explanation["steps"] if step["event_type"] == "calculation")
        self.assertIn("披露 9.10% 核对，一致", calculated["explanation"])
        app = ApiApplication(self.root, manager=self.manager)
        _, _, blob = app.export_run(self.session, run.id, "explanation.json")
        self.assertEqual(json.loads(blob), explanation)

    def test_dimensions_are_computed_once_per_unique_image(self):
        app = ApiApplication(self.root, manager=self.manager, workflow_runner=fake_workflow)
        run = self.manager.create_run(self.session, "financial", {})
        payload = fake_workflow(run, "financial", {}, tiny_pdf(), [])
        payload["evidence"] = [dict(payload["evidence"][0], evidence_id=f"E{i}") for i in range(40)]
        run.finish("succeeded", result=payload)
        original = pymupdf.open
        with patch("pymupdf.open", wraps=original) as opened:
            result = app.get_run(self.session, run.id, include_result=True)["result"]
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(len(result["evidence"]), 40)
        self.assertAlmostEqual(result["evidence"][-1]["page_width"], 595.276, places=3)

    def test_reproduction_bundle_contains_full_outputs_and_verifiable_source_without_session(self):
        source = self.root / "source"
        (source / "agent").mkdir(parents=True)
        (source / "agent" / "main.py").write_text("print('source at execution')\n")
        (source / "requirements.txt").write_text("httpx==0.28.1\n")
        (source / ".env").write_text("PRIVATE_KEY=never-export-this\n")
        external = self.root / "external.txt"
        external.write_text("external-secret")
        (source / "agent" / "escape.py").symlink_to(external)
        run = self.completed(large_result())
        provenance = capture_sources(run, source_root=source)
        (run.folder / "escape.txt").symlink_to(external)
        run.output("input.pdf").write_bytes(tiny_pdf())
        app = ApiApplication(self.root, manager=self.manager)
        filename, mime, blob = app.export_run(self.session, run.id, "reproduction.zip")
        self.assertTrue(filename.endswith("_reproduction.zip"))
        self.assertEqual(mime, "application/zip")
        with ZipFile(io.BytesIO(blob)) as archive:
            saved = json.loads(archive.read("run/result.json"))
            self.assertEqual(len(saved["evidence"]), 257)
            self.assertEqual(archive.read("run/report.md").decode(), large_result()["report_md"])
            self.assertEqual(len(archive.read("run/events.jsonl").splitlines()), len(run.events))
            state = json.loads(archive.read("run/run.json"))
            self.assertNotIn("session_id", state)
            self.assertNotIn("run/escape.txt", archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            for name, expected in manifest["sha256"].items():
                self.assertEqual(sha256(archive.read(name)), expected)
            with ZipFile(io.BytesIO(archive.read("source_code.zip"))) as sources:
                self.assertNotIn(".env", sources.namelist())
                self.assertNotIn("agent/escape.py", sources.namelist())
                for name, expected in provenance["code_sha256"].items():
                    self.assertEqual(sha256(sources.read(name)), expected)

    def test_exports_respect_session_and_do_not_publish_running_results(self):
        app = ApiApplication(self.root, manager=self.manager)
        run = self.completed(large_result())
        with self.assertRaises(ApiError) as caught:
            app.export_run("b" * 32, run.id, "result.json")
        self.assertEqual(caught.exception.status, 404)
        pending = self.manager.create_run(self.session, "financial", {})
        with self.assertRaises(ApiError) as caught:
            app.export_run(self.session, pending.id, "reproduction.zip")
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(ApiError) as caught:
            app.export_run(self.session, run.id, "../run.json")
        self.assertEqual(caught.exception.status, 404)


class InputBoundaryTests(unittest.TestCase):
    def test_unsupported_event_type_is_rejected_before_queueing(self):
        with tempfile.TemporaryDirectory() as directory:
            app = ApiApplication(Path(directory))
            try:
                with self.assertRaises(ApiError) as caught:
                    app.create_run("a" * 32, "announcement", {"event_type": "dividend"}, tiny_pdf())
                self.assertEqual(caught.exception.status, 400)
                self.assertEqual(app.manager.list_runs("a" * 32), [])
            finally:
                app.close()

    def test_known_model_credential_is_rejected_before_state_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = ApiApplication(root)
            secret = "environment-credential-for-test"
            try:
                with patch.dict(os.environ, {"LLM_API_KEY": secret}):
                    with self.assertRaises(ApiError):
                        app.create_run("a" * 32, "financial", {"draft": "误粘贴 " + secret}, tiny_pdf())
                self.assertFalse(list(root.rglob("run.json")))
            finally:
                app.close()

    @staticmethod
    def handler(body, declared_length=None):
        return Mock(headers={"Content-Type": "multipart/form-data; boundary=quality-boundary",
                             "Content-Length": str(len(body) if declared_length is None else declared_length)},
                    rfile=io.BytesIO(body))

    def test_new_upload_limit_can_exceed_legacy_single_file_limit(self):
        pdf = b"%PDF-1.7\n" + b"x" * 1024
        body = (b'--quality-boundary\r\nContent-Disposition: form-data; name="pdf"; filename="a.pdf"'
                b'\r\nContent-Type: application/pdf\r\n\r\n' + pdf + b'\r\n--quality-boundary--\r\n')
        with patch.object(webui_http, "MAX_UPLOAD", 512):
            with self.assertRaises(ValueError):
                webui_http._parse_multipart(self.handler(body))
            _, actual = webui_http._parse_multipart(self.handler(body), max_bytes=2048)
        self.assertEqual(actual, pdf)

    def test_incomplete_upload_is_explicitly_rejected(self):
        body = (b'--quality-boundary\r\nContent-Disposition: form-data; name="pdf"; filename="a.pdf"'
                b'\r\n\r\n%PDF-1.7\r\n--quality-boundary--\r\n')
        with self.assertRaises(ValueError):
            webui_http._parse_multipart(self.handler(body, len(body) + 10))


class CacheIntegrityTests(unittest.TestCase):
    def test_valid_json_with_invalid_cache_shape_is_a_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = {"sha256": "a" * 64, "document_id": "D"}
            path = extract_cache.cache_path(root, material)
            path.parent.mkdir(parents=True)
            for invalid in ([], "invalid", None):
                with self.subTest(value=invalid):
                    path.write_text(json.dumps(invalid))
                    self.assertIsNone(extract_cache.load_cache(root, material))

    def test_cache_with_invalid_facts_is_a_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = {"sha256": "a" * 64, "document_id": "D"}
            path = extract_cache.cache_path(root, material)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"cache_key": extract_cache.cache_key(material), "facts": [None]}))
            self.assertIsNone(extract_cache.load_cache(root, material))

    def test_cache_cannot_hide_modified_or_missing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.pdf"
            original = b"%PDF-1.7 original"
            path.write_bytes(original)
            material = {"sha256": sha256(original), "document_id": "D", "local_file": path.name}
            extract_cache.save_cache(root, material, [{"evidence_id": "E", "value": "1"}])
            path.write_bytes(b"%PDF-1.7 modified")
            with self.assertRaisesRegex(ValueError, "指纹"):
                extract_cache.extract_material_cached(root, material, Mock())
            path.unlink()
            with self.assertRaises((OSError, ValueError)):
                extract_cache.extract_material_cached(root, material, Mock())


if __name__ == "__main__":
    unittest.main()
