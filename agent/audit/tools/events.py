"""JSON / native 工具循环共用的开始、完成事件与执行包装。"""
from __future__ import annotations

from time import perf_counter

from audit.tools.dispatch import dispatch


def emit_tool_start(run, *, round_no: int, call_id: str, protocol: str, tool: str,
                    arguments: dict, budget_before: int) -> None:
    run.event(
        "agent_tool_start",
        round=round_no,
        call_id=call_id,
        protocol=protocol,
        tool=tool,
        arguments=arguments,
        budget_before=budget_before,
    )


def emit_tool_complete(run, *, round_no: int, call_id: str, protocol: str, tool: str,
                       status: str, budget_left: int, duration_ms: float,
                       arguments: dict, result: dict, evidence_ids: list) -> None:
    run.event(
        "agent_tool",
        round=round_no,
        call_id=call_id,
        protocol=protocol,
        tool=tool,
        status=status,
        budget_left=budget_left,
        duration_ms=duration_ms,
        arguments=arguments,
        result=result,
        evidence_ids=evidence_ids,
    )


def execute_tool_call(*, run, round_no: int, call_id: str, protocol: str, tool: str,
                      arguments: dict, facts: list[dict], document_texts: dict | None,
                      draft: str, budget_before: int) -> tuple[dict, bool]:
    """执行一次业务工具，并保证开始/完成事件成对。

    返回 ``(result, consumed)``。预算耗尽也发完整事件，但不登记为实际调用。
    """
    emit_tool_start(
        run,
        round_no=round_no,
        call_id=call_id,
        protocol=protocol,
        tool=tool,
        arguments=arguments,
        budget_before=budget_before,
    )
    if budget_before <= 0:
        result = {"status": "error", "error": "工具调用预算已用尽"}
        emit_tool_complete(
            run,
            round_no=round_no,
            call_id=call_id,
            protocol=protocol,
            tool=tool,
            status="budget_exhausted",
            budget_left=0,
            duration_ms=0.0,
            arguments=arguments,
            result=result,
            evidence_ids=[],
        )
        return result, False

    started = perf_counter()
    try:
        result = dispatch(tool, facts, arguments, document_texts=document_texts, draft=draft)
    except Exception as exc:
        emit_tool_complete(
            run,
            round_no=round_no,
            call_id=call_id,
            protocol=protocol,
            tool=tool,
            status="error",
            budget_left=budget_before,
            duration_ms=round((perf_counter() - started) * 1000, 3),
            arguments=arguments,
            result={"status": "error", "error": str(exc)},
            evidence_ids=[],
        )
        raise
    status = result.get("status") or result.get("verdict") or "ok"
    emit_tool_complete(
        run,
        round_no=round_no,
        call_id=call_id,
        protocol=protocol,
        tool=tool,
        status=status,
        budget_left=budget_before - 1,
        duration_ms=round((perf_counter() - started) * 1000, 3),
        arguments=arguments,
        result=result,
        evidence_ids=result.get("evidence_ids", []),
    )
    return result, True
