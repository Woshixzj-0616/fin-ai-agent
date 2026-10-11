"""B7 R1-R11 失败注入检查。

脚本只在临时目录或进程内 monkeypatch 注入失败，不修改仓库文件。
输出原始 JSON 证据，供 B7 收工复核使用。
"""
from __future__ import annotations

import argparse
import copy
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
for candidate in (ROOT, AGENT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from api.routes import ApiApplication, ApiError  # noqa: E402
from materials import Run as LegacyRun  # noqa: E402
from runtime import RunManager  # noqa: E402
from runtime import events as runtime_events  # noqa: E402
from runtime import run_manager as runtime_run_manager  # noqa: E402
import runtime.redaction as runtime_redaction  # noqa: E402
import webui_http  # noqa: E402
from webui import Handler  # noqa: E402

import check_docs  # noqa: E402
import check_sensitive  # noqa: E402
import review_structure  # noqa: E402


def _result(
    check_id: str,
    name: str,
    injection: str,
    expected: str,
    observed: str,
    passed: bool,
) -> dict:
    return {
        "id": check_id,
        "name": name,
        "injection": injection,
        "expected": expected,
        "observed": observed,
        "pass": bool(passed),
    }


def _run_cli(script: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


@contextmanager
def _http_server(app):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.fin_api = app
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _request(address, method: str, path: str, *, body=None, headers=None):
    conn = http.client.HTTPConnection(*address, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    payload = response.read()
    headers_out = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    conn.close()
    return status, headers_out, payload


def r1_sensitive_scanner() -> dict:
    secret = "sk-" + "a" * 24
    with tempfile.TemporaryDirectory(prefix="b7_r1_") as temp:
        root = Path(temp)
        leak = root / "leak.txt"
        leak.write_text(secret + "\n", encoding="utf-8")
        clean = root / "clean.txt"
        clean.write_text("营业收入为 80000000 元\n", encoding="utf-8")
        normal = _run_cli(
            "check_sensitive.py",
            "--root",
            str(root),
            "--path",
            str(clean),
        )
        injected = _run_cli(
            "check_sensitive.py",
            "--root",
            str(root),
            "--path",
            str(leak),
        )
        output = injected.stdout + injected.stderr
        passed = (
            normal.returncode == 0
            and injected.returncode != 0
            and secret not in output
            and "openai_key" in output
        )
        observed = (
            f"normal={normal.returncode}, injected={injected.returncode}, "
            f"secret_echo={secret in output}, rule_echo={'openai_key' in output}"
        )
    return _result(
        "R1",
        "敏感扫描器正样本必须红",
        "临时文本放入运行时拼出的 OpenAI 风格密钥",
        "正常样本 0，正样本非 0，且不回显密钥",
        observed,
        passed,
    )


def r2_redaction_gate() -> dict:
    secret = "sk-" + "b" * 24

    def disk_text(bypass: bool) -> str:
        with tempfile.TemporaryDirectory(prefix="b7_r2_") as temp:
            manager = RunManager(Path(temp))
            try:
                context = manager.create_run(
                    "s" * 32,
                    "redaction",
                    {"api_token": secret, "messages": [secret]},
                )
                patches = []
                if bypass:
                    identity = lambda value, **kwargs: value
                    patches = [
                        patch.object(runtime_events, "redact_value", identity),
                        patch.object(runtime_run_manager, "redact_value", identity),
                    ]
                for item in patches:
                    item.start()
                try:
                    context.event(
                        "calculation",
                        payload={"api_key": secret, "raw_response": {"text": secret}},
                    )
                finally:
                    for item in reversed(patches):
                        item.stop()
                return (
                    context.events_path.read_text(encoding="utf-8")
                    + (context.folder / "run.json").read_text(encoding="utf-8")
                )
            finally:
                manager.close()

    normal_text = disk_text(False)
    injected_text = disk_text(True)
    passed = secret not in normal_text and secret in injected_text
    return _result(
        "R2",
        "脱敏门移除必须红",
        "将 events/run_manager 的 redact_value 临时替换为恒等函数",
        "正常落盘无密钥；移除后 event JSONL 或 run.json 出现密钥",
        f"normal_hit={secret in normal_text}, injected_hit={secret in injected_text}",
        passed,
    )


def r3_security_header_gate() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r3_") as temp:
        root = Path(temp)
        manager = RunManager(root)
        app = ApiApplication(root, manager=manager)
        try:
            with _http_server(app) as address:
                status, headers, _ = _request(address, "GET", "/app/")
                normal_csp = headers.get("content-security-policy", "")
            with patch.object(webui_http, "_send_security_headers", lambda *args, **kwargs: None):
                with _http_server(app) as address:
                    status_injected, headers_injected, _ = _request(address, "GET", "/app/")
                    injected_csp = headers_injected.get("content-security-policy", "")
            passed = (
                status == 200
                and normal_csp
                and status_injected == 200
                and not injected_csp
            )
            observed = (
                f"normal_csp={bool(normal_csp)}, injected_csp={bool(injected_csp)}, "
                f"statuses={status}/{status_injected}"
            )
        finally:
            app.close()
    return _result(
        "R3",
        "安全头移除必须红",
        "临时禁用 webui_http._send_security_headers",
        "/app/ 正常有 CSP；禁用后 CSP 缺失",
        observed,
        passed,
    )


def r4_upload_gate() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r4_") as temp:
        root = Path(temp)
        manager = RunManager(root)
        app = ApiApplication(
            root,
            manager=manager,
            workflow_runner=lambda *_args, **_kwargs: {"ok": True},
        )
        routes = sys.modules[ApiApplication.__module__]
        pdf = b"x"
        comparisons = [b"x"] * 5
        normal_code = None
        with patch.object(routes, "validate_pdf_upload", return_value={}):
            try:
                app.create_run("u" * 32, "financial", {}, pdf, comparisons)
            except ApiError as exc:
                normal_code = exc.code
        with (
            patch.object(routes, "validate_pdf_upload", return_value={}),
            patch.object(routes, "MAX_COMPARISON_PDFS", 5),
            patch.object(routes, "MAX_RUN_UPLOAD_BYTES", 81),
        ):
            try:
                app.create_run("u" * 32, "financial", {}, b"x" * 40, comparisons)
                injected_rejected = False
            except ApiError as exc:
                injected_rejected = True
                injected_error = f"{exc.code}: {exc.message}"
        if not injected_rejected:
            injected_error = "accepted"
        app.close()
    passed = normal_code == "INVALID_UPLOAD" and not injected_rejected
    return _result(
        "R4",
        "上传总量门移除必须红",
        "临时把对比材料上限改为 5、总量上限改为 81，并保留验证桩",
        "正常 5 份被拒；移除数量/总量门后不再被拒",
        f"normal_code={normal_code}, injected_rejected={injected_rejected}, injected_error={injected_error}",
        passed,
    )


def r5_proxy_spoof() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r5_") as temp:
        root = Path(temp)
        manager = RunManager(root)
        app = ApiApplication(root, manager=manager)

        def statuses() -> list[int]:
            result = []
            with _http_server(app) as address:
                for index in range(11):
                    status, _, _ = _request(
                        address,
                        "POST",
                        "/api/runs/not-a-run/cancel",
                        headers={
                            "Content-Type": "application/json",
                            "X-Forwarded-For": f"198.51.100.{index + 1}",
                        },
                    )
                    result.append(status)
            return result

        try:
            os.environ.pop("TRUSTED_PROXY_HOPS", None)
            with webui_http._rate_lock:
                webui_http._rate_buckets.clear()
            normal = statuses()
            with patch.object(
                webui_http,
                "resolve_client_ip",
                lambda peer, forwarded, trusted_hops=0: str(forwarded or peer),
            ):
                with webui_http._rate_lock:
                    webui_http._rate_buckets.clear()
                injected = statuses()
        finally:
            app.close()
            with webui_http._rate_lock:
                webui_http._rate_buckets.clear()
    passed = 429 in normal and 429 not in injected
    return _result(
        "R5",
        "代理欺骗必须红",
        "未配置代理时临时直接采用 X-Forwarded-For 作为客户端 IP",
        "正常第 11 次 429；注入后伪造不同 XFF 可绕过 429",
        f"normal_has_429={429 in normal}, injected_has_429={429 in injected}",
        passed,
    )


def r6_legacy_search_route() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r6_") as temp:
        manager = RunManager(Path(temp))
        app = ApiApplication(Path(temp), manager=manager)
        original = Handler.do_POST

        def without_search(self):
            if self.path == "/api/search":
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(404)
                self.send_header("Connection", "close")
                self.send_header("Content-Length", "0")
                self.end_headers()
                self.close_connection = True
                return
            return original(self)

        try:
            with _http_server(app) as address:
                status, _, _ = _request(
                    address,
                    "POST",
                    "/api/search",
                    body='{"query":"营业收入"}'.encode(),
                    headers={"Content-Type": "application/json"},
                )
            with patch.object(Handler, "do_POST", without_search):
                with _http_server(app) as address:
                    status_injected, _, _ = _request(
                        address,
                        "POST",
                        "/api/search",
                        body='{"query":"营业收入"}'.encode(),
                        headers={"Content-Type": "application/json"},
                    )
        finally:
            app.close()
    passed = status == 200 and status_injected == 404
    return _result(
        "R6",
        "旧 API 删除必须红",
        "临时从 Handler.do_POST 拦截并移除 /api/search",
        "正常 200；移除后 404",
        f"normal={status}, injected={status_injected}",
        passed,
    )


def r7_legacy_page_content() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r7_") as temp:
        manager = RunManager(Path(temp))
        app = ApiApplication(Path(temp), manager=manager)
        original_page = webui_http.PAGE
        try:
            with _http_server(app) as address:
                status, _, payload = _request(address, "GET", "/")
                normal_has_ask = b"/api/ask" in payload
            with patch.object(webui_http, "PAGE", original_page.replace("/api/ask", "/api/removed")):
                with _http_server(app) as address:
                    status_injected, _, payload_injected = _request(address, "GET", "/")
                    injected_has_ask = b"/api/ask" in payload_injected
        finally:
            app.close()
    passed = status == 200 and normal_has_ask and status_injected == 200 and not injected_has_ask
    return _result(
        "R7",
        "旧页面迁移丢内容必须红",
        "临时把旧 PAGE 中的 /api/ask 替换为 /api/removed",
        "正常响应含 /api/ask；注入响应缺失",
        f"normal_has_ask={normal_has_ask}, injected_has_ask={injected_has_ask}",
        passed,
    )


def r8_webui_structure_gate() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r8_") as temp:
        root = Path(temp)
        target = root / "agent" / "webui.py"
        target.parent.mkdir(parents=True)
        target.write_text("\n".join(f"x{i} = {i}" for i in range(301)) + "\n", encoding="utf-8")
        payload = review_structure._review_files(root)
    violation = any("超过 300 行门" in item for item in payload["violations"])
    normal = review_structure._review_files(ROOT)
    passed = not normal["violations"] and violation
    return _result(
        "R8",
        "webui.py 重新塞回实现必须红",
        "临时构造 301 行的 agent/webui.py 并运行结构审查",
        "真实仓库 0 违规；注入文件触发 300 行门",
        f"real_violations={len(normal['violations'])}, injected_over_limit={violation}",
        passed,
    )


def r9_archive_web_asset() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r9_") as temp:
        root = Path(temp)
        fake_repo = root / "fake_repo"
        fake_agent = fake_repo / "agent"
        fake_agent.mkdir(parents=True)
        (fake_agent / "placeholder.py").write_text("VALUE = 1\n", encoding="utf-8")
        normal_run = LegacyRun(root / "normal", "archive", {})
        original_agent_dir = sys.modules["materials"].AGENT_DIR
        try:
            sys.modules["materials"].AGENT_DIR = fake_agent
            injected_run = LegacyRun(root / "injected", "archive", {})
        finally:
            sys.modules["materials"].AGENT_DIR = original_agent_dir
    key = "web/legacy/index.html"
    passed = key in normal_run.sources and key not in injected_run.sources
    return _result(
        "R9",
        "归档漏 Web 资产必须红",
        "临时把 Run.sources 扫描根指向没有 web/legacy/index.html 的假仓库",
        "真实归档含旧页面；注入归档缺失",
        f"normal_has_asset={key in normal_run.sources}, injected_has_asset={key in injected_run.sources}",
        passed,
    )


def r10_inventory_hidden_caller() -> dict:
    path = ROOT / "docs" / "refactor" / "evidence" / "B7" / "compatibility-inventory.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    hidden = copy.deepcopy(payload)
    row = next(item for item in hidden["entries"] if item["id"] == "http.analyze")
    row["status"] = "retired"
    callers = row["callers"]
    checker = lambda item: (
        item["status"] == "retired" and bool(item["callers"])
    )
    passed = not any(checker(item) for item in payload["entries"]) and checker(row)
    return _result(
        "R10",
        "兼容清单隐藏调用方必须红",
        "把 /api/analyze 从 keep 改成 retired，但保留已有调用方",
        "正常清单不违反退役门；注入行违反 `retired => callers=[]`",
        f"normal_violation={any(checker(item) for item in payload['entries'])}, injected_violation={checker(row)}, callers={callers}",
        passed,
    )


def r11_docs_bad_link() -> dict:
    with tempfile.TemporaryDirectory(prefix="b7_r11_") as temp:
        root = Path(temp)
        good = root / "good.md"
        good.write_text("[本地](target.md)\n", encoding="utf-8")
        (root / "target.md").write_text("target\n", encoding="utf-8")
        bad = root / "bad.md"
        bad.write_text("[坏链](missing.md)\n", encoding="utf-8")
        normal = _run_cli("check_docs.py", "--root", str(root), "--path", str(good))
        injected = _run_cli("check_docs.py", "--root", str(root), "--path", str(bad))
        passed = normal.returncode == 0 and injected.returncode != 0 and "本地链接不存在" in injected.stderr
        observed = (
            f"normal={normal.returncode}, injected={injected.returncode}, "
            f"missing_link_error={'本地链接不存在' in injected.stderr}"
        )
    return _result(
        "R11",
        "文档坏链必须红",
        "临时 Markdown 写入不存在的本地链接 missing.md",
        "正常文档 0；坏链文档非 0 且报本地链接不存在",
        observed,
        passed,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="执行 B7 R1-R11 失败注入检查")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "refactor" / "evidence" / "B7" / "reverse-checks.json",
    )
    args = parser.parse_args(argv)

    checks = [
        r1_sensitive_scanner(),
        r2_redaction_gate(),
        r3_security_header_gate(),
        r4_upload_gate(),
        r5_proxy_spoof(),
        r6_legacy_search_route(),
        r7_legacy_page_content(),
        r8_webui_structure_gate(),
        r9_archive_web_asset(),
        r10_inventory_hidden_caller(),
        r11_docs_bad_link(),
    ]
    payload = {
        "schema_version": 1,
        "checks": checks,
        "ok": all(item["pass"] for item in checks),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for item in checks:
        print(f"{item['id']} {'PASS' if item['pass'] else 'FAIL'} {item['observed']}")
    print(f"B7 反向检查{'通过' if payload['ok'] else '失败'}：{args.output}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
