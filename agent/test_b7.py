"""B7 安全加固、CI 与旧入口治理契约测试。"""
from __future__ import annotations

import ast
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from api.routes import ApiApplication, ApiError
    from runtime import RunManager
except ModuleNotFoundError:
    from agent.api.routes import ApiApplication, ApiError
    from agent.runtime import RunManager


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


def b7_workflow(context, command, parameters, pdf, comparison_pdfs):
    secret = "sk-" + "z" * 24
    source = context.output("source.pdf")
    source.write_bytes(pdf)
    image = context.output("600519_2024_abcd1234_p1.png")
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        document[0].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(image)
    context.event(
        "calculation",
        actor="program",
        title="B7 安全和脱敏测试",
        summary="事件落盘不得包含原始模型字段或密钥",
        payload={
            "formula": "1+1",
            "api_key": secret,
            "messages": [{"role": "user", "content": secret}],
            "raw_response": {"text": secret},
        },
    )
    return {
        "ok": True,
        "counts": {"match": 1},
        "evidence": [
            {
                "evidence_id": "ev_b7_revenue",
                "company_code": "600519",
                "metric": "revenue",
                "value": "80000000",
                "page": 1,
                "page_image": image.name,
                "source_file": str(source.relative_to(context.root)).replace("\\", "/"),
                "raw_response": secret,
                "messages": [secret],
            }
        ],
        "failures": [],
        "report_md": f"# B7 测试报告\n\n{secret}\n",
    }
