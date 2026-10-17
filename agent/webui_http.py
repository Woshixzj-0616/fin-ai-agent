"""旧 Web HTTP 层与安全响应边界。"""
from __future__ import annotations

import json
import base64
import hmac
import os
import queue
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

try:
    from api import ApiApplication, ApiError
    from api.security import (
        InvalidUpload,
        new_session_id,
        parse_session_cookie,
        resolve_client_ip,
        session_cookie_header,
        validate_pdf_upload,
    )
    from api.sse import encode_sse, heartbeat_frame, parse_last_event_id
    from materials import ROOT, Run
    from model_settings import (
        activate_model_session,
        prepare_model_settings,
        public_model_settings,
        reset_model_session,
        save_model_settings,
    )
    from qa import answer as qa_answer
    from webui_page import PAGE, PAGE_PNG
    from webui_pipeline import (
        LAST_DOCUMENT_TEXTS,
        LAST_FACTS,
        WORKSPACE,
        _ANALYZE_LOCK,
        _LAST_LOCK,
        _WORK_TTL_SEC,
        guess_identity,
        run_announcement_pipeline,
        run_pipeline,
    )
except ModuleNotFoundError:
    from agent.api import ApiApplication, ApiError
    from agent.api.security import (
        InvalidUpload,
        new_session_id,
        parse_session_cookie,
        resolve_client_ip,
        session_cookie_header,
        validate_pdf_upload,
    )
    from agent.api.sse import encode_sse, heartbeat_frame, parse_last_event_id
    from agent.materials import ROOT, Run
    from agent.model_settings import (
        activate_model_session,
        prepare_model_settings,
        public_model_settings,
        reset_model_session,
        save_model_settings,
    )
    from agent.qa import answer as qa_answer
    from agent.webui_page import PAGE, PAGE_PNG
    from agent.webui_pipeline import (
        LAST_DOCUMENT_TEXTS,
        LAST_FACTS,
        WORKSPACE,
        _ANALYZE_LOCK,
        _LAST_LOCK,
        _WORK_TTL_SEC,
        guess_identity,
        run_announcement_pipeline,
        run_pipeline,
    )


MAX_UPLOAD = 40 * 1024 * 1024
_RATE_CAPACITY = 10
_RATE_REFILL_PER_SEC = 0.2
_rate_buckets: dict[str, tuple[float, float]] = {}
_rate_lock = threading.Lock()
_API_INIT_LOCK = threading.Lock()

WEB_ROOT = (ROOT / "web").resolve()
WEB_MIME_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
FIN_SCOPE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
LEGACY_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'"
)


def _rate_limit(ip: str, cost: float = 1.0) -> bool:
    """令牌桶限流；返回 True=放行，False=应拒绝。"""
    now = time.monotonic()
    with _rate_lock:
        tokens, last = _rate_buckets.get(ip, (_RATE_CAPACITY, now))
        tokens = min(_RATE_CAPACITY, tokens + (now - last) * _RATE_REFILL_PER_SEC)
        if tokens < cost:
            _rate_buckets[ip] = (tokens, now)
            return False
        _rate_buckets[ip] = (tokens - cost, now)
        return True


def _cleanup_work_dir() -> None:
    """清理 webui_work 里超时的上传 PDF 与页图，防磁盘被吃满。"""
    try:
        cutoff = time.time() - _WORK_TTL_SEC
        if not WORKSPACE.is_dir():
            return
        for path in WORKSPACE.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_web_asset(url_path: str) -> tuple[Path, str] | None:
    """把 /app/ URL 映射到 web/ 内的静态文件，越界或目录路径一律拒绝。"""
    decoded = unquote(url_path)
    if "\x00" in decoded or "\\" in decoded:
        return None
    if decoded in ("/app", "/app/"):
        relative = "index.html"
    elif decoded.startswith("/app/"):
        relative = decoded[len("/app/") :]
    else:
        return None
    if not relative or relative.startswith("/"):
        return None
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    candidate = (WEB_ROOT / relative).resolve()
    try:
        candidate.relative_to(WEB_ROOT)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    mime = WEB_MIME_TYPES.get(candidate.suffix.lower())
    if not mime:
        return None
    return candidate, mime


