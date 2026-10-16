"""B2 Web API、SSE、会话与安全边界测试。"""
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
    from api.security import (
        InvalidUpload,
        new_session_id,
        resolve_client_ip,
        session_cookie_header,
        validate_pdf_upload,
    )
    from api.sse import encode_sse, heartbeat_frame, parse_last_event_id
    from runtime import RunManager
except ModuleNotFoundError:
    from agent.api.routes import ApiApplication, ApiError
    from agent.api.runtime_bridge import RuntimeRunBridge
    from agent.api.security import (
        InvalidUpload,
        new_session_id,
        resolve_client_ip,
        session_cookie_header,
        validate_pdf_upload,
    )
    from agent.api.sse import encode_sse, heartbeat_frame, parse_last_event_id
    from agent.runtime import RunManager


def wait_for(condition, description: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"等待超时：{description}")


def tiny_pdf(text: str = "金融投研智能体测试 600519 2024年年度报告") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    blob = doc.tobytes()
    doc.close()
    return blob


def sample_fact() -> dict:
    return {
        "evidence_id": "ev_revenue_2024",
        "company_code": "600519",
        "company_name": "贵州茅台",
        "metric": "revenue",
        "metric_name": "营业收入",
        "period_year": 2024,
        "report_year": 2024,
        "value": "80000000",
        "normalized_value": "80000000",
        "unit": "元",
        "scope": "consolidated",
        "adjustment": "as_reported",
        "comparison_group": "same-report-table",
        "issues": [],
        "page": 5,
    }