class B7ArtifactTests(unittest.TestCase):
    def test_required_artifacts_exist(self):
        required = (
            ".github/workflows/ci.yml",
            "agent/runtime/redaction.py",
            "agent/webui_page.py",
            "agent/webui_pipeline.py",
            "agent/webui_http.py",
            "agent/webui_app.py",
            "web/legacy/index.html",
            "scripts/check_sensitive.py",
            "scripts/check_docs.py",
            "scripts/review_structure.py",
            "scripts/b7_release_smoke.py",
            "scripts/b7_reverse_checks.py",
            "docs/refactor/B7-兼容清单.md",
            "docs/refactor/TDD-B7-安全加固-CI与旧入口治理.md",
            "docs/refactor/evidence/B7/compatibility-inventory.json",
            "docs/refactor/evidence/B7/ci-local-verification.json",
            "docs/refactor/evidence/B7/reverse-checks.json",
        )
        missing = [relative for relative in required if not (ROOT / relative).is_file()]
        self.assertEqual(missing, [])

    def test_ci_runs_the_frozen_quality_commands(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("name: CI", workflow)
        for trigger in ("push:", "pull_request:", "workflow_dispatch:"):
            self.assertIn(trigger, workflow)
        for version in ('"3.11"', '"3.12"'):
            self.assertIn(version, workflow)
        commands = (
            "python -m pip install -r requirements.txt",
            "python -B scripts/check_sensitive.py",
            "python -B scripts/check_docs.py",
            "python -B scripts/review_structure.py",
            "python -B -m compileall -q agent scripts",
            "python -B -m unittest discover -s agent -p 'test*.py'",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, workflow)
        self.assertNotIn("continue-on-error", workflow)


class B7RedactionContractTests(unittest.TestCase):
    def test_redaction_module_exposes_one_shared_contract(self):
        try:
            from runtime.redaction import redact_text, redact_value
        except ModuleNotFoundError:
            from agent.runtime.redaction import redact_text, redact_value

        secret = "sk-" + "a" * 24
        text = f"Authorization: Bearer {secret}"
        redacted = redact_text(text)
        self.assertNotIn(secret, redacted)
        self.assertIn("[已隐藏凭证]", redacted)
        self.assertEqual(redact_text("营业收入为 80000000 元"), "营业收入为 80000000 元")

        value = redact_value(
            {
                "metric": "revenue",
                "messages": [{"role": "user", "content": secret}],
                "nested": {"raw_response": {"text": secret}},
            }
        )
        dumped = json.dumps(value, ensure_ascii=False)
        self.assertEqual(value["metric"], "revenue")
        self.assertNotIn("messages", value)
        self.assertNotIn("raw_response", dumped)
        self.assertNotIn(secret, dumped)


class B7RedactionPersistenceTests(unittest.TestCase):
    def test_event_jsonl_and_run_json_are_redacted_on_disk(self):
        secret = "sk-" + "b" * 24
        with tempfile.TemporaryDirectory(prefix="b7_redaction_") as temp:
            manager = RunManager(Path(temp))
            self.addCleanup(manager.close)
            context = manager.create_run(
                "s" * 32,
                "financial",
                {"api_token": secret, "messages": [{"role": "user", "content": secret}]},
            )
            context.event(
                "calculation",
                payload={
                    "formula": "1+1",
                    "api_key": secret,
                    "raw_response": {"text": secret},
                    "messages": [secret],
                },
            )
            event_text = context.events_path.read_text(encoding="utf-8")
            run_text = (context.folder / "run.json").read_text(encoding="utf-8")
        for label, text in (("events.jsonl", event_text), ("run.json", run_text)):
            with self.subTest(label=label):
                self.assertNotIn(secret, text)
                self.assertNotIn("raw_response", text)
                self.assertNotIn("messages", text)
        self.assertIn("1+1", event_text)

    def test_api_response_and_event_jsonl_do_not_expose_model_payload(self):
        secret = "sk-" + "z" * 24
        with tempfile.TemporaryDirectory(prefix="b7_api_redaction_") as temp:
            root = Path(temp).resolve()
            manager = RunManager(root)
            app = ApiApplication(root, manager=manager, workflow_runner=b7_workflow)
            self.addCleanup(app.close)
            session = "r" * 32
            created = app.create_run(
                session,
                "financial",
                {"code": "600519", "name": "贵州茅台", "year": 2024},
                tiny_pdf(),
                [],
            )
            run_id = created["run_id"]
            wait_for(
                lambda: app.get_run(session, run_id)["status"] == "succeeded",
                "B7 API 脱敏运行完成",
            )
            response_text = json.dumps(
                app.get_run(session, run_id, include_result=True),
                ensure_ascii=False,
            )
            event_text = app.manager.bus.events_path(run_id).read_text(encoding="utf-8")
        for text in (response_text, event_text):
            self.assertNotIn(secret, text)
            self.assertNotIn("raw_response", text)
            self.assertNotIn("messages", text)


class B7HttpSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import webui
            import webui_http
        except ModuleNotFoundError:
            from agent import webui
            from agent import webui_http
        cls.webui_http = webui_http
        cls.temp = tempfile.TemporaryDirectory(prefix="b7_http_")
        cls.root = Path(cls.temp.name).resolve()
        cls.manager = RunManager(cls.root)
        cls.app = ApiApplication(cls.root, manager=cls.manager, workflow_runner=b7_workflow)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
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
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        headers_out = {key.lower(): value for key, value in response.getheaders()}
        status = response.status
        conn.close()
        return status, headers_out, payload

    def create_run(self):
        session = "a" * 32
        created = self.app.create_run(
            session,
            "financial",
            {"code": "600519", "name": "贵州茅台", "year": 2024},
            tiny_pdf(),
            [],
        )
        wait_for(
            lambda: self.app.get_run(session, created["run_id"])["status"] == "succeeded",
            "B7 HTTP 运行完成",
        )
        return session, created["run_id"]

    def assert_common_security_headers(self, headers):
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["referrer-policy"], "no-referrer")

    def test_app_and_legacy_page_security_headers(self):
        status, headers, payload = self.request("GET", "/app/")
        self.assertEqual(status, 200, payload)
        self.assert_common_security_headers(headers)
        self.assertEqual(headers["content-security-policy"], self.webui_http.APP_CSP)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(
            headers["permissions-policy"],
            "camera=(), microphone=(), geolocation=()",
        )
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                status, headers, payload = self.request("GET", path)
                self.assertEqual(status, 200, payload)
                self.assert_common_security_headers(headers)
                self.assertEqual(
                    headers["content-security-policy"],
                    self.webui_http.LEGACY_CSP,
                )
                self.assertEqual(headers["cache-control"], "no-store")

    def test_json_sse_and_image_security_headers_and_redaction(self):
        status, headers, payload = self.request("GET", "/api/runs")
        self.assertEqual(status, 200, payload)
        self.assert_common_security_headers(headers)
        self.assertEqual(headers["cache-control"], "no-store")

        session, run_id = self.create_run()
        cookie = f"fin_session={session}"
        status, headers, payload = self.request(
            "GET",
            f"/api/runs/{run_id}/events",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200, payload)
        self.assert_common_security_headers(headers)
        self.assertEqual(headers["cache-control"], "no-cache")
        self.assertEqual(headers["connection"], "close")
        self.assertEqual(headers["x-accel-buffering"], "no")
        stream_text = payload.decode("utf-8", errors="replace")
        self.assertNotIn("sk-" + "z" * 24, stream_text)
        self.assertNotIn("raw_response", stream_text)
        self.assertNotIn("messages", stream_text)

        image_path = f"/api/runs/{run_id}/images/600519_2024_abcd1234_p1.png"
        status, headers, payload = self.request(
            "GET",
            image_path,
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200, payload)
        self.assert_common_security_headers(headers)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertTrue(payload.startswith(b"\x89PNG"))

        status, headers, payload = self.request(
            "GET",
            f"/api/runs/{run_id}?include_result=1",
            headers={"Cookie": cookie},
        )
        self.assertEqual(status, 200, payload)
        self.assert_common_security_headers(headers)
        response_text = payload.decode("utf-8")
        self.assertNotIn("sk-" + "z" * 24, response_text)
        self.assertNotIn("raw_response", response_text)
        self.assertNotIn("messages", response_text)