def _send_security_headers(
    handler: BaseHTTPRequestHandler,
    *,
    csp: str | None = None,
    permissions_policy: bool = False,
) -> None:
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "no-referrer")
    if csp:
        handler.send_header("Content-Security-Policy", csp)
    if permissions_policy:
        handler.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )


def _send_bytes(
    handler: BaseHTTPRequestHandler,
    code: int,
    content_type: str,
    blob: bytes,
    *,
    cache_control: str = "no-store",
    csp: str | None = None,
    permissions_policy: bool = False,
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> None:
    handler.send_response(code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", cache_control)
    _send_security_headers(
        handler,
        csp=csp,
        permissions_policy=permissions_policy,
    )
    for key, value in extra_headers:
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(blob)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(blob)


def _json(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    extra_headers: list[tuple[str, str]] = []
    session_id = getattr(handler, "_new_session_id", None)
    if session_id:
        secure = handler.headers.get("X-Forwarded-Proto", "").lower() == "https"
        extra_headers.append(
            ("Set-Cookie", session_cookie_header(session_id, secure=secure))
        )
    _send_bytes(
        handler,
        code,
        "application/json; charset=utf-8",
        blob,
        extra_headers=tuple(extra_headers),
    )


def _json_api_error(handler: BaseHTTPRequestHandler, error: ApiError) -> None:
    _json(handler, error.status, error.to_dict())


def _six_facade():
    try:
        import six_module_facade as facade
    except ModuleNotFoundError as exc:
        if exc.name != "six_module_facade":
            raise
        from agent import six_module_facade as facade
    facade.prepare_local_sample()
    return facade


def _six_error(handler: BaseHTTPRequestHandler, error: Exception) -> None:
    status = int(getattr(error, "status", 500))
    code = str(getattr(error, "code", "SIX_MODULE_ERROR"))
    _json(handler, status, {"error": {"code": code, "message": str(error)}})


def _parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict, bytes]:
    """只支持浏览器标准 multipart/form-data，够薄页面用。"""
    ctype = handler.headers.get("Content-Type", "")
    match = re.search(r'boundary="?([^";]+)"?', ctype)
    if not match:
        raise ValueError("缺少 multipart boundary")
    boundary = b"--" + match.group(1).encode()
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_UPLOAD:
        raise ValueError("请求体为空或超过 40MiB")
    body = handler.rfile.read(length)
    fields: dict = {}
    pdf = b""
    for part in body.split(boundary):
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, content = part.split(b"\r\n\r\n", 1)
        head_text = head.decode("utf-8", errors="replace")
        name_m = re.search(r'name="([^"]+)"', head_text)
        if not name_m:
            continue
        name = name_m.group(1)
        if name == "pdf" or 'filename="' in head_text:
            if name == "pdf":
                pdf = content
            elif name == "comparison_pdf":
                fields.setdefault("_comparison_pdfs", []).append(content)
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields, pdf


class Handler(BaseHTTPRequestHandler):
    server_version = "FinAgentWebUI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[webui] {self.address_string()} {fmt % args}")

    def _authorized(self) -> bool:
        """Require a shared team password when the service is internet-facing."""
        password = os.environ.get("APP_ACCESS_PASSWORD", "")
        if not password:
            return True
        header = self.headers.get("Authorization", "")
        try:
            scheme, encoded = header.split(" ", 1)
            supplied = base64.b64decode(encoded, validate=True).decode("utf-8")
            username, candidate = supplied.split(":", 1)
            valid = scheme.lower() == "basic" and username == "finscope"
            valid = valid and hmac.compare_digest(candidate, password)
        except (ValueError, UnicodeDecodeError):
            valid = False
        if valid:
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="FinScope", charset="UTF-8"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        _send_security_headers(self)
        self.end_headers()
        self.close_connection = True
        return False

    def _serve_web_asset(self, path: str) -> None:
        resolved = _resolve_web_asset(path)
        if resolved is None:
            _json(self, 404, {"error": "not found"})
            return
        asset, content_type = resolved
        try:
            blob = asset.read_bytes()
        except OSError:
            _json(self, 404, {"error": "not found"})
            return
        _send_bytes(
            self,
            200,
            content_type,
            blob,
            cache_control=(
                "no-store"
                if asset.suffix.lower() == ".html"
                else "public, max-age=300"
            ),
            csp=FIN_SCOPE_CSP if asset == WEB_ROOT / "finscope.html" else APP_CSP,
            permissions_policy=asset.suffix.lower() == ".html",
        )

    def _api(self) -> ApiApplication:
        app = getattr(self.server, "fin_api", None)
        if app is not None:
            return app
        with _API_INIT_LOCK:
            app = getattr(self.server, "fin_api", None)
            if app is None:
                app = ApiApplication(ROOT)
                self.server.fin_api = app
        return app

    def _new_api_session(self) -> str:
        session_id = parse_session_cookie(self.headers.get("Cookie"))
        if session_id:
            return session_id
        session_id = new_session_id()
        self._new_session_id = session_id
        return session_id

    def _read_json_body(self, limit: int = 64 * 1024) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            raise ApiError(400, "INVALID_BODY", "请求体为空或过大")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(400, "INVALID_JSON", "请求体不是合法 JSON") from exc
        if not isinstance(body, dict):
            raise ApiError(400, "INVALID_JSON", "请求体必须是 JSON 对象")
        return body

    def _serve_events(self, run_id: str) -> None:
        session_id = self._new_api_session()
        try:
            header_seq = parse_last_event_id(self.headers.get("Last-Event-ID"))
            query_raw = parse_qs(urlsplit(self.path).query).get("after_seq", [None])[0]
            query_seq = parse_last_event_id(query_raw) if query_raw is not None else 0
            after_seq = max(header_seq, query_seq)
            subscription = self._api().subscribe_events(
                session_id,
                run_id,
                after_seq=after_seq,
            )
        except ValueError as exc:
            _json_api_error(self, ApiError(400, "INVALID_LAST_EVENT_ID", str(exc)))
            return
        except ApiError as exc:
            _json_api_error(self, exc)
            return
        terminal = {"succeeded", "partial_failed", "failed", "cancelled", "expired"}
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            _send_security_headers(self)
            if getattr(self, "_new_session_id", None):
                secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                self.send_header(
                    "Set-Cookie",
                    session_cookie_header(self._new_session_id, secure=secure),
                )
            self.end_headers()
            self.close_connection = True
            while True:
                try:
                    event = subscription.get(timeout=self._api().heartbeat_seconds)
                except queue.Empty:
                    self.wfile.write(heartbeat_frame().encode("utf-8"))
                    self.wfile.flush()
                    continue
                self.wfile.write(
                    encode_sse(event, wire_event="message").encode("utf-8")
                )
                self.wfile.flush()
                if event.status in terminal:
                    break
        except ConnectionError:
            pass
        finally:
            subscription.close()

    def _create_run_json(self) -> None:
        fields, pdf = _parse_multipart(self)
        if not pdf:
            raise InvalidUpload("缺少 PDF")
        command = (fields.get("command") or "financial").strip()
        parameters: dict = {}
        if command == "financial":
            code = (fields.get("code") or "").strip()
            name = (fields.get("name") or "").strip()
            year_raw = (fields.get("year") or "").strip()
            auto = guess_identity(pdf)
            code = code or str(auto.get("code") or "")
            name = name or str(auto.get("name") or "")
            year_raw = year_raw or str(auto.get("year") or "")
            if not re.fullmatch(r"\d{6}", code):
                raise ApiError(400, "INVALID_IDENTITY", "未能识别证券代码")
            if not name:
                raise ApiError(400, "INVALID_IDENTITY", "未能识别公司名称")
            if not re.fullmatch(r"\d{4}", year_raw):
                raise ApiError(400, "INVALID_IDENTITY", "未能识别报告年度")
            parameters.update(
                code=code,
                name=name,
                year=int(year_raw),
                draft=fields.get("draft") or None,
            )
        else:
            parameters.update(
                event_type=fields.get("event_type") or "auto",
                ocr=fields.get("ocr") == "true",
            )
        payload = self._api().create_run(
            self._new_api_session(),
            command,
            parameters,
            pdf,
            fields.get("_comparison_pdfs", []),
        )
        _json(self, 202, payload)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/healthz":
            _json(self, 200, {"status": "ok"})
            return
        if not self._authorized():
            return
        if path == "/api/six/modules":
            try:
                _json(self, 200, {"modules": _six_facade().labels()})
            except Exception as exc:
                _six_error(self, exc)
            return
        if path == "/api/six/sample":
            try:
                _json(self, 200, _six_facade().sample())
            except Exception as exc:
                _six_error(self, exc)
            return
        if path == "/api/six/sample/research":
            try:
                _json(self, 200, _six_facade().sample_research())
            except Exception as exc:
                _six_error(self, exc)
            return
        six_path = re.fullmatch(r"/api/six/(?:sample|reports/([0-9a-f]{32}))(?:/([^/]+))?(?:/([^/]+))?", path)
        if six_path:
            facade = _six_facade()
            report_id = six_path.group(1) or facade.SAMPLE_REPORT_ID
            first, second = six_path.group(2), six_path.group(3)
            try:
                if first is None:
                    _json(self, 200, facade.report(report_id))
                elif first == "research" and second is None:
                    _json(self, 200, facade.research(report_id))
                elif first == "modules" and second:
                    _json(self, 200, facade.report_module(report_id, second))
                elif first == "pages" and second and second.isdigit():
                    _json(self, 200, facade.page(report_id, int(second)))
                elif first == "file" and second is None:
                    pdf_path = facade.report_pdf_path(report_id)
                    _send_bytes(self, 200, "application/pdf", pdf_path.read_bytes(), cache_control="no-store")
                elif first == "research" and second == "export":
                    from urllib.parse import quote
                    result = facade.research(report_id)
                    filename = quote(f"{result.get('company') or '财报'}_{result.get('period') or '综合研报'}_六模块分析.md")
                    _send_bytes(
                        self,
                        200,
                        "text/markdown; charset=utf-8",
                        str(result.get("report_markdown") or "").encode("utf-8"),
                        extra_headers=(("Content-Disposition", f"attachment; filename*=UTF-8''{filename}"),),
                    )
                else:
                    _json(self, 404, {"error": {"message": "六模块资源不存在。"}})
            except Exception as exc:
                _six_error(self, exc)
            return
        if path == "/api/six/reports":
            try:
                project_id = parse_qs(parsed.query).get("project_id", [""])[0]
                _json(self, 200, _six_facade().project_reports(project_id))
            except Exception as exc:
                _six_error(self, exc)
            return
        match = re.fullmatch(r"/api/six/reports/([0-9a-f]{32})", path)
        if match:
            try:
                _json(self, 200, _six_facade().report(match.group(1)))
            except Exception as exc:
                _six_error(self, exc)
            return
        if path == "/api/settings/model":
            _json(self, 200, public_model_settings(self._new_api_session()))
            return
        if path == "/api/runs":
            _json(self, 200, self._api().list_runs(self._new_api_session()))
            return
        match = re.fullmatch(r"/api/runs/([^/]+)/events", path)
        if match:
            self._serve_events(match.group(1))
            return
        match = re.fullmatch(r"/api/runs/([^/]+)/images/([^/]+)", path)
        if match:
            self._serve_run_image(match.group(1), unquote(match.group(2)))
            return
        match = re.fullmatch(r"/api/runs/([^/]+)", path)
        if match:
            include_result = parse_qs(parsed.query).get("include_result", ["0"])[0] in {
                "1",
                "true",
                "yes",
            }
            try:
                payload = self._api().get_run(
                    self._new_api_session(),
                    match.group(1),
                    include_result=include_result,
                )
            except ApiError as exc:
                _json_api_error(self, exc)
                return
            _json(self, 200, payload)
            return
        decoded_path = unquote(path)
        if decoded_path == "/app" or decoded_path.startswith("/app/"):
            self._serve_web_asset(decoded_path)
            return
        if path == "/trace.js":
            blob = (ROOT / "docs" / "trace.js").read_bytes()
            _send_bytes(
                self,
                200,
                "application/javascript; charset=utf-8",
                blob,
                csp=LEGACY_CSP,
            )
            return
        if path in ("/", "/index.html"):
            if os.environ.get("APP_ACCESS_PASSWORD") or os.environ.get("FIN_PUBLIC_MODE") == "1":
                self.send_response(302)
                self.send_header("Location", "/app/finscope.html")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                _send_security_headers(self)
                self.end_headers()
            else:
                _send_bytes(
                    self, 200, "text/html; charset=utf-8", PAGE.encode("utf-8"),
                    csp=LEGACY_CSP,
                )
            return
        if path.startswith("/img/"):
            self._serve_image(path[len("/img/") :])
            return
        _json(self, 404, {"error": "not found"})

    def _serve_run_image(self, run_id: str, name: str) -> None:
        try:
            path = self._api().get_evidence_image(self._new_api_session(), run_id, name)
            blob = path.read_bytes()
        except ApiError as exc:
            _json_api_error(self, exc)
            return
        except OSError:
            _json(self, 404, {"error": "页图不存在或无权访问"})
            return
        _send_bytes(self, 200, "image/png", blob)

    def _serve_image(self, name: str) -> None:
        if not PAGE_PNG.fullmatch(name):
            _json(self, 404, {"error": "not found"})
            return
        path = ROOT / "results" / name
        if not path.is_file():
            _json(self, 404, {"error": "页图不存在"})
            return
        _send_bytes(self, 200, "image/png", path.read_bytes())

    def _search_json(self) -> None:
        """语义检索：基于本次分析的 PDF 页文本，返回相关段落。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 32 * 1024:
            _json(self, 400, {"error": "请求体为空或过大"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            _json(self, 400, {"error": "请求体不是合法 JSON"})
            return
        query = (body.get("query") or "").strip() if isinstance(body, dict) else ""
        top_k = int(body.get("top_k") or 8) if isinstance(body, dict) else 8
        if not query or len(query) > 40:
            _json(self, 400, {"error": "query 需为 1–40 字"})
            return
        with _LAST_LOCK:
            facts_snapshot = list(LAST_FACTS)
            docs = {key: list(value) for key, value in LAST_DOCUMENT_TEXTS.items()}
        if not docs:
            _json(
                self,
                200,
                {
                    "status": "empty",
                    "hits": [],
                    "hint": "请先完成一次分析，再做语义检索",
                },
            )
            return
        from tools import search_run_text

        out = search_run_text(facts_snapshot, docs, query=query, top_k=top_k)
        out["note"] = out.get("note") or "检索命中为相关段落，不能单独作为数值裁决依据"
        _json(self, 200, out)

    def _ask_json(self) -> None:
        """受限问答：只认上次分析的 facts，答案必挂 evidence_id。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64 * 1024:
            _json(self, 400, {"error": "请求体为空或过大"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            _json(self, 400, {"error": "请求体不是合法 JSON"})
            return
        question = (
            (body.get("question") or "").strip() if isinstance(body, dict) else ""
        )
        if not question:
            _json(self, 400, {"error": "缺少 question"})
            return
        client, run = None, None
        try:
            from llm_check import LLMClient

            client = LLMClient.from_environment()
            run = Run(
                ROOT,
                "webui-ask",
                {"command": "webui-ask", "question_len": len(question)},
            )
        except Exception:
            client, run = None, None
        try:
            with _LAST_LOCK:
                facts_snapshot = list(LAST_FACTS)
            out = qa_answer(
                question,
                facts_snapshot,
                client=client,
                run=run,
                use_llm=client is not None,
            )
            if run is not None:
                run.finish(
                    status="ok",
                    question_len=len(question),
                    answer_status=out.get("status"),
                    evidence_count=len(out.get("evidence_ids") or []),
                )
            _json(self, 200, out)
        except Exception as exc:
            traceback.print_exc()
            _json(self, 400, {"error": f"{type(exc).__name__}: {exc}"})

    def _client_ip(self) -> str:
        try:
            trusted_hops = int(os.environ.get("TRUSTED_PROXY_HOPS") or 0)
        except ValueError:
            trusted_hops = 0
        return resolve_client_ip(
            self.client_address[0],
            self.headers.get("X-Forwarded-For"),
            trusted_hops=trusted_hops,
        )

    def do_POST(self):
        if not self._authorized():
            return
        token = activate_model_session(self._new_api_session())
        try:
            ip = self._client_ip()
            if not _rate_limit(ip):
                _json(self, 429, {"error": "请求过于频繁，请稍后再试"})
                return
            path = urlsplit(self.path).path
            ctype = self.headers.get("Content-Type") or ""
            if path == "/api/six/reports" and ctype.startswith("multipart/form-data"):
                try:
                    fields, pdf = _parse_multipart(self)
                    payload = _six_facade().create_project_report(
                        project_id=fields.get("project_id") or "",
                        file_name=fields.get("file_name") or "",
                        content=pdf,
                        modules=fields.get("modules") or "all",
                        session_id=self._new_api_session(),
                        company=fields.get("company") or "",
                        code=fields.get("code") or "",
                        year=fields.get("year") or "",
                    )
                    _json(self, 202, payload)
                except Exception as exc:
                    _six_error(self, exc)
                return
            match = re.fullmatch(r"/api/six/reports/([0-9a-f]{32})/modules/([a-z_]+)/retry", path)
            if match:
                try:
                    payload = _six_facade().retry(
                        match.group(1), match.group(2), self._new_api_session()
                    )
                    _json(self, 202, payload)
                except Exception as exc:
                    _six_error(self, exc)
                return
            match = re.fullmatch(r"/api/six/reports/([0-9a-f]{32})/regenerate", path)
            if match:
                try:
                    _json(self, 202, _six_facade().regenerate(match.group(1), self._new_api_session()))
                except Exception as exc:
                    _six_error(self, exc)
                return
            if path == "/api/settings/model" and ctype.startswith("application/json"):
                try:
                    body = self._read_json_body(limit=8 * 1024)
                    _, changes = prepare_model_settings(body)
                    save_model_settings(changes)
                    _json(self, 200, public_model_settings())
                except ValueError as exc:
                    _json_api_error(
                        self,
                        ApiError(400, "INVALID_MODEL_SETTINGS", str(exc)),
                    )
                return
            if path == "/api/projects/complete-identity" and ctype.startswith("multipart/form-data"):
                try:
                    fields, pdf = _parse_multipart(self)
                    project = json.loads(fields.get("project") or "{}")
                    if not isinstance(project, dict):
                        raise ValueError("项目资料格式无效")
                    if pdf:
                        validate_pdf_upload(pdf)
                    try:
                        from project_completion import ProjectCompletionError, complete_project_identity
                    except ModuleNotFoundError:
                        from agent.project_completion import ProjectCompletionError, complete_project_identity
                    try:
                        result = complete_project_identity(project, pdf)
                    except ProjectCompletionError as exc:
                        _json(self, 422, {"error": {"message": str(exc)}})
                        return
                    _json(self, 200, result)
                except (ValueError, InvalidUpload) as exc:
                    _json(self, 400, {"error": {"message": str(exc)}})
                return
            if path == "/api/runs" and ctype.startswith("multipart/form-data"):
                try:
                    self._create_run_json()
                except InvalidUpload as exc:
                    _json_api_error(self, ApiError(400, exc.code, str(exc)))
                except ApiError as exc:
                    _json_api_error(self, exc)
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/cancel", path)
            if match and (ctype.startswith("application/json") or not ctype):
                try:
                    payload = self._api().cancel_run(
                        self._new_api_session(),
                        match.group(1),
                    )
                    _json(self, 200, payload)
                except ApiError as exc:
                    _json_api_error(self, exc)
                return
            match = re.fullmatch(r"/api/runs/([^/]+)/(ask|search)", path)
            if match and ctype.startswith("application/json"):
                try:
                    body = self._read_json_body()
                    session_id = self._new_api_session()
                    if match.group(2) == "ask":
                        out = self._api().ask(
                            session_id,
                            match.group(1),
                            body.get("question"),
                        )
                    else:
                        out = self._api().search(
                            session_id,
                            match.group(1),
                            body.get("query"),
                            top_k=body.get("top_k") or 8,
                        )
                    _json(self, 200, out)
                except ApiError as exc:
                    _json_api_error(self, exc)
                return
            if path == "/api/ask" and ctype.startswith("application/json"):
                self._ask_json()
                return
            if path == "/api/search" and ctype.startswith("application/json"):
                self._search_json()
                return
            fields, pdf = _parse_multipart(self)
            if path == "/api/prefill":
                if not pdf:
                    _json(self, 400, {"error": "缺少 PDF"})
                    return
                validate_pdf_upload(pdf)
                _json(self, 200, guess_identity(pdf))
                return
            if path == "/api/announcement":
                if not pdf:
                    _json(self, 400, {"error": "缺少 PDF"})
                    return
                validate_pdf_upload(pdf)
                if not _ANALYZE_LOCK.acquire(blocking=False):
                    _json(self, 429, {"error": "已有分析任务在进行中，请稍后再试"})
                    return
                try:
                    _json(
                        self,
                        200,
                        run_announcement_pipeline(
                            pdf,
                            fields.get("event_type", "auto"),
                            ocr=fields.get("ocr") == "true",
                        ),
                    )
                finally:
                    _ANALYZE_LOCK.release()
                return
            if path != "/api/analyze":
                _json(self, 404, {"error": "not found"})
                return
            if not pdf:
                _json(self, 400, {"error": "缺少 PDF"})
                return
            validate_pdf_upload(pdf)
            code = (fields.get("code") or "").strip()
            name = (fields.get("name") or "").strip()
            year_raw = (fields.get("year") or "").strip()
            draft = fields.get("draft") or ""
            auto = guess_identity(pdf)
            if not code:
                code = auto.get("code") or ""
            if not name:
                name = auto.get("name") or ""
            if not year_raw:
                year_raw = str(auto.get("year") or "")
            if not re.fullmatch(r"\d{6}", code):
                _json(
                    self,
                    400,
                    {"error": "未能从 PDF 识别证券代码，请检查文件或手动补充财报身份"},
                )
                return
            if not name:
                _json(
                    self,
                    400,
                    {"error": "未能从 PDF 识别公司名称，请检查文件或手动补充财报身份"},
                )
                return
            if not re.fullmatch(r"\d{4}", year_raw):
                _json(
                    self,
                    400,
                    {"error": "未能从 PDF 识别报告年度，请检查文件或手动补充财报身份"},
                )
                return
            if not _ANALYZE_LOCK.acquire(blocking=False):
                _json(self, 429, {"error": "已有分析任务在进行中，请稍后再试"})
                return
            try:
                _cleanup_work_dir()
                payload = run_pipeline(
                    pdf,
                    code,
                    name,
                    int(year_raw),
                    draft=draft or None,
                    comparison_blobs=fields.get("_comparison_pdfs", []),
                )
                payload["auto_detected"] = auto
                _json(self, 200, payload)
            finally:
                _ANALYZE_LOCK.release()
        except Exception as exc:
            traceback.print_exc()
            _json(self, 400, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            reset_model_session(token)


__all__ = [
    "APP_CSP",
    "FIN_SCOPE_CSP",
    "Handler",
    "LEGACY_CSP",
    "MAX_UPLOAD",
    "WEB_MIME_TYPES",
    "WEB_ROOT",
    "_RATE_CAPACITY",
    "_RATE_REFILL_PER_SEC",
    "_cleanup_work_dir",
    "_json",
    "_json_api_error",
    "_parse_multipart",
    "_rate_buckets",
    "_rate_limit",
    "_resolve_web_asset",
]
