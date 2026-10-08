"""Adapter for the current profitability-analysis implementation."""

from __future__ import annotations

from typing import Any

from backend.core.context import ReportContext
from modules.part2_profit.pipeline import analyze_profit_report
from backend.pdf_reader import select_initial_pages


MODULE_ID = "profit"
MODULE_VERSION = "模块二_盈利来源与变化_v2"


def run(context: ReportContext) -> dict[str, Any]:
    initial_pages = context.initial_pages or select_initial_pages(context.pages, analysis_module=MODULE_ID)
    workflow = analyze_profit_report(
        file_name=context.file_name,
        page_count=context.page_count,
        initial_pages=initial_pages,
        get_page=context.read_page,
        search_pages=context.search_pages,
        business_context=context.related_results.get("business"),
        on_tool=context.tool_activity,
        on_stage=context.progress,
        recorder=context.recorder,
    )
    result = workflow.get("result", {})
    lines = result.get("profit_lines", [])
    metrics = result.get("profit_metrics", [])
    for metric in metrics:
        name = str(metric.get("name", ""))
        if name == "营业毛利率":
            formula = "(营业收入-营业成本)/营业收入"
        elif name.endswith("费用率"):
            formula = "对应费用/营业收入"
        else:
            formula = str(metric.get("formula") or "见输出中的计算来源行及源代码提交")
        context.record_calculation(
            name or "盈利指标",
            inputs=metric.get("source_rows", lines),
            formula=formula,
            output=metric,
            rule_version=MODULE_VERSION,
        )
    bridge = result.get("profit_bridge") or {}
    if bridge:
        context.record_calculation(
            "利润变化贡献桥",
            inputs=bridge.get("detail_rows", lines),
            formula=str(bridge.get("formula") or "见利润桥规则与输入行"),
            output=bridge,
            rule_version=MODULE_VERSION,
        )
    for check in result.get("profit_checks", []):
        context.record_calculation(
            str(check.get("name") or "利润勾稽"),
            inputs=check.get("source_rows", lines),
            formula=str(check.get("formula") or "见利润勾稽规则与输入行"),
            output=check,
            rule_version=MODULE_VERSION,
        )
    for segment in result.get("business_segments", []):
        if segment.get("current_gross_margin") or segment.get("previous_gross_margin"):
            context.record_calculation(
                f"{segment.get('segment_name', '分业务')}毛利率",
                inputs=segment.get("evidence_segments", []),
                formula="(业务收入-业务成本)/业务收入；期间、范围、币种及证据通过后复算",
                output=segment,
                rule_version=MODULE_VERSION,
            )
    context.recorder.save_artifact(
        "profit_calculated_result",
        {
            "profit_lines": lines,
            "profit_metrics": metrics,
            "profit_bridge": bridge,
            "profit_checks": result.get("profit_checks", []),
            "business_segments": result.get("business_segments", []),
            "analysis_blocks": result.get("analysis_blocks", []),
            "fact_revision_log": result.get("fact_revision_log", []),
        },
    )
    return workflow