class B7WebCompatibilityTests(unittest.TestCase):
    def test_webui_is_a_thin_compatibility_facade(self):
        try:
            import webui
            import webui_http
            import webui_page
            import webui_pipeline
        except ModuleNotFoundError:
            from agent import webui
            from agent import webui_http
            from agent import webui_page
            from agent import webui_pipeline

        self.assertIs(webui.Handler, webui_http.Handler)
        self.assertIs(webui.run_pipeline, webui_pipeline.run_pipeline)
        self.assertIs(webui.run_announcement_pipeline, webui_pipeline.run_announcement_pipeline)
        # 页面按原始字节解码（保留 CRLF）；后端加载与测试必须同一口径。
        self.assertEqual(
            webui.PAGE,
            (ROOT / "web" / "legacy" / "index.html").read_bytes().decode("utf-8"),
        )
        self.assertEqual(webui.PAGE, webui_page.PAGE)
        self.assertEqual(webui.PAGE, webui_page.read_page_text())
        raw_bytes = (ROOT / "web" / "legacy" / "index.html").read_bytes()
        self.assertEqual(webui.PAGE.count("\r\n"), raw_bytes.count(b"\r\n"))
        self.assertEqual(webui.PAGE.count("\n"), raw_bytes.count(b"\n"))

        source = (ROOT / "agent" / "webui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertLessEqual(source.count("\n") + 1, 300)
        self.assertEqual(definitions, ["main"])


class B7UploadBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b7_upload_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manager = RunManager(self.root)
        self.app = ApiApplication(
            self.root,
            manager=self.manager,
            workflow_runner=lambda *_args, **_kwargs: {"ok": True},
        )
        self.addCleanup(self.app.close)
        self.routes = sys.modules[ApiApplication.__module__]

    def create(self, pdf, comparisons):
        return self.app.create_run(
            "u" * 32,
            "financial",
            {"code": "600519", "name": "贵州茅台", "year": 2024},
            pdf,
            comparisons,
        )

    def test_upload_limits_have_frozen_real_values(self):
        self.assertEqual(self.routes.MAX_COMPARISON_PDFS, 4)
        self.assertEqual(self.routes.MAX_RUN_UPLOAD_BYTES, 80 * 1024 * 1024)

    def test_comparison_count_and_total_size_boundaries(self):
        with patch.object(self.routes, "validate_pdf_upload", return_value={}):
            with patch.object(self.routes, "MAX_RUN_UPLOAD_BYTES", 80):
                accepted = self.create(
                    b"x" * 40,
                    [b"x" * size for size in (10, 10, 10, 9)],
                )
                wait_for(
                    lambda: self.app.get_run("u" * 32, accepted["run_id"])["status"] == "succeeded",
                    "79 字节边界运行完成",
                )
                with self.assertRaises(ApiError) as caught:
                    self.create(
                        b"x" * 40,
                        [b"x" * size for size in (10, 10, 10, 11)],
                    )
                self.assertEqual(caught.exception.code, "INVALID_UPLOAD")
            with self.assertRaises(ApiError) as caught:
                self.create(b"x" * 1, [b"x"] * 5)
            self.assertEqual(caught.exception.code, "INVALID_UPLOAD")


class B7ProxyRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import webui
            import webui_http
        except ModuleNotFoundError:
            from agent import webui
            from agent import webui_http
        cls.webui_http = webui_http
        cls.temp = tempfile.TemporaryDirectory(prefix="b7_proxy_")
        cls.manager = RunManager(Path(cls.temp.name).resolve())
        cls.app = ApiApplication(cls.manager.root, manager=cls.manager)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
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

    def setUp(self):
        with self.webui_http._rate_lock:
            self.webui_http._rate_buckets.clear()
        self.old_hops = os.environ.pop("TRUSTED_PROXY_HOPS", None)
        self.addCleanup(self._restore_proxy_env)

    def _restore_proxy_env(self):
        if self.old_hops is None:
            os.environ.pop("TRUSTED_PROXY_HOPS", None)
        else:
            os.environ["TRUSTED_PROXY_HOPS"] = self.old_hops
        with self.webui_http._rate_lock:
            self.webui_http._rate_buckets.clear()

    def post_with_forwarded_for(self, forwarded_for):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        conn.request(
            "POST",
            "/api/runs/not-a-run/cancel",
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-For": forwarded_for,
            },
        )
        response = conn.getresponse()
        payload = response.read()
        status = response.status
        conn.close()
        return status, payload

    def test_forged_forwarded_for_cannot_bypass_rate_limit_without_proxy(self):
        statuses = []
        for index in range(11):
            status, payload = self.post_with_forwarded_for(f"198.51.100.{index + 1}")
            statuses.append(status)
            if index < 10:
                self.assertEqual(status, 404, payload)
        self.assertEqual(statuses, [404] * 10 + [429])

    def test_trusted_proxy_uses_rightmost_forwarded_address(self):
        os.environ["TRUSTED_PROXY_HOPS"] = "1"
        for _ in range(10):
            status, payload = self.post_with_forwarded_for("203.0.113.10")
            self.assertEqual(status, 404, payload)
        status, payload = self.post_with_forwarded_for("203.0.113.10")
        self.assertEqual(status, 429, payload)
        status, payload = self.post_with_forwarded_for("203.0.113.11")
        self.assertEqual(status, 404, payload)


