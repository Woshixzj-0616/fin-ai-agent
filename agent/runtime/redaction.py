"""运行数据的统一脱敏入口。

事件文件、运行状态、桥接 payload 和公开响应都从这里取规则，避免每个模块
各自维护一套不完整的关键字黑名单。
"""
from __future__ import annotations

import re
from typing import Any

REDACTED_SECRET = "[已隐藏凭证]"
REDACTED_FIELD = "[已隐藏字段]"

_FORBIDDEN_FIELDS = frozenset(
    {
        "chain_of_thought",
        "content",
        "content_preview",
        "context_quote",
        "messages",
        "parsed",
        "prompt",
        "quote",
        "raw",
        "raw_response",
        "reasoning",
        "reasoning_content",
        "thinking_content",
        "internal_reasoning",
        "thoughts",
        "sentences",
        "system_prompt",
        "thinking",
    }
)

_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTHORIZATION_BEARER = re.compile(
    r"(?i)(\bAuthorization\s*:\s*Bearer\s+)([^\s,;]+)"
)
_ENV_SECRET = re.compile(
    r"(\b(?:[A-Z0-9]+_)+(?:API_KEY|TOKEN|SECRET)\b\s*[:=]\s*)"
    r"([\"']?)([^\"'\s,;]+)"
)
_TOKEN_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
)

SECRET_PATTERNS = (
    ("private_key", _PEM_PRIVATE_KEY),
    ("authorization_bearer", _AUTHORIZATION_BEARER),
    ("environment_secret", _ENV_SECRET),
    ("openai_key", _TOKEN_PATTERNS[0]),
    ("github_token", _TOKEN_PATTERNS[1]),
    ("github_pat", _TOKEN_PATTERNS[2]),
    ("aws_access_key", _TOKEN_PATTERNS[3]),
    ("google_api_key", _TOKEN_PATTERNS[4]),
)


def redact_text(value: Any, *, limit: int | None = None) -> str:
    """隐藏常见凭证，同时保留正常财务文本和结构化标识。"""
    text = str(value)
    text = _PEM_PRIVATE_KEY.sub(REDACTED_SECRET, text)
    text = _AUTHORIZATION_BEARER.sub(
        lambda match: match.group(1) + REDACTED_SECRET,
        text,
    )
    text = _ENV_SECRET.sub(
        lambda match: match.group(1) + match.group(2) + REDACTED_SECRET,
        text,
    )
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED_SECRET, text)
    if limit is not None and limit >= 0 and len(text) > limit:
        if limit == 0:
            return ""
        return text[: limit - 1] + "…"
    return text


def redact_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 8,
    max_items: int | None = 100,
    max_string: int | None = 2000,
) -> Any:
    """递归清理公开数据；禁止字段直接移除，字符串统一走凭证规则。"""
    if depth > max_depth:
        return {"truncated": True}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value, limit=max_string)
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            if key.lower() in _FORBIDDEN_FIELDS:
                continue
            output[key] = redact_value(
                raw_item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
            )
        return output
    if isinstance(value, (list, tuple)):
        return [
            redact_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
            )
            for item in list(value)[:max_items]
        ]
    return redact_text(value, limit=240)


def redact_result(value: Any) -> Any:
    """完整业务产物只脱敏，不套用事件预览的条数和文本长度限制。"""
    return redact_value(value, max_depth=32, max_items=None, max_string=None)


__all__ = [
    "SECRET_PATTERNS",
    "REDACTED_FIELD",
    "REDACTED_SECRET",
    "redact_text",
    "redact_value",
    "redact_result",
]
