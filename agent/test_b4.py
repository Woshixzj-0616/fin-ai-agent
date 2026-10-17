"""B4 实时执行轨迹、结果工作区与证据定位测试。"""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import pymupdf

try:
    from api.routes import ApiApplication, ApiError
    from api.runtime_bridge import RuntimeRunBridge
    from runtime import RunManager
except ModuleNotFoundError:
    from agent.api.routes import ApiApplication, ApiError
    from agent.api.runtime_bridge import RuntimeRunBridge
    from agent.runtime import RunManager


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def wait_for(condition, description: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"等待超时：{description}")


def tiny_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595.276, height=841.89)
    page.insert_text((72, 72), "600519 2024 年年度报告", fontsize=12)
    blob = doc.tobytes()
    doc.close()
    return blob


def fake_workflow(context, command, parameters, pdf, comparison_pdfs):
    source = context.output("source.pdf")
    source.write_bytes(pdf)
    image = context.output("600519_2024_abcd1234_p1.png")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        doc[0].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(image)

    fact = {
        "evidence_id": "ev_b4_revenue",
        "company_code": "600519",
        "company_name": "贵州茅台",
        "metric": "revenue",
        "metric_name": "营业收入",
        "period_year": 2024,
        "value": "80000000",
        "unit": "元",
        "page": 1,
        "page_image": image.name,
        "value_bbox": [10, 20, 40, 50],
        "label_bbox": [5, 20, 9, 50],
        "source_file": str(source.relative_to(context.root)).replace("\\", "/"),
        "source_sha256": "a" * 64,
        "issues": [],
        "scope": "consolidated",
        "adjustment": "as_reported",
    }
    context.event(
        "evidence.found",
        actor="parser",
        title="找到营业收入证据",
        summary="第 1 页命中",
        evidence_ids=[fact["evidence_id"]],
        payload={"metric": "revenue", "page": 1},
    )
    context.output("webui_evidence.json").write_text(
        json.dumps([fact], ensure_ascii=False), encoding="utf-8"
    )
    context.output("webui_document_texts.json").write_text(
        json.dumps({"600519_2024": ["2024 年营业收入为 80000000 元。"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "counts": {"match": 1, "mismatch": 0, "unverified": 0, "issue": 0},
        "evidence": [fact],
        "rows": [
            {
                "metric": "revenue",
                "metric_name": "营业收入",
                "current": "80000000",
                "previous": "70000000",
                "page": 1,
                "page_image": image.name,
                "value_bbox": [10, 20, 40, 50],
            }
        ],
        "issues": [],
        "failures": [],
        "report_md": "# B4 测试报告\n",
    }


class RuntimeEventPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b4_events_")
        self.addCleanup(self.temp.cleanup)
        self.manager = RunManager(Path(self.temp.name))
        self.addCleanup(self.manager.close)

    def test_runtime_bridge_keeeps_structured_plan_without_raw_model_payload(self):
        context = self.manager.create_run("s" * 32, "financial", {})
        context.start()
        bridge = RuntimeRunBridge(context)
        bridge.event(
            "model_parse",
            sentences=["完整草稿原句"],
            parsed={
                "items": [
                    {
                        "claim_id": "C1",
                        "claim_type": "amount",
                        "metric": "revenue",
                        "period_year": 2024,
                        "plain_claim": "营业收入为 8000 万元",
                        "quote": "完整草稿原句",
                        "verification_action": "compare_amount",
                    }
                ],
                "unclaimed_sentences": [2],
            },
        )
        bridge.event(
            "agent_loop_round",
            round=1,
            action="no_tool_call",
            content_preview="供应商原始自由文本不应进入事件",
        )

        events = self.manager.bus.replay(context.id)
        plan = next(event for event in events if event.event_type == "model_parse")
        loop = next(event for event in events if event.event_type == "agent_loop_round")

        self.assertEqual(plan.title, "模型计划摘要")
        self.assertEqual(plan.payload["claim_count"], 1)
        self.assertEqual(plan.payload["unclaimed_count"], 1)
        self.assertEqual(plan.payload["plan"][0]["claim_id"], "C1")
        self.assertNotIn("sentences", plan.payload)
        self.assertNotIn("parsed", plan.payload)
        self.assertNotIn("quote", json.dumps(plan.payload, ensure_ascii=False))
        self.assertNotIn("content_preview", loop.payload)
        self.assertNotIn("供应商原始自由文本", json.dumps(loop.payload, ensure_ascii=False))


class ResultEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b4_result_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = RunManager(self.root)
        self.app = ApiApplication(
            self.root,
            manager=self.manager,
            workflow_runner=fake_workflow,
        )
        self.addCleanup(self.app.close)
        self.session = "a" * 32

    def create_completed_run(self):
        created = self.app.create_run(
            self.session,
            "financial",
            {"code": "600519", "name": "贵州茅台", "year": 2024},
            tiny_pdf(),
            [],
        )
        wait_for(
            lambda: self.app.get_run(self.session, created["run_id"])["status"] == "succeeded",
            "B4 测试运行完成",
        )
        return created["run_id"]

    def test_result_contains_scoped_image_urls_and_real_page_dimensions(self):
        run_id = self.create_completed_run()
        payload = self.app.get_run(self.session, run_id, include_result=True)
        evidence = payload["result"]["evidence"][0]
        row = payload["result"]["rows"][0]

        expected_url = f"/api/runs/{run_id}/images/600519_2024_abcd1234_p1.png"
        self.assertEqual(evidence["page_image_url"], expected_url)
        self.assertEqual(row["page_image_url"], expected_url)
        self.assertAlmostEqual(evidence["page_width"], 595.276, places=3)
        self.assertAlmostEqual(evidence["page_height"], 841.89, places=2)
        self.assertEqual(evidence["image_scale"], 1.6)

        image_path = self.app.get_evidence_image(
            self.session,
            run_id,
            "600519_2024_abcd1234_p1.png",
        )
        self.assertTrue(image_path.is_file())
        with self.assertRaises(ApiError) as caught:
            self.app.get_evidence_image(
                "b" * 32,
                run_id,
                "600519_2024_abcd1234_p1.png",
            )
        self.assertEqual(caught.exception.code, "RUN_NOT_FOUND")


class EvidenceImageHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer

        from webui import Handler

        cls.temp = tempfile.TemporaryDirectory(prefix="b4_http_")
        cls.root = Path(cls.temp.name).resolve()
        cls.manager = RunManager(cls.root)
        cls.app = ApiApplication(
            cls.root,
            manager=cls.manager,
            workflow_runner=fake_workflow,
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.fin_api = cls.app
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.app.close()
        cls.temp.cleanup()

    def request(self, path, cookie):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
        conn.request("GET", path, headers={"Cookie": cookie})
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        headers = dict(response.getheaders())
        conn.close()
        return status, headers, payload

    def test_image_endpoint_is_run_and_session_scoped(self):
        session = "c" * 32
        created = self.app.create_run(
            session,
            "financial",
            {"code": "600519", "name": "贵州茅台", "year": 2024},
            tiny_pdf(),
            [],
        )
        run_id = created["run_id"]
        wait_for(
            lambda: self.app.get_run(session, run_id)["status"] == "succeeded",
            "页图 HTTP 测试运行完成",
        )
        path = f"/api/runs/{run_id}/images/600519_2024_abcd1234_p1.png"
        status, headers, payload = self.request(path, f"fin_session={session}")
        self.assertEqual(status, 200, payload)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertTrue(payload.startswith(b"\x89PNG"))

        status, _, _ = self.request(path, "fin_session=" + "d" * 32)
        self.assertEqual(status, 404)
        status, _, _ = self.request(
            f"/api/runs/{run_id}/images/..%2frun.json",
            f"fin_session={session}",
        )
        self.assertEqual(status, 404)


class FrontendB4SourceTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (WEB / relative).read_text(encoding="utf-8")

    def test_transport_clients_have_single_responsibility(self):
        api_source = self.read("js/api.js")
        stream_source = self.read("js/stream.js")
        main_source = self.read("js/main.js")

        self.assertIn("export function createApiClient", api_source)
        self.assertIn("fetch(", api_source)
        self.assertIn("export function createRunEventStream", stream_source)
        self.assertIn("EventSource", stream_source)
        self.assertIn("last-event-id", stream_source)
        self.assertIn("createApiClient", main_source)
        self.assertIn("createRunEventStream", main_source)
        self.assertNotIn("fetch(", main_source)
        self.assertNotIn("EventSource", main_source)
        self.assertNotIn("setInterval", main_source)

    def test_stream_uses_recovery_cursor_in_request(self):
        module_uri = (WEB / "js" / "stream.js").resolve().as_uri()
        script = f"""
        const urls = [];
        globalThis.EventSource = class {{
          static CLOSED = 2;
          constructor(url) {{ this.url = String(url); urls.push(this.url); }}
          close() {{}}
        }};
        const module = await import({json.dumps(module_uri)});
        const stream = module.createRunEventStream();
        stream.connect("run-1", {{ afterSeq: 12 }});
        console.log(JSON.stringify({{ urls, runId: stream.getRunId() }}));
        """
        import subprocess

        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["runId"], "run-1")
        self.assertEqual(
            payload["urls"],
            ["/api/runs/run-1/events?after_seq=12"],
        )

    def test_state_contract_includes_b4_interactions(self):
        module_uri = (WEB / "js" / "run-state.js").resolve().as_uri()
        script = (
            f"import({json.dumps(module_uri)}).then((module) => "
            "console.log(JSON.stringify(module.createRunStore().getState())))"
        )
        import subprocess

        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state["events"], [])
        self.assertIsNone(state["result"])
        self.assertEqual(state["connection"], "idle")
        self.assertEqual(state["askHistory"], [])
        self.assertEqual(state["searchResults"], [])

    def test_views_do_not_show_raw_model_fields(self):
        for relative in (
            "js/views/task.js",
            "js/views/trace.js",
            "js/views/results.js",
            "js/views/ask.js",
        ):
            with self.subTest(relative=relative):
                source = self.read(relative)
                for forbidden in (
                    "content_preview",
                    "chain_of_thought",
                    "system_prompt",
                    "raw_response",
                    "reasoning",
                ):
                    self.assertNotIn(forbidden, source)

    def test_b4_dom_contract_exists(self):
        html = self.read("index.html")
        self.assertIn('class="sidebar-status-label"', html)
        for node_id in (
            "cancel-run",
            "task-command",
            "comparison-pdfs",
            "draft-text",
            "ocr-toggle",
            "evidence-dialog",
            "evidence-image",
            "evidence-boxes",
            "ask-form",
            "ask-input",
            "search-form",
            "search-input",
        ):
            self.assertIn(f'id="{node_id}"', html)


if __name__ == "__main__":
    unittest.main()