class B7ArchiveAndQualityTests(unittest.TestCase):
    def test_run_sources_include_web_assets_and_ci_workflow(self):
        try:
            from materials import Run
        except ModuleNotFoundError:
            from agent.materials import Run
        with tempfile.TemporaryDirectory(prefix="b7_archive_") as temp:
            run = Run(Path(temp), "archive", {})
            self.assertIn("web/legacy/index.html", run.sources)
            self.assertIn("web/js/main.js", run.sources)
            self.assertIn(".github/workflows/ci.yml", run.sources)

    def test_quality_scripts_have_positive_and_negative_controls(self):
        from scripts import check_docs, check_sensitive, review_structure
        secret = "sk-" + "c" * 24
        self.assertEqual(check_sensitive.scan_text("营业收入为 80000000 元"), [])
        self.assertIn(("openai_key", 1), check_sensitive.scan_text(secret))
        self.assertEqual(check_docs._fence_errors("```python\npass\n```\n"), [])
        self.assertTrue(check_docs._fence_errors("```python\npass\n"))

        with tempfile.TemporaryDirectory(prefix="b7_quality_") as temp:
            root = Path(temp)
            leak = root / "leak.txt"
            leak.write_text(secret + "\n", encoding="utf-8")
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "check_sensitive.py"),
                    "--root",
                    str(root),
                    "--path",
                    str(leak),
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertIn("openai_key", result.stdout + result.stderr)

            bad_md = root / "bad.md"
            bad_md.write_text("[坏链](missing.md)\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "check_docs.py"),
                    "--root",
                    str(root),
                    "--path",
                    str(bad_md),
                ],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("本地链接不存在", result.stderr)

            bad_py = root / "bad.py"
            bad_py.write_text("from api import routes\n", encoding="utf-8")
            good_py = root / "good.py"
            good_py.write_text("from runtime import RunManager\n", encoding="utf-8")
            self.assertTrue(review_structure._forbidden_imports(bad_py))
            self.assertEqual(review_structure._forbidden_imports(good_py), [])

    def test_docs_scan_skips_generated_outputs_by_default(self):
        from scripts import check_docs

        with tempfile.TemporaryDirectory(prefix="b7_docs_scope_") as temp:
            root = Path(temp)
            good = root / "docs" / "guide.md"
            generated = root / "results" / "holdout" / "report.md"
            work = root / "work" / "debug.md"
            for path in (good, generated, work):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("[坏链](missing.pdf)\n", encoding="utf-8")

            default_files = check_docs._markdown_files(root, [])
            self.assertIn(good.resolve(), default_files)
            self.assertNotIn(generated.resolve(), default_files)
            self.assertNotIn(work.resolve(), default_files)
            self.assertIn(generated.resolve(), check_docs._markdown_files(root, [generated]))

