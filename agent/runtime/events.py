"""运行事件契约：统一实时事件、磁盘事件和最终轨迹的结构。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from .redaction import redact_text, redact_value

TZ = timezone(timedelta(hours=8))


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @classmethod
    def coerce(cls, value: RunStatus | str) -> RunStatus:
        if isinstance(value, cls):
            return value
        aliases = {
            "ok": cls.SUCCEEDED,
            "partial_failure": cls.PARTIAL_FAILED,
            "error": cls.FAILED,
        }
        if value in aliases:
            return aliases[value]
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"未知运行状态：{value}") from exc


TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.PARTIAL_FAILED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.EXPIRED,
}


def timestamp_now() -> str:
    return datetime.now(TZ).isoformat(timespec="milliseconds")


def phase_for_event(event_type: str) -> str:
    """把历史事件名映射到前端稳定阶段。"""
    if event_type.startswith("run_"):
        return "run"
    if event_type == "source_snapshot":
        return "identity"
    if event_type == "pipeline_completed":
        return "report"
    if event_type == "event_extracted":
        return "extraction"
    if event_type.startswith(("pdf.", "identity")):
        return "identity"
    if event_type.startswith(("extract", "file_read", "period_header_carry")):
        return "extraction"
    if event_type.startswith("material_registered"):
        return "extraction"
    if event_type.startswith("validation"):
        return "validation"
    if event_type.startswith(("llm_", "model_", "agent_loop")):
        return "model"
    if event_type.startswith(("tool", "agent_tool")):
        return "tools"
    if event_type.startswith("calc"):
        return "calculation"
    if event_type.startswith("claim"):
        return "claim"
    if event_type.startswith("report"):
        return "report"
    return "system"


@dataclass(frozen=True, slots=True)
class RunEvent:
    run_id: str
    seq: int
    timestamp: str
    phase: str
    event_type: str
    actor: str = "runtime"
    title: str = ""
    summary: str = ""
    status: str = "completed"
    duration_ms: int | float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("run_id 不能为空")
        if not isinstance(self.seq, int) or self.seq < 1:
            raise ValueError("seq 必须为正整数")
        if not isinstance(self.timestamp, str) or not self.timestamp:
            raise ValueError("timestamp 不能为空")
        if not isinstance(self.phase, str) or not self.phase:
            raise ValueError("phase 不能为空")
        if not isinstance(self.event_type, str) or not self.event_type:
            raise ValueError("event_type 不能为空")
        if not isinstance(self.evidence_ids, list) or not all(
            isinstance(item, str) for item in self.evidence_ids
        ):
            raise ValueError("evidence_ids 必须是字符串列表")
        if not isinstance(self.payload, dict):
            raise TypeError("payload 必须是对象")
        object.__setattr__(self, "title", redact_text(self.title))
        object.__setattr__(self, "summary", redact_text(self.summary))
        object.__setattr__(
            self,
            "evidence_ids",
            [redact_text(item) for item in self.evidence_ids],
        )
        object.__setattr__(self, "payload", redact_value(self.payload))
        try:
            json.dumps(self.payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload 必须可 JSON 序列化") from exc

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        seq: int,
        timestamp: str,
        phase: str,
        event_type: str,
        actor: str = "runtime",
        title: str = "",
        summary: str = "",
        status: str = "completed",
        duration_ms: float | None = None,
        evidence_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        return cls(
            run_id=run_id,
            seq=seq,
            timestamp=timestamp,
            phase=phase,
            event_type=event_type,
            actor=actor,
            title=title or event_type,
            summary=summary,
            status=status,
            duration_ms=duration_ms,
            evidence_ids=list(evidence_ids or []),
            payload=dict(payload or {}),
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RunEvent:
        if not isinstance(raw, dict):
            raise TypeError("事件必须是 JSON 对象")
        return cls(
            run_id=raw["run_id"],
            seq=raw["seq"],
            timestamp=raw["timestamp"],
            phase=raw["phase"],
            event_type=raw["event_type"],
            actor=raw.get("actor", "runtime"),
            title=raw.get("title", raw["event_type"]),
            summary=raw.get("summary", ""),
            status=raw.get("status", "completed"),
            duration_ms=raw.get("duration_ms"),
            evidence_ids=list(raw.get("evidence_ids") or []),
            payload=dict(raw.get("payload") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "event_type": self.event_type,
            "actor": self.actor,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "evidence_ids": list(self.evidence_ids),
            "payload": dict(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
