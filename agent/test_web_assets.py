"""B3 独立前端资产与工作台壳测试。"""
from __future__ import annotations

import http.client
import json
import re
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

try:
    from webui import PAGE, Handler
except ModuleNotFoundError:
    from agent.webui import PAGE, Handler


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
REQUIRED_IDS = {
    "run-sidebar",
    "run-sidebar-toggle",
    "run-list",
    "workspace",
    "task-form",
    "stage-view",
    "trace-view",
    "results-view",
    "ask-view",
    "status-region",
    "error-region",
}
ASSET_PATHS = (
    "/app/css/tokens.css",
    "/app/favicon.svg",
    "/app/css/layout.css",
    "/app/css/components.css",
    "/app/css/responsive.css",
    "/app/js/api.js",
    "/app/js/stream.js",
    "/app/js/main.js",
    "/app/js/run-state.js",
    "/app/js/views/ask.js",
    "/app/js/views/task.js",
    "/app/js/views/trace.js",
    "/app/js/views/results.js",
)


class WebAssetHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
        conn.request("GET", path)
        response = conn.getresponse()
        payload = response.read()
        headers = {key.lower(): value for key, value in response.getheaders()}
        status = response.status
        conn.close()
        return status, headers, payload

    def test_app_entries_serve_standalone_html(self):
        for path in ("/app", "/app/"):
            with self.subTest(path=path):
                status, headers, payload = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertEqual(int(headers["content-length"]), len(payload))
                self.assertIn(b'data-app="fin-workbench"', payload)

    def test_dom_contract_contains_all_stable_nodes(self):
        status, _, payload = self.request("/app/")
        self.assertEqual(status, 200)
        html = payload.decode("utf-8")
        self.assertRegex(html, r'<main\b[^>]*\bdata-app="fin-workbench"')
        for node_id in REQUIRED_IDS:
            self.assertRegex(html, rf'\bid="{re.escape(node_id)}"')

    def test_styles_and_scripts_have_contract_headers(self):
        for path in ASSET_PATHS:
            with self.subTest(path=path):
                status, headers, payload = self.request(path)
                self.assertEqual(status, 200)
                expected = {
                    ".css": "text/css; charset=utf-8",
                    ".js": "text/javascript; charset=utf-8",
                    ".svg": "image/svg+xml",
                }[Path(path).suffix]
                self.assertEqual(headers["content-type"], expected)
                self.assertEqual(headers["cache-control"], "public, max-age=300")
                self.assertEqual(int(headers["content-length"]), len(payload))

    def test_missing_and_directory_paths_are_not_guessed(self):
        for path in (
            "/app/missing.css",
            "/app/css",
            "/app/css/",
            "/app/js/views",
            "/app/js/main",
        ):
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertEqual(status, 404)

    def test_static_path_traversal_is_rejected(self):
        for path in (
            "/app/../agent/webui.py",
            "/app/%2e%2e/agent/webui.py",
            "/app/%2e%2e%5cagent%5cwebui.py",
            "/app/%2e%2e%2fagent%2fwebui.py",
            "/app/%00index.html",
        ):
            with self.subTest(path=path):
                status, _, payload = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"class Handler", payload)

    def test_old_page_and_trace_asset_remain_compatible(self):
        expected = PAGE.encode("utf-8")
        for path in ("/", "/index.html"):
            with self.subTest(path=path):
                status, headers, payload = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(payload, expected)
                self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        status, _, payload = self.request("/trace.js")
        self.assertEqual(status, 200)
        self.assertGreater(len(payload), 0)


class FrontendSourceTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (WEB / relative).read_text(encoding="utf-8")

    def test_index_has_no_inline_or_remote_assets(self):
        html = self.read("index.html")
        self.assertNotRegex(html, r"<style\b", re.IGNORECASE)
        self.assertNotRegex(
            html,
            r"<script(?![^>]*\bsrc=)[^>]*>",
            re.IGNORECASE,
        )
        self.assertNotRegex(html, r"\sstyle\s*=", re.IGNORECASE)
        self.assertNotRegex(html, r"\son[a-z]+\s*=", re.IGNORECASE)
        self.assertNotRegex(html, r"(?:src|href)=[\"'](?:https?:)?//", re.IGNORECASE)
        self.assertNotIn("@import", html)
        self.assertIn('<link rel="stylesheet" href="/app/css/tokens.css">', html)
        self.assertIn('<script type="module" src="/app/js/main.js"></script>', html)

    def test_assets_do_not_depend_on_network_or_remote_fonts(self):
        for path in WEB.rglob("*"):
            if not path.is_file() or path.suffix not in {".css", ".html", ".js"}:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in (
                r"https?://",
                r"//cdn\.",
                r"@import\s+url",
                r"fonts\.googleapis\.com",
                r"fonts\.gstatic\.com",
            ):
                with self.subTest(path=path, pattern=pattern):
                    self.assertNotRegex(text, pattern, re.IGNORECASE)

    def test_views_are_pure_and_do_not_create_transport_clients(self):
        for relative in (
            "js/views/ask.js",
            "js/views/task.js",
            "js/views/trace.js",
            "js/views/results.js",
        ):
            with self.subTest(relative=relative):
                source = self.read(relative)
                self.assertNotIn("fetch(", source)
                self.assertNotIn("EventSource", source)
                self.assertRegex(source, r"export\s+function\s+render")

    def test_run_state_has_exact_empty_contract(self):
        module_uri = (WEB / "js" / "run-state.js").resolve().as_uri()
        script = (
            f"import({json.dumps(module_uri)}).then((module) => "
            "console.log(JSON.stringify(module.createRunStore().getState())))"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "runs": [],
                "currentRunId": None,
                "currentRun": None,
                "events": [],
                "result": None,
                "connection": "idle",
                "error": None,
                "cancelPending": False,
                "askHistory": [],
                "searchResults": [],
                "searchStatus": "idle",
            },
        )

    def test_main_wires_state_views_and_sidebar_without_transport(self):
        source = self.read("js/main.js")
        self.assertIn("createRunStore", source)
        self.assertIn("subscribe", source)
        self.assertIn("renderTaskView", source)
        self.assertIn("renderTraceView", source)
        self.assertIn("renderResultsView", source)
        self.assertIn("run-sidebar-toggle", source)
        self.assertIn("createApiClient", source)
        self.assertIn("createRunEventStream", source)
        self.assertNotIn("setInterval", source)
        self.assertNotIn("fetch(", source)
        self.assertNotIn("EventSource", source)


if __name__ == "__main__":
    unittest.main()
