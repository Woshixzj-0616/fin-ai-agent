"""把旧流水线使用的 Run 接口桥接到 B1 运行时。"""
from __future__ import annotations

import json
import hashlib
import threading
from pathlib import Path
from typing import Any

try:
    from runtime import RunStatus
    from runtime.events import timestamp_now
    from runtime.redaction import redact_result, redact_text, redact_value
except ModuleNotFoundError:
    from agent.runtime import RunStatus
    from agent.runtime.events import timestamp_now
    from agent.runtime.redaction import redact_result, redact_text, redact_value


_TERMINAL_EVENT_STATUSES = {
    "succeeded",
    "partial_failed",
    "failed",
    "cancelled",
    "expired",
}
_PRESENTATION = {
    "agent_loop_round": ("模型循环", "model"),
    "agent_loop_start": ("模型循环开始", "model"),
    "agent_loop_submit": ("提交结构化主张", "model"),
    "agent_tool_start": ("工具开始", "tools"),
    "agent_tool": ("工具调用", "tools"),
    "calculation": ("本地计算", "program"),
    "claim_gate": ("主张门控", "program"),
    "draft_loaded": ("草稿已加载", "program"),
    "event_extracted": ("公告事件已抽取", "program"),
    "extraction_channel": ("抽取通道", "parser"),
    "extraction_completed": ("抽取完成", "parser"),
    "extraction_dedup": ("证据去重", "program"),
    "extraction_failed": ("抽取失败", "parser"),
    "period_header_carry": ("沿用跨页表头", "parser"),
    "file_read": ("读取文件", "program"),
    "http_error": ("材料请求失败", "program"),
    "http_request": ("请求材料", "program"),
    "http_response": ("材料响应", "program"),
    "llm_chat": ("模型请求", "model"),
    "llm_chat_tools": ("模型工具请求", "model"),
    "llm_request": ("模型请求", "model"),
    "llm_response": ("模型响应摘要", "model"),
    "llm_response_tools": ("模型工具响应摘要", "model"),
    "llm_retry": ("模型重试", "model"),
    "loop_fallback": ("模型回退", "model"),
    "loop_repair": ("协议修复", "model"),
    "loop_submit_failed": ("结构化提交失败", "model"),
    "material_registered": ("登记材料", "program"),
    "model_parse": ("模型计划摘要", "model"),
    "pipeline_completed": ("结果准备", "runtime"),
    "quarter_dedup": ("季度证据去重", "program"),
    "run_finished": ("运行结束", "runtime"),
    "run_started": ("开始运行", "runtime"),
    "structured_sample_check": ("结构化样例检查", "program"),
    "supplemental_check": ("本地补充核查", "program"),
    "text_claim_checked": ("主张裁决", "program"),
    "tool_summary": ("工具汇总", "tools"),
}


def _safe_text(value: Any, *, limit: int = 800) -> str:
    return redact_text(value, limit=limit)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    return redact_value(value, depth=depth, max_depth=4, max_items=50)


def _plan_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    fields = (
        "claim_id",
        "sentence_id",
        "claim_type",
        "company_name",
        "company_b",
        "period_year",
        "metric",
        "scope",
        "operator",
        "is_forecast",
        "ambiguity",
        "plain_claim",
        "verification_action",
        "period_kind", "kind", "value", "unit", "direction", "currency", "qualifier",
    )
    output = {
        key: _safe_value(item.get(key))
        for key in fields
        if item.get(key) is not None
    }
    if "plain_claim" in output:
        output["plain_claim"] = _safe_text(output["plain_claim"], limit=160)
    if item.get("quote"):
        output["original_sentence"] = redact_text(item["quote"])
    return output


