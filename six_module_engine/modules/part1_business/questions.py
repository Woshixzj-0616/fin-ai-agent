"""Follow-up handling for the business-analysis module."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from backend.core.legacy_runtime import TOOLS, _run_tool_loop
from modules.part1_business.prompts import business_prompt


def answer_business_question(
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
    business_analysis = {
        "business_summary": initial_result.get("business_summary", ""),
        "business_flow": initial_result.get("business_flow", []),
        "revenue_total": initial_result.get("revenue_total", {}),
        "revenue_totals": initial_result.get("revenue_totals", []),
        "revenue_metric_bridges": initial_result.get("revenue_metric_bridges", []),
        "revenue_segments": initial_result.get("revenue_segments", []),
        "growth_drivers": initial_result.get("growth_drivers", []),
        "industry_context": initial_result.get("industry_context", []),
        "strategy_competitiveness": initial_result.get("strategy_competitiveness", []),
        "interpretation_review_items": initial_result.get("interpretation_review_items", []),
        "topics": initial_result.get("topics", []),
        "dependencies": initial_result.get("dependencies", []),
        "risk_factors": initial_result.get("risk_factors", []),
        "major_changes": initial_result.get("major_changes", []),
        "industry_metrics": initial_result.get("industry_metrics", []),
        "follow_up_checks": initial_result.get("follow_up_checks", []),
        "coverage_checks": initial_result.get("coverage_checks", []),
        "uncertainties": initial_result.get("uncertainties", []),
    }
    context = {
        "file_name": file_name,
        "page_count": page_count,
        "company": initial_result.get("company", ""),
        "period": initial_result.get("period", ""),
        "business_analysis": business_analysis,
        "already_read_pages": initial_result.get("read_pages", []),
        "recent_questions_and_answers": [
            {"question": turn.get("question", ""), "answer": turn.get("answer", "")[:1200]}
            for turn in history[-6:]
            if turn.get("status") == "completed"
        ],
    }
    messages = [
        {"role": "system", "content": business_prompt("question")},
        {
            "role": "user",
            "content": (
                "请围绕模块一结果回答业务、经营和收入结构问题；若用户询问盈利、资产、现金或债务判断，"
                "说明需要后续模块并指出应查资料。已知任务信息如下；只能把明确列出的来源页作为已有证据，"
                "需要新证据时使用工具查阅全文。简明回答，区分原文事实与推断，引用 PDF 页码，证据不足要明说。\n\n"
                + f"任务上下文 JSON：{json.dumps(context, ensure_ascii=False)}\n\n"
                + f"本轮追问：{question[:1200]}"
            ),
        },
    ]
    content, trace, usage = _run_tool_loop(
        messages=messages,
        page_count=page_count,
        get_page=get_page,
        search_pages=search_pages,
        on_tool=on_tool,
        available_tools=TOOLS[:2],
        recorder=recorder,
    )
    initial_pages = set(int(page) for page in initial_result.get("read_pages", []))
    tool_pages = set(usage.pop("_tool_pages", []))
    allowed_pages = initial_pages | tool_pages
    citations = sorted(set(int(page) for page in re.findall(r"(?:PDF\s*第\s*|第\s*)(\d+)\s*页", content)))
    invalid_citations = [page for page in citations if page not in allowed_pages]
    if invalid_citations:
        content += "\n\n有引用页码不在本次已读取范围内，程序无法确认这些出处；请人工打开原文复核。"
    return {
        "answer": content[:6000],
        "trace": trace,
        "read_pages": sorted(tool_pages),
        "usage": usage,
        "unsupported_pages": invalid_citations,
    }