class B7InventoryTests(unittest.TestCase):
    def test_inventory_has_complete_frozen_rows(self):
        path = ROOT / "docs" / "refactor" / "evidence" / "B7" / "compatibility-inventory.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["entries"]
        by_id = {row["id"]: row for row in rows}
        expected = {
            "http.prefill",
            "http.analyze",
            "http.announcement",
            "http.ask",
            "http.search",
            "static.root",
            "static.index",
            "static.trace",
            "python.webui_facade",
            "launcher.webui_script",
            "launcher.webui_app",
        }
        self.assertEqual(set(by_id), expected)
        for row in rows:
            with self.subTest(row=row["id"]):
                self.assertEqual(
                    set(row),
                    {"id", "kind", "entry", "callers", "status", "evidence", "retirement_gate"},
                )
                self.assertIn(row["status"], {"keep", "retire_candidate", "retired"})
                self.assertIsInstance(row["callers"], list)
                self.assertIsInstance(row["evidence"], list)
                self.assertTrue(row["evidence"])
                if row["status"] == "keep":
                    self.assertTrue(row["callers"])
                if row["status"] == "retired":
                    self.assertEqual(row["callers"], [])
                    self.assertTrue(row["retirement_gate"])
        self.assertEqual(by_id["http.prefill"]["status"], "keep")
        self.assertEqual(by_id["launcher.webui_app"]["status"], "keep")


if __name__ == "__main__":
    unittest.main()
