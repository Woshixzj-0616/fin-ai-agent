"""Adapter for the current business-analysis implementation."""

from __future__ import annotations

from typing import Any

from backend.core.context import ReportContext
from modules.part1_business.facts import attach_table_identities
from modules.part1_business.workflow import analyze_business_report
from backend.pdf_reader import select_initial_pages


MODULE_ID = "business"
MODULE_VERSION = "模块一_业务与经营背景_v3.0-dev"


def run(context: ReportContext) -> dict[str, Any]:
    initial_pages = context.initial_pages or select_initial_pages(context.pages, analysis_module=MODULE_ID)
    workflow = analyze_business_report(
        file_name=context.file_name,
        page_count=context.page_count,
        initial_pages=initial_pages,
        get_page=context.read_page,
        search_pages=context.search_pages,
        on_tool=context.tool_activity,
        on_stage=context.progress,
        recorder=context.recorder,
    )
    result = workflow.get("result", {})
    if isinstance(result, dict):
        result.setdefault("prompt_version", MODULE_VERSION)
        attach_table_identities(result, context.report_id)
    for item in [*result.get("revenue_totals", []), *result.get("revenue_segments", [])]:
        change = item.get("calculated_change")
        if change:
            context.record_calculation(
                "业务收入期间变化",
                inputs={
                    key: item.get(key)
                    for key in (
                        "calculation_ids", "calculation_evidence_status", "unit_match_current", "unit_match_previous", "unit_evidence_status", "table_id", "table_name", "table_dimension", "table_identity_status", "evidence_id",
                        "evidence",
                        "metric_name", "current_value", "current_unit", "current_period",
                        "previous_value", "previous_unit", "previous_period",
                        "reporting_scope", "currency", "source_pages", "evidence_quote",
                    )
                },
                formula="收入差额=本期金额换算为元-上期金额换算为元；同比仅在可比期间、口径与正基数条件满足时计算",
                output={
                    "calculated_change": change,
                    "evidence_status": item.get("calculation_evidence_status"),
                },
                rule_version=workflow.get("result", {}).get("prompt_version", MODULE_VERSION),
            )
        if item.get("total_origin") == "derived_from_reported_complete_segments":
            context.record_calculation(
                "业务收入完整分项加总",
                inputs={
                    "calculation_id": item.get("calculation_ids", {}).get("component_sum"),
                    "table_id": item.get("table_id"),
                    "table_name": item.get("table_name"),
                    "table_dimension": item.get("table_dimension"),
                    "metric_name": item.get("metric_name"),
                    "unit": item.get("current_unit"),
                    "period": item.get("current_period"),
                    "components": item.get("components", []),
                    "table_row_count": item.get("table_row_count"),
                    "table_completeness_status": item.get("table_completeness_status"),
                    "table_boundary_evidence": [
                        evidence for evidence in item.get("evidence", [])
                        if evidence.get("role") == "table_end"
                    ],
                },
                formula="程序计算合计=同一收入表、同一分类维度内，已逐项核对且被标记为完整的所有报告分项之和",
                output={
                    "current_value": item.get("current_value"),
                    "current_unit": item.get("current_unit"),
                    "origin": item.get("total_origin"),
                    "evidence_status": item.get("calculation_evidence_status"),
                },
                rule_version=workflow.get("result", {}).get("prompt_version", MODULE_VERSION),
            )
        if "name" not in item:
            continue
        for period_label, prefix, denominator_key in (
            ("本期", "current", "current_share_denominator"),
            ("上期", "previous", "previous_share_denominator"),
        ):
            share = item.get(f"{prefix}_share_percent")
            denominator = item.get(denominator_key)
            if share is None or not isinstance(denominator, dict):
                continue
            amount_key = f"{prefix}_value"
            unit_key = f"{prefix}_unit"
            period_key = f"{prefix}_period"
            context.record_calculation(
                f"业务收入{period_label}结构占比",
                inputs={
                    "calculation_id": item.get("calculation_ids", {}).get(f"{prefix}_share"),
                    "numerator": {
                        key: item.get(key)
                        for key in (
                            "table_id", "table_name", "table_dimension", "table_identity_status", "unit_match_current", "unit_match_previous", "unit_evidence_status", "evidence_id",
                            "evidence", "name", "metric_name", amount_key, unit_key,
                            period_key, "reporting_scope", "currency", "source_pages",
                        )
                    },
                    "denominator": {
                        key: denominator.get(key)
                        for key in (
                            "table_id", "table_name", "table_dimension", "table_identity_status", "unit_match_current", "unit_match_previous", "unit_evidence_status", "evidence_id",
                            "evidence", "metric_name", amount_key, unit_key,
                            period_key, "reporting_scope", "currency", "source_pages",
                        )
                    },
                },
                formula="结构占比=同表、同指标、同期间、同范围及同币种的收入分项÷收入合计×100%",
                output={
                    "share_percent": share,
                    "validation_note": item.get(f"{prefix}_share_note"),
                },
                rule_version=workflow.get("result", {}).get("prompt_version", MODULE_VERSION),
            )
        share_change = item.get("share_change_percentage_points")
        if share_change is not None:
            context.record_calculation(
                "业务收入结构占比变化",
                inputs={
                    "calculation_id": item.get("calculation_ids", {}).get("share_change"),
                    "table_id": item.get("table_id"),
                    "table_name": item.get("table_name"),
                    "table_dimension": item.get("table_dimension"),
                    "numerator_evidence_id": item.get("evidence_id"),
                    "current_share_percent": item.get("current_share_percent"),
                    "previous_share_percent": item.get("previous_share_percent"),
                    "current_denominator": item.get("current_share_denominator"),
                    "previous_denominator": item.get("previous_share_denominator"),
                },
                formula="收入占比变化=本期同口径占比-上期同口径占比（百分点）",
                output={"percentage_points": share_change},
                rule_version=workflow.get("result", {}).get("prompt_version", MODULE_VERSION),
            )
    for bridge in result.get("revenue_metric_bridges", []):
        context.record_calculation(
            "同表不同收入口径差额",
            inputs={
                "calculation_id": bridge.get("calculation_id"),
                "table_name": bridge.get("table_name"),
                "table_dimension": bridge.get("table_dimension"),
                "higher_metric": bridge.get("higher_metric"),
                "higher_value": bridge.get("higher_value"),
                "lower_metric": bridge.get("lower_metric"),
                "lower_value": bridge.get("lower_value"),
                "unit": bridge.get("unit"),
                "period": bridge.get("current_period"),
                "reporting_scope": bridge.get("reporting_scope"),
                "currency": bridge.get("currency"),
                "source_pages": bridge.get("source_pages"),
                "evidence": bridge.get("evidence"),
                "evidence_status": bridge.get("evidence_status"),
            },
            formula="同表口径差额=年报列示的营业总收入-年报列示的营业收入；只在两项均可核验且期间、范围、币种相同时计算",
            output={"difference_yuan": bridge.get("difference_yuan")},
            rule_version=workflow.get("result", {}).get("prompt_version", MODULE_VERSION),
        )
    if result:
        context.recorder.save_artifact(
            "business_calculated_result",
            {
                "revenue_totals": result.get("revenue_totals", []),
                "revenue_metric_bridges": result.get("revenue_metric_bridges", []),
                "revenue_segments": result.get("revenue_segments", []),
                "topics": result.get("topics", []),
                "coverage_checks": result.get("coverage_checks", []),
            },
        )
    return workflow