def fake_workflow(context, command, parameters, pdf, comparison_pdfs):
    context.event(
        "calculation",
        actor="program",
        title="测试计算",
        summary="用本地规则完成 1+1",
        payload={"formula": "1+1", "result": 2},
    )
    fact = sample_fact()
    context.output("webui_evidence.json").write_text(
        json.dumps([fact], ensure_ascii=False), encoding="utf-8"
    )
    context.output("webui_document_texts.json").write_text(
        json.dumps({"600519_2024": ["2024年营业收入为80000000元，第5页。"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "counts": {"match": 1, "mismatch": 0, "unverified": 0, "issue": 0},
        "evidence": [fact],
        "failures": [],
        "report_md": "# 测试报告\n",
    }


class SecurityTests(unittest.TestCase):
    def test_forwarded_for_uses_rightmost_trusted_hop(self):
        self.assertEqual(
            resolve_client_ip(
                "10.0.0.8",
                "1.2.3.4, 203.0.113.9",
                trusted_hops=1,
            ),
            "203.0.113.9",
        )
        self.assertEqual(
            resolve_client_ip(
                "10.0.0.8",
                "1.2.3.4, 198.51.100.7, 203.0.113.9",
                trusted_hops=2,
            ),
            "198.51.100.7",
        )
        self.assertEqual(resolve_client_ip("bad", "bad", trusted_hops="bad"), "unknown")
        self.assertEqual(resolve_client_ip("10.0.0.8", "1.2.3.4", trusted_hops=2), "10.0.0.8")

    def test_forwarded_for_is_ignored_without_trusted_proxy(self):
        self.assertEqual(
            resolve_client_ip("10.0.0.8", "1.2.3.4", trusted_hops=0),
            "10.0.0.8",
        )

    def test_session_ids_are_random_hex(self):
        first = new_session_id()
        second = new_session_id()
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)
        self.assertIn("Secure", session_cookie_header(first, secure=True))
        with self.assertRaises(ValueError):
            session_cookie_header("bad")

    def test_validate_pdf_upload_accepts_real_pdf(self):
        metadata = validate_pdf_upload(tiny_pdf())
        self.assertGreaterEqual(metadata["page_count"], 1)

    def test_validate_pdf_upload_rejects_bad_inputs(self):
        doc = pymupdf.open()
        doc.new_page()
        encrypted = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="user",
        )
        doc.close()
        for blob in (b"", b"not-a-pdf", b"%PDF-1.4 broken", encrypted):
            with self.subTest(blob=blob), self.assertRaises(InvalidUpload):
                validate_pdf_upload(blob)

    def test_validate_pdf_upload_rejects_oversize_without_allocating(self):
        with self.assertRaises(InvalidUpload):
            validate_pdf_upload(b"%PDF-" + b"x" * 32, max_bytes=8)


class SseTests(unittest.TestCase):
    def test_encode_sse_frame(self):
        event = type(
            "Event",
            (),
            {
                "seq": 7,
                "event_type": "calendar",
                "to_dict": lambda self: {"seq": 7, "event_type": "calendar"},
            },
        )()
        frame = encode_sse(event)
        self.assertIn("id: 7\n", frame)
        self.assertIn("event: calendar\n", frame)
        self.assertIn('data: {"seq":7,"event_type":"calendar"}\n\n', frame)

    def test_parse_last_event_id(self):
        self.assertEqual(parse_last_event_id(None), 0)
        self.assertEqual(parse_last_event_id("12"), 12)
        for bad in ("-1", "abc", "1.5"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_last_event_id(bad)

    def test_heartbeat_frame_contract(self):
        self.assertEqual(heartbeat_frame(), ": keep-alive\n\n")


class ApiApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fin_api_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = RunManager(self.root)
        self.app = ApiApplication(
            self.root,
            manager=self.manager,
            workflow_runner=fake_workflow,
            heartbeat_seconds=0.05,
        )
        self.addCleanup(self.app.close)

    def create(self, session="session-a"):
        return self.app.create_run(
            session,
            "financial",
            {"code": "600519", "name": "贵州茅台", "year": 2024},
            tiny_pdf(),
            [],
        )

    def test_create_run_returns_immediately_and_persists_events(self):
        created = self.create()
        with self.assertRaises(ApiError) as caught:
            self.app.create_run("session-a", "unsupported", {}, tiny_pdf(), [])
        self.assertEqual(caught.exception.code, "UNSUPPORTED_COMMAND")
        self.assertRegex(created["run_id"], r"^\d{8}_\d{6}_[0-9a-f]{8}$")
        self.assertIn(created["status"], {"queued", "running", "succeeded"})
        self.assertTrue(created["events_url"].endswith("/events"))
        wait_for(
            lambda: self.app.get_run("session-a", created["run_id"])["status"]
            in {"succeeded", "partial_failed", "failed"},
            "运行进入终态",
        )
        completed = self.app.get_run("session-a", created["run_id"])
        self.assertEqual(completed["status"], "succeeded")
        events = self.manager.bus.replay(created["run_id"])
        self.assertEqual([event.seq for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[-1].event_type, "run_finished")

    def test_session_isolation_for_run_listing_and_lookup(self):
        created = self.create("session-a")
        with self.assertRaises(ApiError) as caught:
            self.app.get_run("session-b", created["run_id"])
        self.assertEqual(caught.exception.code, "RUN_NOT_FOUND")
        with self.assertRaises(ApiError) as caught:
            self.app.cancel_run("session-b", created["run_id"])
        self.assertEqual(caught.exception.code, "RUN_NOT_FOUND")
        wait_for(
            lambda: self.app.get_run("session-a", created["run_id"])["status"] == "succeeded",
            "运行成功",
        )
        self.assertEqual(self.app.list_runs("session-a")["runs"][0]["run_id"], created["run_id"])
        self.assertEqual(self.app.list_runs("session-b")["runs"], [])

    def test_ask_and_search_read_only_the_selected_run(self):
        created = self.create()
        wait_for(
            lambda: self.app.get_run("session-a", created["run_id"])["status"] == "succeeded",
            "运行成功",
        )
        answer = self.app.ask("session-a", created["run_id"], "2024年营业收入是多少")
        self.assertEqual(answer["status"], "ok")
        self.assertTrue(answer["evidence_ids"])
        search = self.app.search("session-a", created["run_id"], "营业收入", top_k=3)
        self.assertEqual(search["status"], "ok")
        self.assertTrue(search["hits"])
        self.assertIn("result", self.app.get_run(
            "session-a", created["run_id"], include_result=True
        ))
        for call in (
            lambda: self.app.ask("session-a", created["run_id"], ""),
            lambda: self.app.search("session-a", created["run_id"], "", top_k=0),
            lambda: self.app.search("session-a", created["run_id"], "收入", top_k="bad"),
        ):
            with self.assertRaises(ApiError):
                call()
        with self.assertRaises(ApiError) as caught:
            self.app.ask("session-b", created["run_id"], "2024年营业收入是多少")
        self.assertEqual(caught.exception.code, "RUN_NOT_FOUND")
        with self.assertRaises(ApiError) as caught:
            self.app.search("session-b", created["run_id"], "营业收入")
        self.assertEqual(caught.exception.code, "RUN_NOT_FOUND")

    def test_replay_gap_is_rejected(self):
        created = self.create()
        wait_for(
            lambda: self.app.get_run("session-a", created["run_id"])["status"] == "succeeded",
            "运行成功",
        )
        with self.assertRaises(ApiError) as caught:
            self.app.subscribe_events("session-a", created["run_id"], after_seq=999)
        self.assertEqual(caught.exception.code, "EVENT_REPLAY_GAP")

    def test_runtime_bridge_maps_legacy_events_and_finish(self):
        context = self.manager.create_run("session-a", "bridge", {})
        context.start()
        bridge = RuntimeRunBridge(context)
        output = bridge.output("input.txt")
        output.write_text("bridge", encoding="utf-8")
        self.assertEqual(bridge.read(output), b"bridge")
        self.assertEqual(len(bridge.events), 1)
        bridge.event("calculation", formula="1+1", result=2)
        bridge.finish(status="ok", evidence_count=1)
        events = self.manager.bus.replay(context.id)
        self.assertEqual(
            [event.event_type for event in events],
            ["run_created", "run_started", "file_read", "calculation", "pipeline_completed"],
        )
        self.assertEqual(context.status.value, "running")
        context.finish(context._prepared_finish[0], result={"evidence": [1]})
        self.assertEqual(context.status.value, "succeeded")


    def test_runtime_bridge_maps_announcement_end_states(self):
        for legacy_status, expected in (("completed", "succeeded"), ("no_fields", "partial_failed")):
            with self.subTest(status=legacy_status):
                context = self.manager.create_run("session-a", "announcement", {})
                context.start()
                bridge = RuntimeRunBridge(context)
                bridge.finish(status=legacy_status, event_count=0)
                self.assertEqual(context.status.value, "running")
                self.assertEqual(json.loads(bridge.events[-1])["requested_status"], expected)
                context.finish(context._prepared_finish[0], result={"events": []})
                self.assertEqual(context.status.value, expected)

    def test_real_announcement_workflow_retains_results(self):
        # 使用当前默认工作流和真实 PDF，覆盖抽取到 API 终态的桥接。
        application = ApiApplication(self.root, manager=self.manager)
        fixture_root = Path(__file__).resolve().parents[1] / "data" / "demo" / "real"
        for filename in ("fosun_2025_pledge.pdf", "suitang_2025_winning.pdf"):
            with self.subTest(pdf=filename):
                created = application.create_run("session-a", "announcement",
                                                 {"event_type": "auto"},
                                                 (fixture_root / filename).read_bytes())
                self.manager.wait(created["run_id"], timeout=10)
                result = application.get_run("session-a", created["run_id"], include_result=True)
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(result["result"]["announcement"]["status"], "completed")
                self.assertTrue(result["result"]["evidence"])
                self.assertTrue(result["result"]["report_md"])
                self.assertEqual(self.manager.bus.replay(created["run_id"])[-1].event_type,
                                 "run_finished")


class ApiHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer

        from webui import Handler

        cls.temp = tempfile.TemporaryDirectory(prefix="fin_api_http_")
        cls.root = Path(cls.temp.name).resolve()
        cls.manager = RunManager(cls.root)
        cls.app = ApiApplication(
            cls.root,
            manager=cls.manager,
            workflow_runner=fake_workflow,
            heartbeat_seconds=0.05,
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

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        headers_out = dict(response.getheaders())
        status = response.status
        conn.close()
        return status, headers_out, payload

    def create_run(self):
        boundary = "----FinApiBoundary"
        parts = []
        for name, value in (
            ("command", "financial"),
            ("code", "600519"),
            ("name", "贵州茅台"),
            ("year", "2024"),
        ):
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            )
        pdf = tiny_pdf()
        parts.append(
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="pdf"; filename="report.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        )
        body = "".join(parts).encode("utf-8") + pdf + f"\r\n--{boundary}--\r\n".encode("ascii")
        status, headers, payload = self.request(
            "POST",
            "/api/runs",
            body,
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        self.assertEqual(status, 202, payload)
        return json.loads(payload), headers["Set-Cookie"].split(";", 1)[0]

    def test_http_create_status_session_and_sse_replay(self):
        created, cookie = self.create_run()
        run_id = created["run_id"]
        session_id = cookie.split("=", 1)[1]
        wait_for(
            lambda: self.app.get_run(session_id, run_id)["status"] == "succeeded",
            "HTTP 运行成功",
        )
        status, _, payload = self.request(
            "GET", f"/api/runs/{run_id}", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(json.loads(payload)["status"], "succeeded")
        status, _, payload = self.request(
            "GET",
            f"/api/runs/{run_id}/events",
            headers={"Cookie": cookie, "Last-Event-ID": "1"},
        )
        self.assertEqual(status, 200, payload)
        frames = [line for line in payload.decode("utf-8").splitlines() if line.startswith("data: ")]
        sequences = [json.loads(line[6:])["seq"] for line in frames]
        self.assertTrue(sequences)
        self.assertTrue(all(seq > 1 for seq in sequences))

    def test_http_cross_session_and_replay_gap(self):
        created, cookie = self.create_run()
        run_id = created["run_id"]
        forged = "fin_session=" + "f" * 32
        status, _, payload = self.request(
            "GET", "/api/runs", headers={"Cookie": forged}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["runs"], [])
        status, _, payload = self.request(
            "GET", f"/api/runs/{run_id}", headers={"Cookie": forged}
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(payload)["error"]["code"], "RUN_NOT_FOUND")
        for path, body in (
            (f"/api/runs/{run_id}/cancel", None),
            (f"/api/runs/{run_id}/ask", '{"question":"2024年营业收入是多少"}'.encode()),
            (f"/api/runs/{run_id}/search", '{"query":"营业收入"}'.encode()),
        ):
            status, _, payload = self.request(
                "POST",
                path,
                body,
                {"Cookie": forged, **({"Content-Type": "application/json"} if body else {})},
            )
            self.assertEqual(status, 404, payload)
            self.assertEqual(json.loads(payload)["error"]["code"], "RUN_NOT_FOUND")
        status, _, payload = self.request(
            "GET",
            f"/api/runs/{run_id}/events",
            headers={"Cookie": cookie, "Last-Event-ID": "999"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(payload)["error"]["code"], "EVENT_REPLAY_GAP")
        status, _, payload = self.request(
            "GET", f"/api/runs/{run_id}/events",
            headers={"Cookie": forged, "Last-Event-ID": "999"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(payload)["error"]["code"], "RUN_NOT_FOUND")

    def test_http_sse_heartbeat_without_terminal_event(self):
        session_id = "a" * 32
        context = self.app.manager.create_run(session_id, "heartbeat", {})
        conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
        conn.putrequest("GET", f"/api/runs/{context.id}/events")
        conn.putheader("Cookie", f"fin_session={session_id}")
        conn.putheader("Last-Event-ID", "1")
        conn.endheaders()
        try:
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.fp.readline(), b": keep-alive\n")
            self.assertEqual(response.fp.readline(), b"\n")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
