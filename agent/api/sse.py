"""SSE 帧编码与断点游标解析。"""
from __future__ import annotations

import json
from typing import Any


def parse_last_event_id(raw: str | None) -> int:
    if raw is None or str(raw).strip() == "":
        return 0
    text = str(raw).strip()
    if not text.isdigit():
        raise ValueError("Last-Event-ID 必须为非负整数")
    value = int(text)
    if value < 0:
        raise ValueError("Last-Event-ID 必须为非负整数")
    return value


def encode_sse(event: Any, *, wire_event: str | None = None) -> str:
    payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
    event_type = wire_event or str(payload.get("event_type") or "message")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {payload['seq']}\nevent: {event_type}\ndata: {data}\n\n"


def heartbeat_frame(comment: str = "keep-alive") -> str:
    return f": {comment}\n\n"