def _event_payload(kind: str, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    payload = dict(data)
    evidence_ids = payload.pop("evidence_ids", [])
    if not isinstance(evidence_ids, list):
        evidence_ids = []
    evidence_ids = [
        _safe_text(item, limit=240)
        for item in evidence_ids
        if isinstance(item, (str, int, float))
    ][:50]

    if kind == "model_parse":
        parsed = payload.get("parsed") if isinstance(payload.get("parsed"), dict) else {}
        items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
        unclaimed = (
            parsed.get("unclaimed_sentences")
            if isinstance(parsed.get("unclaimed_sentences"), list)
            else []
        )
        return (
            {
                "claim_count": len(items),
                "unclaimed_count": len(unclaimed),
                "plan": [_plan_item(item) for item in items[:60]],
            },
            evidence_ids,
        )

    return _safe_value(payload), evidence_ids


def _event_meta(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    title, actor = _PRESENTATION.get(kind, (kind.replace("_", " "), "legacy"))
    if kind == "model_parse":
        return title, f"{payload.get('claim_count', 0)} 条主张 · 未归属 {payload.get('unclaimed_count', 0)} 句"
    if kind == "calculation":
        operation = payload.get("operation") or payload.get("formula")
        return title, f"操作：{operation}" if operation else "本地规则计算完成"
    if kind in {"agent_tool_start", "agent_tool"}:
        tool = payload.get("tool") or "工具"
        status = payload.get("status") or ("starting" if kind == "agent_tool_start" else "completed")
        return title, f"{tool} · {status}"
    if kind == "file_read":
        path = str(payload.get("path") or "")
        name = Path(path).name if path else "文件"
        size = payload.get("size_bytes")
        return title, f"{name} · {size} bytes" if size is not None else name
    if kind == "extraction_completed":
        return title, f"形成 {payload.get('evidence_count', 0)} 条证据"
    if kind == "text_claim_checked":
        return title, f"{payload.get('claim_id', '主张')} · {payload.get('status', '已裁决')}"
    if kind == "run_finished":
        return title, f"运行终态：{payload.get('status', 'succeeded')}"
    if kind == "pipeline_completed":
        return title, "计算完成，正在保存完整结果"
    return title, "已记录本步实际输入、输出与依据"


class RuntimeRunBridge:
    """兼容旧 Run 的常用方法与事件列表，实际事件进入 RunContext。"""

    def __init__(self, context):
        self.context = context
        self.id = context.id
        self.root = context.root
        self.folder = context.folder
        self.command = context.command
        self._events: list[str] = []
        self._detail_lock = threading.Lock()

    @property
    def events(self) -> list[str]:
        return list(self._events)

    def output(self, name: str) -> Path:
        return self.context.output(name)

    def event(self, kind: str, **data: Any) -> None:
        raw = dict(data)
        raw_status = raw.pop("status", None)
        payload, evidence_ids = _event_payload(kind, raw)
        title, summary = _event_meta(kind, payload)
        actor = _PRESENTATION.get(kind, (title, "legacy"))[1]
        status = "completed"
        if raw_status in _TERMINAL_EVENT_STATUSES or raw_status == "running":
            status = str(raw_status)
        elif raw_status is not None:
            payload["status"] = _safe_value(raw_status)
        event = self.context.event(
            kind,
            actor=actor,
            title=title,
            summary=summary,
            status=status,
            evidence_ids=evidence_ids,
            payload=payload,
        )
        complete = redact_result(dict(data))
        if kind == "model_parse":
            parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else {}
            complete = {"claim_count": len(parsed.get("items") or []),
                        "plan": [_plan_item(item) for item in parsed.get("items") or []],
                        "unclaimed_sentence_ids": parsed.get("unclaimed_sentences") or [],
                        "input_sentences": [{"sentence_id": row.get("sentence_id"), "original_sentence": row.get("text")}
                                            for row in data.get("sentences") or [] if isinstance(row, dict)]}
        self._append(kind, complete, title=title, summary=event.summary, timestamp=event.timestamp, seq=event.seq)

    def read(self, path: Path) -> bytes:
        blob = self.context.read(path)
        payload = {
            "path": str(Path(path).resolve()),
            "size_bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
        title, _ = _event_meta("file_read", payload)
        records = [json.loads(line) for line in self.context.events]
        record = next((item for item in reversed(records) if item.get("event_type") == "file_read"
                       and item.get("payload", {}).get("path") == payload["path"]), {})
        self._append("file_read", payload, title=title, summary=f"{Path(path).name} · {len(blob)} bytes",
                     seq=record.get("seq"), timestamp=record.get("timestamp"))
        return blob

    def finish(self, *, snapshot_prefix=None, status: str = "succeeded", **summary: Any) -> bool:
        safe_summary = _safe_value(dict(summary))
        # 公告抽取沿用 completed/no_fields，运行接口使用统一终态。
        runtime_status = {"completed": "succeeded", "no_fields": "partial_failed"}.get(status, status)
        prepared = self.context.prepare_finish(RunStatus.coerce(runtime_status), **safe_summary)
        if prepared:
            self.event("pipeline_completed", requested_status=runtime_status, **safe_summary)
        return prepared

    def _append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        title: str = "",
        summary: str = "",
        timestamp: str | None = None,
        seq: int | None = None,
    ) -> None:
        record = redact_result({
                    "time": timestamp or timestamp_now(),
                    "run_id": self.id,
                    "seq": seq,
                    "event": kind,
                    **payload,
                    "title": title or kind,
                    "summary": summary,
                })
        line = json.dumps(record, ensure_ascii=False)
        with self._detail_lock:
            self._events.append(line)
            with self.output("execution_details.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
