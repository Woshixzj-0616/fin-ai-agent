"""Adapter for the current business-analysis implementation."""

from __future__ import annotations

from typing import Any

from backend.core.context import ReportContext
from backend.deepseek_agent import analyze_report
from backend.pdf_reader import select_initial_pages


MODULE_ID = "business"
MODULE_VERSION = "模块一_业务与经营背景_v2"


def run(context: ReportContext) -> dict[str, Any]:
    initial_pages = context.initial_pages or select_initial_pages(context.pages, analysis_module=MODULE_ID)
    workflow = analyze_report(
        file_name=context.file_name,
        page_count=context.page_count,
        initial_pages=initial_pages,
        get_page=context.read_page,
        search_pages=context.search_pages,
        on_tool=context.tool_activity,
        on_stage=context.progress,
        recorder=context.recorder,
        analysis_module=MODULE_ID,
    )
    result = workflow.get("result", {})
    for item in [*result.get("revenue_totals", []), *result.get("revenue_segments", [])]:
        change = item.get("calculated_change")
        if change:
            context.record_calculation(
                "业务收入期间变化",
                inputs={
                    key: item.get(key)
                    for key in (
                        "metric_name", "current_value", "current_unit", "current_period",
                        "previous_value", "previous_unit", "previous_period",
                        "reporting_scope", "currency", "source_pages", "evidence_quote",
                    )
                },
                formula="收入差额=本期金额换算为元-上期金额换算为元；同比仅在可比期间、口径与正基数条件满足时计算",
                output=change,
                rule_version=workflow.get("result", {}).get("prompt_version", MODULE_VERSION),
            )
    if result:
        context.recorder.save_artifact(
            "business_calculated_result",
            {
                "revenue_totals": result.get("revenue_totals", []),
                "revenue_segments": result.get("revenue_segments", []),
                "coverage_checks": result.get("coverage_checks", []),
            },
        )
    return workflow
