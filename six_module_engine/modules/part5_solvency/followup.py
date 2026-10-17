"""Module-five follow-up with its own guidance and annual-report retrieval tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from backend.core.legacy_runtime import TOOLS, _run_tool_loop


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MAX_HISTORY_TURNS = 6


def _module_context(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the module's full accounting evidence, calculations, and caveats in scope."""
    fields = (
        "module_id", "module_version", "analysis_status", "headline", "debt_structure",
        "maturity_profile", "funding_sources", "cash_availability", "support_metrics",
        "obligations", "data_coverage", "facts", "calculations", "findings",
        "additional_sections", "limitations", "handoff_questions", "evidence_review",
        "read_pages",
    )
    return {key: result.get(key) for key in fields if key in result}


def answer_solvency_question(
    *,
    file_name: str,
    page_count: int,
    initial_result: dict[str, Any],
    history: list[dict[str, Any]],
    question: str,
    get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
    on_tool: Callable[[str, str], None] | None = None,
    recorder: Any = None,
) -> dict[str, Any]:
    """Answer using this run's complete module-five result and searchable source pages."""
    guidance = (PROMPT_DIR / "专业指导_v1.md").read_text(encoding="utf-8")
    followup_guidance = (PROMPT_DIR / "追问指导_v1.md").read_text(encoding="utf-8")
    context = {
        "file_name": file_name,
        "page_count": page_count,
        "module_result": _module_context(initial_result),
        "recent_completed_questions": [
            {"question": str(turn.get("question") or "")[:1200],
             "answer": str(turn.get("answer") or "")[:1600]}
            for turn in history[-MAX_HISTORY_TURNS:]
            if turn.get("status") == "completed"
        ],
    }
    user_message = (
        "请基于当前年报和本次模块五已保存结果回答。金额、比率及失败原因以程序保存的事实和计算为准，"
        "不得自行补算、把缺失值当零、把文本匹配说成专业结论已证实。需新的原文依据时，先用搜索工具定位，"
        "再读取具体页；只引用实际读过的 PDF 页码。清楚区分年报披露、程序计算、分析推断及待复核事项。"
        "如果现有材料不能回答，明确说明缺少什么。\n\n完整模块上下文：\n"
        + json.dumps(context, ensure_ascii=False, default=str)[:34000]
        + "\n\n本轮追问：\n"
        + question[:1200]
    )
    messages = [
        {"role": "system", "content": guidance + "\n\n" + followup_guidance},
        {"role": "user", "content": user_message},
    ]

    def tool_activity(name: str, detail: str) -> None:
        if on_tool:
            on_tool(name, detail)

    content, trace, usage = _run_tool_loop(
        messages=messages,
        page_count=page_count,
        get_page=get_page,
        search_pages=search_pages,
        on_tool=tool_activity,
        available_tools=TOOLS[:2],
        max_rounds=3,
        recorder=recorder,
    )
    tool_pages = set(int(page) for page in usage.pop("_tool_pages", []))
    allowed_pages = set(int(page) for page in initial_result.get("read_pages", [])) | tool_pages
    cited_pages = sorted(set(int(page) for page in re.findall(r"(?:PDF\s*第\s*|第\s*)(\d+)\s*页", content)))
    unsupported_pages = [page for page in cited_pages if page not in allowed_pages]
    answer = content[:6000]
    if unsupported_pages:
        answer += "\n\n本轮有页码不在本次已读取范围内，程序无法确认这些出处；请打开原文核对。"
    return {
        "answer": answer,
        "trace": trace,
        "read_pages": sorted(tool_pages),
        "usage": usage,
        "unsupported_pages": unsupported_pages,
    }
