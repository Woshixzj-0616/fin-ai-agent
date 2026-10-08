"""模块三追问：每次带入本次分析、专业指导，并继续调用年报工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.deepseek_client import ModelCallError
from modules.part3_assets.agent import (
    ANALYSIS_TOOLS,
    MAX_TOOL_STEP_TOKENS,
    PROMPT_ROOT,
    _add_usage,
    _dispatch_tool,
    _invoke,
    _response_content,
    _safe_error,
    _usage,
)

MAX_FOLLOWUP_ROUNDS = 3


def _followup_prompt() -> str:
    return (PROMPT_ROOT / "模块三追问_v1.md").read_text(encoding="utf-8")


def answer_asset_question(
    context: Any,
    result: dict[str, Any],
    question: str,
    history: list[dict[str, Any]],
) -> tuple[str, list[int], bool]:
    """Answer against the current module result and allow limited source lookups/calculations."""
    professional = (PROMPT_ROOT / "模块三专业指导_v1.md").read_text(encoding="utf-8")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": professional + "\n\n" + _followup_prompt()},
        {
            "role": "user",
            "content": (
                "以下是当前这次模块三分析的完整结果和报告身份。回答后续问题时以此为上下文；"
                "需要时可搜索、读取原年报页或调用程序计算工具。\n\n"
                + json.dumps(result, ensure_ascii=False, default=str)
            ),
        },
    ]
    for turn in history[-6:]:
        old_question = str(turn.get("question") or "").strip()
        old_answer = str(turn.get("answer") or "").strip()
        if old_question and old_answer:
            messages.extend([
                {"role": "user", "content": old_question[:2400]},
                {"role": "assistant", "content": old_answer[:7000]},
            ])
    messages.append({"role": "user", "content": question})

    facts = result.get("facts") if isinstance(result.get("facts"), list) else []
    calculations = result.get("calculations") if isinstance(result.get("calculations"), list) else []
    pages_read: set[int] = set()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    answer = ""
    initial_counts = (len(facts), len(calculations))

    try:
        for round_index in range(MAX_FOLLOWUP_ROUNDS):
            response = _invoke(
                context,
                messages,
                tools=ANALYSIS_TOOLS,
                max_tokens=MAX_TOOL_STEP_TOKENS,
                thinking="enabled",
            )
            usage = _add_usage(usage, _usage(response))
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ModelCallError("DeepSeek 没有返回模块三追问结果。")
            assistant = choices[0].message
            tool_calls = getattr(assistant, "tool_calls", None) or []
            messages.append(assistant.model_dump(exclude_none=True))
            if not tool_calls:
                answer = _response_content(response).strip()
                break

            for tool_call in tool_calls:
                function = tool_call.function
                arguments = json.loads(function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须为对象。")
                output = _dispatch_tool(
                    function.name,
                    arguments,
                    context=context,
                    facts=facts,
                    known_calculations=calculations,
                    pages_read=pages_read,
                )
                if function.name == "read_pdf_page" and output.get("page"):
                    pages_read.add(int(output["page"]))
                elif function.name == "submit_asset_fact" and isinstance(output.get("fact"), dict):
                    for evidence in output["fact"].get("evidence", []):
                        if evidence.get("page") is not None:
                            pages_read.add(int(evidence["page"]))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(output, ensure_ascii=False, default=str)[:18_000],
                })

            if round_index == MAX_FOLLOWUP_ROUNDS - 1:
                messages.append({
                    "role": "user",
                    "content": "原文查阅轮数已到上限。请现在直接回答用户问题，只引用已经读过的年报内容、当前模块结果和程序计算；不补造数字，并明确仍待复核的地方。",
                })
                final_response = _invoke(context, messages, max_tokens=MAX_TOOL_STEP_TOKENS, thinking="disabled")
                usage = _add_usage(usage, _usage(final_response))
                answer = _response_content(final_response).strip()
        if not answer:
            raise ModelCallError("DeepSeek 没有生成可显示的追问回答。")
    except Exception as exc:
        raise _safe_error(exc) from exc

    result["facts"] = facts
    result["calculations"] = calculations
    result["followup_usage"] = _add_usage(result.get("followup_usage", {}), usage)
    changed = (len(facts), len(calculations)) != initial_counts
    context.save_artifact("assets_followup_latest", {
        "question": question,
        "answer": answer,
        "source_pages": sorted(pages_read),
        "usage": usage,
        "fact_count": len(facts),
        "calculation_count": len(calculations),
    })
    return answer, sorted(pages_read), changed
