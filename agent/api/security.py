"""Web API 的会话、客户端地址和上传安全边界。"""
from __future__ import annotations

import ipaddress
import re
import secrets
from http.cookies import SimpleCookie

import pymupdf

SESSION_COOKIE = "fin_session"
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")


class InvalidUpload(ValueError):
    """上传文件未通过统一边界。"""

    code = "INVALID_UPLOAD"


def new_session_id() -> str:
    return secrets.token_hex(16)


def parse_session_cookie(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:  # noqa: BLE001 - Cookie 解析失败按无会话处理
        return None
    morsel = cookie.get(SESSION_COOKIE)
    value = morsel.value if morsel is not None else ""
    return value if SESSION_RE.fullmatch(value) else None


def session_cookie_header(session_id: str, *, secure: bool = False) -> str:
    if not SESSION_RE.fullmatch(session_id):
        raise ValueError("session_id 格式不合法")
    parts = [
        f"{SESSION_COOKIE}={session_id}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=604800",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def resolve_client_ip(
    peer_ip: str,
    forwarded_for: str | None = None,
    *,
    trusted_hops: int = 0,
) -> str:
    """默认使用 socket peer；启用受信代理解析时从右侧取链路。"""
    peer = _valid_ip(str(peer_ip or "").strip()) or "unknown"
    try:
        hops = int(trusted_hops)
    except (TypeError, ValueError):
        hops = 0
    if hops <= 0:
        return peer
    parts = [part.strip() for part in str(forwarded_for or "").split(",") if part.strip()]
    if len(parts) < hops:
        return peer
    candidate = _valid_ip(parts[-hops])
    return candidate or peer


def validate_pdf_upload(blob: bytes, *, max_bytes: int = 40 * 1024 * 1024) -> dict:
    """检查体积、魔数、加密与解析错误，返回最小 PDF 元数据。"""
    if not isinstance(blob, bytes) or not blob:
        raise InvalidUpload("上传内容为空")
    if len(blob) > max_bytes:
        raise InvalidUpload(f"上传内容超过 {max_bytes // (1024 * 1024)}MiB")
    if not blob.startswith(b"%PDF-"):
        raise InvalidUpload("上传内容不是 PDF 文件")
    try:
        with pymupdf.open(stream=blob, filetype="pdf") as document:
            if document.needs_pass:
                raise InvalidUpload("PDF 已加密，无法进入分析流水线")
            if document.page_count < 1:
                raise InvalidUpload("PDF 没有可分析页面")
            return {
                "page_count": document.page_count,
                "pdf_repaired_by_parser": document.is_repaired,
            }
    except InvalidUpload:
        raise
    except Exception as exc:
        raise InvalidUpload("PDF 无法解析或已损坏") from exc
