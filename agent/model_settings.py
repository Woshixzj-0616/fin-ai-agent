"""运行进程内的模型连接设置；浏览器提交的密钥不会写入磁盘。"""
from __future__ import annotations

import os
import re
import threading
from contextvars import ContextVar, Token
from urllib.parse import urlsplit


_LOCK = threading.RLock()
_OVERRIDES: dict[str, str] = {}
_SESSION_OVERRIDES: dict[str, dict[str, str]] = {}
_CURRENT_SESSION: ContextVar[str | None] = ContextVar("model_session", default=None)
_ALLOWED = {"api_key", "base_url", "model", "format"}
_SECRET_SHAPE = re.compile(r"(?i)(?:sk-[a-z0-9_-]{12,}|key\s*[:=]\s*\S{8,})")
_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEFAULT_MODEL = "deepseek-chat"


def activate_model_session(session_id: str) -> Token:
    """Bind one browser session to this request or background run."""
    return _CURRENT_SESSION.set(session_id)


def reset_model_session(token: Token) -> None:
    _CURRENT_SESSION.reset(token)


def _overrides(session_id: str | None = None) -> dict[str, str]:
    selected = session_id if session_id is not None else _CURRENT_SESSION.get()
    return _SESSION_OVERRIDES.get(selected, {}) if selected else _OVERRIDES


def current_model_settings() -> dict[str, str]:
    """返回调用模型客户端所需设置的快照。"""
    with _LOCK:
        overrides = _overrides()
        env_key = "" if os.environ.get("FIN_PUBLIC_MODE") == "1" else os.environ.get("LLM_API_KEY", "")
        return {
            "api_key": overrides.get("api_key", env_key),
            "base_url": overrides.get("base_url", os.environ.get("LLM_BASE_URL") or _DEFAULT_BASE_URL),
            "model": overrides.get("model", os.environ.get("LLM_MODEL") or _DEFAULT_MODEL),
            "format": overrides.get("format", os.environ.get("LLM_FORMAT") or "json_object"),
            "stream": os.environ.get("LLM_STREAM", ""),
        }


def public_model_settings(session_id: str | None = None) -> dict[str, object]:
    """返回状态与非敏感参数，不返回 API 密钥。"""
    token = activate_model_session(session_id) if session_id else None
    try:
        settings = current_model_settings()
    finally:
        if token is not None:
            reset_model_session(token)
    key = settings["api_key"]
    base_url = settings["base_url"]
    try:
        parts = urlsplit(base_url)
        if parts.username or parts.password or parts.query or parts.fragment:
            base_url = ""
        elif parts.scheme != "https" or not parts.hostname:
            base_url = ""
    except ValueError:
        base_url = ""
    with _LOCK:
        override = _overrides(session_id)
        source = "temporary" if override.get("api_key") else "environment" if key else "none"
    return {
        "configured": bool(
            key
            and base_url
            and settings["model"]
            and settings["format"] in {"json_schema", "json_object"}
            and key.isascii()
            and not re.search(r"\s", key)
            and not _SECRET_SHAPE.search(base_url + settings["model"])
        ),
        "source": source,
        "base_url": base_url,
        "model": settings["model"],
        "format": settings["format"] if settings["format"] in {"json_schema", "json_object"} else "json_schema",
        "has_temporary_key": bool(override.get("api_key")),
    }


def prepare_model_settings(payload: object) -> tuple[dict[str, str], dict[str, str]]:
    """合并并验证一次模型设置请求，尚不修改当前运行设置。"""
    if not isinstance(payload, dict):
        raise ValueError("模型设置必须是 JSON 对象")
    if set(payload) - (_ALLOWED | {"clear_key"}):
        raise ValueError("模型设置包含不支持的字段")
    current = current_model_settings()
    candidate = dict(current)
    changes: dict[str, str] = {}
    for name in ("base_url", "model", "format"):
        if name not in payload:
            continue
        value = payload[name]
        if not isinstance(value, str):
            raise ValueError(f"{name} 必须是文本")
        value = value.strip()
        limit = 512 if name == "base_url" else 120
        if len(value) > limit:
            raise ValueError(f"{name} 长度超过限制")
        candidate[name] = value
        changes[name] = value

    raw_key = payload.get("api_key", "")
    if not isinstance(raw_key, str):
        raise ValueError("API 密钥必须是文本")
    raw_key = raw_key.strip()
    if len(raw_key) > 4096:
        raise ValueError("API 密钥长度超过限制")
    if raw_key:
        candidate["api_key"] = raw_key
        changes["api_key"] = raw_key
    elif payload.get("clear_key") is True:
        candidate["api_key"] = ""
        changes["api_key"] = ""

    if candidate["format"] not in {"json_schema", "json_object"}:
        raise ValueError("结构化输出格式只能选择 json_schema 或 json_object")
    if _SECRET_SHAPE.search(candidate["base_url"] + candidate["model"]):
        raise ValueError("服务地址或模型名称疑似包含密钥")

    if candidate["base_url"] or candidate["model"] or candidate["api_key"]:
        try:
            parts = urlsplit(candidate["base_url"])
        except ValueError:
            raise ValueError("服务地址格式无效") from None
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment):
            raise ValueError("服务地址须为不含账号、查询参数或片段的 HTTPS 地址")
        if not candidate["model"]:
            raise ValueError("请填写模型名称")
        if candidate["api_key"] and (not candidate["api_key"].isascii() or re.search(r"\s", candidate["api_key"])):
            raise ValueError("API 密钥含空白或无效字符")

    return candidate, changes


def save_model_settings(changes: dict[str, str], session_id: str | None = None) -> None:
    """只更新当前 Python 进程内的覆盖值；空密钥恢复到环境变量配置。"""
    with _LOCK:
        selected = session_id if session_id is not None else _CURRENT_SESSION.get()
        overrides = _SESSION_OVERRIDES.setdefault(selected, {}) if selected else _OVERRIDES
        for name, value in changes.items():
            if name == "api_key" and not value:
                overrides.pop(name, None)
            else:
                overrides[name] = value
