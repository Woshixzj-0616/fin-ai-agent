"""Normalization and presentation-ready result shaping for module one."""

from __future__ import annotations

import re
from typing import Any, Callable

from backend.core.legacy_runtime import _json_object
from backend.core.legacy_support import (
    _business_currency_family,
    _business_evidence,
    _business_money_in_yuan,
    _business_scope_family,
    _normalize_space,
)
from modules.part1_business.calculations import (
    _business_add_derived_table_totals,
    _business_apply_segment_shares,
    _business_calculation_evidence_status,
    _business_change,
    _business_cross_period_issue,
    _business_has_verified_citation,
    _business_reconcile_derived_totals,
    _business_related_revenue_metric_bridges,
    _business_revenue_metric_family,
)
from modules.part1_business.evidence import (
    _annotate_business_row_value_evidence,
    _annotate_business_unit_evidence,
    _business_topic_numeric_claims,
)


def _business_coverage_entry(
    key: str,
    name: str,
    records: list[dict[str, Any]],
    retrieval_sections: dict[str, dict[str, Any]],
    *,
    has_direct_summary: bool = False,
) -> dict[str, Any]:
    retrieval = retrieval_sections.get(key, {})
    candidates = retrieval.get("candidate_pages", [])
    content_exists = has_direct_summary or bool(records)
    has_citation = has_direct_summary or any(_business_has_verified_citation(item) for item in records)
    if has_citation:
        status = "supported"
        note = "至少一项内容有可回查的页码和原文摘录；仍需人工确认解释是否准确。"
    elif content_exists:
        status = "needs_review"
        note = "模型给出了内容，但没有可匹配的出处摘录，需要人工核对。"
    elif candidates:
        status = "candidate_only"
        note = "检索命中候选页，但没有提取出带出处的结果。"
    else:
        status = "search_miss"
        note = "本次检索未命中；这不代表年报未披露。"
    return {
        "key": key,
        "name": name,
        "status": status,
        "candidate_pages": candidates,
        "note": note,
    }


def _business_build_coverage(
    *,
    business_summary_evidence: dict[str, Any],
    business_flow: list[dict[str, Any]],
    industry_context: list[dict[str, Any]],
    strategy_competitiveness: list[dict[str, Any]],
    revenue_total: dict[str, Any],
    revenue_totals: list[dict[str, Any]],
    revenue_segments: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    risk_factors: list[dict[str, Any]],
    follow_up_checks: list[dict[str, Any]],
    retrieval: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    retrieval_sections = (retrieval or {}).get("sections", {})
    entries = [
        _business_coverage_entry(
            "business_model",
            "业务模式与经营链条",
            business_flow,
            retrieval_sections,
            has_direct_summary=bool(
                business_summary_evidence.get("quote_verified")
                and business_summary_evidence.get("source_pages")
            ),
        ),
        _business_coverage_entry(
            "industry_context", "行业背景", industry_context, retrieval_sections
        ),
        _business_coverage_entry(
            "strategy_competitiveness",
            "公司战略与竞争特点",
            strategy_competitiveness,
            retrieval_sections,
        ),
    ]
    segment_entry = _business_coverage_entry(
        "revenue_structure", "收入结构与变化", revenue_segments, retrieval_sections
    )
    revenue_records = [*revenue_totals, *revenue_segments]
    unit_review_current = sum(
        bool(str(item.get("current_value") or "").strip())
        and item.get("unit_match_current") is False
        for item in revenue_records
    )
    unit_review_previous = sum(
        bool(str(item.get("previous_value") or "").strip())
        and item.get("unit_match_previous") is False
        for item in revenue_records
    )
    current_shares_not_calculated = sum(
        bool(str(item.get("current_value") or "").strip())
        and item.get("current_share_percent") is None
        for item in revenue_segments
    )
    previous_shares_not_calculated = sum(
        bool(str(item.get("previous_value") or "").strip())
        and item.get("previous_share_percent") is None
        for item in revenue_segments
    )
    segment_entry["unit_evidence_review"] = {
        "current_records": unit_review_current,
        "previous_records": unit_review_previous,
    }
    segment_entry["shares_not_calculated"] = {
        "current_records": current_shares_not_calculated,
        "previous_records": previous_shares_not_calculated,
    }
    if unit_review_current or unit_review_previous:
        segment_entry["status"] = "needs_review"
        unit_note = (
            "收入结构中有单位未能从引用原文定位："
            f"本期 {unit_review_current} 条、上期 {unit_review_previous} 条；"
            "相关占比或合计核对可能未计算，原始提取值仍保留。"
        )
        segment_entry["note"] = f"{segment_entry['note']} {unit_note}".strip()
    if segment_entry["status"] == "search_miss" and _business_has_verified_citation(revenue_total):
        segment_entry["status"] = "partial"
        segment_entry["note"] = "收入总额有出处，但没有取得带出处的分部结构。"
        segment_entry["candidate_pages"] = revenue_total.get("source_pages", [])
    entries.append(segment_entry)
    entries.append(
        _business_coverage_entry(
            "dependencies_risks",
            "经营依赖与风险",
            dependencies + risk_factors,
            retrieval_sections,
        )
    )
    handoff = _business_coverage_entry(
        "handoff_questions", "交给后续模块的问题", follow_up_checks, retrieval_sections
    )
    if not follow_up_checks:
        handoff["status"] = "not_generated"
        handoff["note"] = "尚未生成有针对性的后续核查问题。"
    entries.append(handoff)
    return entries


def _business_margin(value: str) -> str:
    """A percentage-point change is not last year's absolute gross margin."""
    return value if re.fullmatch(r"-?\d+(?:\.\d+)?%", value.strip()) else ""


def _normalize_business_analysis(
    raw: str,
    allowed_pages: set[int],
    page_text: Callable[[int], str | None],
    read_pages: list[int],
    retrieval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _json_object(raw)
    if data is None:
        return {
            "module": "business",
            "company": "未能识别",
            "period": "未能识别",
            "reporting_scope": "未能确认",
            "currency": "未能确认",
            "business_summary": "",
            "business_flow": [],
            "revenue_total": {},
            "revenue_totals": [],
            "revenue_segments": [],
            "growth_drivers": [],
            "industry_context": [],
            "strategy_competitiveness": [],
            "topics": [],
            "dependencies": [],
            "risk_factors": [],
            "major_changes": [],
            "industry_metrics": [],
            "follow_up_checks": [],
            "summary": raw.strip()[:1200] or "模型未返回可识别的结构化结果。",
            "uncertainties": ["模型没有返回可识别的结构化结果；请查看原文并重新分析。"],
            "coverage_checks": [],
            "analysis_review_status": "无法评定",
            "read_pages": sorted(set(read_pages)),
        }

    uncertainties = data.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        uncertainties = []
    normalized_uncertainties = [
        str(item).strip()[:300] for item in uncertainties[:12] if str(item).strip()
    ]
    invalid_page = False
    unverified_quote = False

    def clean_text(value: Any, limit: int = 500) -> str:
        return str(value or "").strip()[:limit]

    interpretation_review_items = []
    raw_interpretation_reviews = data.get("interpretation_review_items", [])
    if isinstance(raw_interpretation_reviews, list):
        for item in raw_interpretation_reviews[:24]:
            if not isinstance(item, dict):
                continue
            review_item = {
                "section": clean_text(item.get("section"), 60),
                "label": clean_text(item.get("label"), 120),
                "analysis": clean_text(item.get("analysis"), 700),
                "limitation": clean_text(item.get("limitation"), 400),
                "reason": clean_text(item.get("reason"), 240),
                "status": "needs_mapping_review",
            }
            if review_item["analysis"] or review_item["limitation"]:
                interpretation_review_items.append(review_item)

    def quote_matched_pages(refs: list[int], quote: str, verified: bool) -> list[int]:
        if not quote or not verified:
            return refs
        normalized_quote = _normalize_space(quote)
        matched = [
            page for page in refs
            if normalized_quote and normalized_quote in _normalize_space(page_text(page) or "")
        ]
        return matched or refs

    def safe_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def normalize_evidence_records(raw_evidence: Any) -> list[dict[str, Any]]:
        nonlocal invalid_page, unverified_quote
        if not isinstance(raw_evidence, list):
            return []
        output: list[dict[str, Any]] = []
        seen: set[tuple[str, tuple[int, ...], str]] = set()
        for evidence_item in raw_evidence[:8]:
            if not isinstance(evidence_item, dict):
                continue
            refs, quote, verified, invalid = _business_evidence(
                evidence_item, allowed_pages, page_text
            )
            refs = quote_matched_pages(refs, quote, verified)
            invalid_page = invalid_page or invalid
            unverified_quote = unverified_quote or bool(quote and not verified)
            role = clean_text(evidence_item.get("role"), 60) or "supporting_evidence"
            identity = (role, tuple(refs), quote)
            if identity in seen or not (refs or quote):
                continue
            seen.add(identity)
            output.append(
                {
                    "role": role,
                    "period": clean_text(evidence_item.get("period"), 20),
                    "source_pages": refs,
                    "evidence_quote": quote,
                    "quote_verified": verified,
                }
            )
        return output

    company = clean_text(data.get("company"), 160) or "未能确认"
    period = clean_text(data.get("period"), 80) or "未能确认"
    reporting_scope = clean_text(data.get("reporting_scope"), 60) or "未能确认"
    currency = clean_text(data.get("currency"), 40) or ""

    business_summary_refs, business_summary_quote, business_summary_verified, invalid = _business_evidence(
        {
            "source_pages": data.get("business_summary_source_pages", []),
            "evidence_quote": data.get("business_summary_evidence_quote", ""),
        },
        allowed_pages,
        page_text,
    )
    business_summary_refs = quote_matched_pages(
        business_summary_refs, business_summary_quote, business_summary_verified
    )
    invalid_page = invalid_page or invalid
    unverified_quote = unverified_quote or bool(
        business_summary_quote and not business_summary_verified
    )
    summary_refs, summary_quote, summary_verified, invalid = _business_evidence(
        {
            "source_pages": data.get("summary_source_pages", []),
            "evidence_quote": data.get("summary_evidence_quote", ""),
        },
        allowed_pages,
        page_text,
    )
    summary_refs = quote_matched_pages(summary_refs, summary_quote, summary_verified)
    invalid_page = invalid_page or invalid
    unverified_quote = unverified_quote or bool(summary_quote and not summary_verified)

    def normalize_evidenced_list(
        key: str,
        limit: int,
        fields: dict[str, int],
        *,
        defaults: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        nonlocal invalid_page, unverified_quote
        raw_items = data.get(key, [])
        output: list[dict[str, Any]] = []
        if not isinstance(raw_items, list):
            return output
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            refs, quote, verified, invalid = _business_evidence(item, allowed_pages, page_text)
            refs = quote_matched_pages(refs, quote, verified)
            invalid_page = invalid_page or invalid
            unverified_quote = unverified_quote or bool(quote and not verified)
            evidence_records = normalize_evidence_records(item.get("evidence", []))
            evidence_pages = {page for evidence in evidence_records for page in evidence["source_pages"]}
            refs = sorted(evidence_pages) if evidence_pages else refs
            normalized = {
                name: clean_text(item.get(name, (defaults or {}).get(name, "")), max_length)
                for name, max_length in fields.items()
            }
            normalized.update(
                {
                    "source_pages": refs,
                    "evidence_quote": quote,
                    "quote_verified": verified,
                    "evidence": evidence_records,
                }
            )
            if any(normalized.get(field) for field in fields):
                output.append(normalized)
        return output

    business_flow = normalize_evidenced_list(
        "business_flow",
        8,
        {"stage": 80, "description": 500, "fact_type": 32},
    )
    raw_total = data.get("revenue_total", {})
    if not isinstance(raw_total, dict):
        raw_total = {}
    refs, quote, verified, invalid = _business_evidence(raw_total, allowed_pages, page_text)
    refs = quote_matched_pages(refs, quote, verified)
    invalid_page = invalid_page or invalid
    unverified_quote = unverified_quote or bool(quote and not verified)
    revenue_total_evidence = normalize_evidence_records(raw_total.get("evidence", []))
    evidence_pages = {page for evidence in revenue_total_evidence for page in evidence["source_pages"]}
    refs = sorted(evidence_pages) if evidence_pages else refs
    revenue_total = {
        "table_name": clean_text(raw_total.get("table_name"), 160),
        "table_dimension": clean_text(raw_total.get("table_dimension"), 80),
        "total_origin": "reported_in_annual_report",
        "metric_name": clean_text(raw_total.get("metric_name"), 100),
        "current_value": clean_text(raw_total.get("current_value"), 100),
        "current_unit": clean_text(raw_total.get("current_unit"), 40),
        "current_period": clean_text(raw_total.get("current_period"), 60),
        "previous_value": clean_text(raw_total.get("previous_value"), 100),
        "previous_unit": clean_text(raw_total.get("previous_unit"), 40),
        "previous_period": clean_text(raw_total.get("previous_period"), 60),
        "reporting_scope": clean_text(raw_total.get("reporting_scope"), 60) or reporting_scope,
        "currency": clean_text(raw_total.get("currency"), 40) or currency,
        "reported_yoy": clean_text(raw_total.get("reported_yoy"), 80),
        "comparability_note": clean_text(raw_total.get("comparability_note"), 240),
        "source_pages": refs,
        "evidence_quote": quote,
        "quote_verified": verified,
        "evidence": revenue_total_evidence,
    }
    _annotate_business_row_value_evidence(revenue_total)
    _annotate_business_unit_evidence(revenue_total)
    revenue_total["calculated_change"] = _business_change(
        revenue_total["current_value"],
        revenue_total["current_unit"],
        revenue_total["current_period"],
        revenue_total["previous_value"],
        revenue_total["previous_unit"],
        revenue_total["previous_period"],
        comparable=(
            raw_total.get("cross_period_comparable") is not False
            and not _business_cross_period_issue(revenue_total["comparability_note"])
        ),
        current_currency=revenue_total["currency"],
        previous_currency=revenue_total["currency"],
        current_scope=revenue_total["reporting_scope"],
        previous_scope=revenue_total["reporting_scope"],
    )
    revenue_total["calculation_evidence_status"] = _business_calculation_evidence_status(revenue_total)

    revenue_totals: list[dict[str, Any]] = []

    def append_revenue_total(item: dict[str, Any], *, include_if_empty: bool = False) -> None:
        if not item.get("metric_name") and not include_if_empty:
            return
        if not _business_revenue_metric_family(item.get("metric_name", "")):
            return
        identity = (
            _normalize_space(item.get("table_name", "")),
            _normalize_space(item.get("table_dimension", "")),
            _business_revenue_metric_family(item.get("metric_name", "")),
            item.get("current_value", ""),
            item.get("current_unit", ""),
            item.get("previous_value", ""),
            item.get("previous_unit", ""),
            item.get("current_period", ""),
            item.get("previous_period", ""),
            _business_scope_family(item.get("reporting_scope", "")),
            _business_currency_family(item.get("currency", "")),
            tuple(item.get("source_pages", [])),
        )
        if any(
            identity
            == (
                _normalize_space(existing.get("table_name", "")),
                _normalize_space(existing.get("table_dimension", "")),
                _business_revenue_metric_family(existing.get("metric_name", "")),
                existing.get("current_value", ""),
                existing.get("current_unit", ""),
                existing.get("previous_value", ""),
                existing.get("previous_unit", ""),
                existing.get("current_period", ""),
                existing.get("previous_period", ""),
                _business_scope_family(existing.get("reporting_scope", "")),
                _business_currency_family(existing.get("currency", "")),
                tuple(existing.get("source_pages", [])),
            )
            for existing in revenue_totals
        ):
            return
        revenue_totals.append(item)

    append_revenue_total(revenue_total)
    raw_revenue_totals = data.get("revenue_totals", [])
    if isinstance(raw_revenue_totals, list):
        for item in raw_revenue_totals[:24]:
            if not isinstance(item, dict):
                continue
            refs, quote, verified, invalid = _business_evidence(item, allowed_pages, page_text)
            refs = quote_matched_pages(refs, quote, verified)
            invalid_page = invalid_page or invalid
            unverified_quote = unverified_quote or bool(quote and not verified)
            evidence_records = normalize_evidence_records(item.get("evidence", []))
            evidence_pages = {page for evidence in evidence_records for page in evidence["source_pages"]}
            refs = sorted(evidence_pages) if evidence_pages else refs
            total = {
                "table_name": clean_text(item.get("table_name"), 160),
                "table_dimension": clean_text(item.get("table_dimension"), 80),
                "total_origin": "reported_in_annual_report",
                "metric_name": clean_text(item.get("metric_name"), 100),
                "current_value": clean_text(item.get("current_value"), 100),
                "current_unit": clean_text(item.get("current_unit"), 40),
                "current_period": clean_text(item.get("current_period"), 60),
                "previous_value": clean_text(item.get("previous_value"), 100),
                "previous_unit": clean_text(item.get("previous_unit"), 40),
                "previous_period": clean_text(item.get("previous_period"), 60),
                "reporting_scope": clean_text(item.get("reporting_scope"), 60) or reporting_scope,
                "currency": clean_text(item.get("currency"), 40) or currency,
                "reported_yoy": clean_text(item.get("reported_yoy"), 80),
                "comparability_note": clean_text(item.get("comparability_note"), 240),
                "source_pages": refs,
                "evidence_quote": quote,
                "quote_verified": verified,
                "evidence": evidence_records,
            }
            _annotate_business_row_value_evidence(total)
            _annotate_business_unit_evidence(total)
            total["calculated_change"] = _business_change(
                total["current_value"],
                total["current_unit"],
                total["current_period"],
                total["previous_value"],
                total["previous_unit"],
                total["previous_period"],
                comparable=(
                    item.get("cross_period_comparable") is not False
                    and not _business_cross_period_issue(total["comparability_note"])
                ),
                current_currency=total["currency"],
                previous_currency=total["currency"],
                current_scope=total["reporting_scope"],
                previous_scope=total["reporting_scope"],
            )
            total["calculation_evidence_status"] = _business_calculation_evidence_status(total)
            append_revenue_total(total)

    revenue_segments: list[dict[str, Any]] = []
    raw_segments = data.get("revenue_segments", [])
    if isinstance(raw_segments, list):
        for item in raw_segments[:80]:
            if not isinstance(item, dict):
                continue
            refs, quote, verified, invalid = _business_evidence(item, allowed_pages, page_text)
            refs = quote_matched_pages(refs, quote, verified)
            invalid_page = invalid_page or invalid
            unverified_quote = unverified_quote or bool(quote and not verified)
            evidence_records = normalize_evidence_records(item.get("evidence", []))
            evidence_pages = {page for evidence in evidence_records for page in evidence["source_pages"]}
            refs = sorted(evidence_pages) if evidence_pages else refs
            segment = {
                "name": clean_text(item.get("name"), 120),
                "basis": clean_text(item.get("basis"), 80),
                "table_name": clean_text(item.get("table_name"), 160),
                "table_dimension": clean_text(item.get("table_dimension"), 80)
                or clean_text(item.get("basis"), 80),
                "metric_name": clean_text(item.get("metric_name"), 100),
                "current_value": clean_text(item.get("current_value"), 100),
                "current_unit": clean_text(item.get("current_unit"), 40),
                "current_period": clean_text(item.get("current_period"), 60),
                "previous_value": clean_text(item.get("previous_value"), 100),
                "previous_unit": clean_text(item.get("previous_unit"), 40),
                "previous_period": clean_text(item.get("previous_period"), 60),
                "reporting_scope": clean_text(item.get("reporting_scope"), 60) or reporting_scope,
                "currency": clean_text(item.get("currency"), 40) or currency,
                "reported_yoy": clean_text(item.get("reported_yoy"), 80),
                "gross_margin_current": _business_margin(clean_text(item.get("gross_margin_current"), 60)),
                "gross_margin_previous": _business_margin(clean_text(item.get("gross_margin_previous"), 60)),
                "company_explanation": clean_text(item.get("company_explanation"), 500),
                "comparability_note": clean_text(item.get("comparability_note"), 240),
                "table_rows_complete": item.get("table_rows_complete") is True,
                "table_row_count": safe_int(item.get("table_row_count")),
                "table_completeness_status": (
                    "model_asserted" if item.get("table_rows_complete") is True else "not_confirmed"
                ),
                "source_pages": refs,
                "evidence_quote": quote,
                "quote_verified": verified,
                "evidence": evidence_records,
            }
            _annotate_business_row_value_evidence(segment)
            _annotate_business_unit_evidence(segment)
            cross_period_comparable = (
                item.get("cross_period_comparable") is not False
                and not _business_cross_period_issue(segment["comparability_note"])
            )
            segment["cross_period_comparable"] = cross_period_comparable
            segment["calculated_change"] = _business_change(
                segment["current_value"],
                segment["current_unit"],
                segment["current_period"],
                segment["previous_value"],
                segment["previous_unit"],
                segment["previous_period"],
                comparable=cross_period_comparable,
                current_currency=segment["currency"],
                previous_currency=segment["currency"],
                current_scope=segment["reporting_scope"],
                previous_scope=segment["reporting_scope"],
            )
            segment["calculation_evidence_status"] = _business_calculation_evidence_status(segment)
            if segment["name"]:
                revenue_segments.append(segment)

    _business_add_derived_table_totals(revenue_totals, revenue_segments)
    _business_reconcile_derived_totals(revenue_totals, normalized_uncertainties)
    revenue_metric_bridges = _business_related_revenue_metric_bridges(
        revenue_totals, normalized_uncertainties
    )
    for segment in revenue_segments:
        _business_apply_segment_shares(segment, revenue_totals)

    growth_drivers = normalize_evidenced_list(
        "growth_drivers",
        8,
        {"driver": 100, "fact_type": 32, "description": 500, "limitation": 300},
    )
    industry_context = normalize_evidenced_list(
        "industry_context",
        12,
        {
            "topic": 120,
            "period": 60,
            "source_type": 60,
            "company_statement": 400,
            "analysis": 500,
            "limitation": 300,
        },
    )
    strategy_competitiveness = normalize_evidenced_list(
        "strategy_competitiveness",
        12,
        {
            "aspect": 120,
            "management_statement": 400,
            "action_or_result": 500,
            "analysis": 500,
            "limitation": 300,
        },
    )
    topics = normalize_evidenced_list(
        "topics",
        16,
        {
            "title": 120,
            "question": 300,
            "observation": 600,
            "analysis": 700,
            "alternative_explanations": 500,
            "limitation": 400,
            "handoff": 400,
        },
    )
    for topic in topics:
        unlocated_numbers = _business_topic_numeric_claims(topic, page_text)
        topic["observation_numeric_evidence_status"] = (
            "all_numeric_magnitudes_located_in_cited_pages"
            if not unlocated_numbers
            else "numeric_magnitudes_need_review"
        )
        topic["unlocated_observation_numbers"] = unlocated_numbers
        if unlocated_numbers:
            warning = (
                f"专题“{topic.get('title', '未命名')}”的观察字段含有未能在其引用页定位的数值："
                f"{', '.join(unlocated_numbers)}。请复核数字或补充准确出处；该检查只核对数值是否出现，不证明其语义匹配。"
            )
            if warning not in normalized_uncertainties:
                normalized_uncertainties.append(warning)
    dependencies = normalize_evidenced_list(
        "dependencies",
        8,
        {"name": 120, "description": 500},
    )
    risk_factors = normalize_evidenced_list(
        "risk_factors",
        12,
        {"risk": 160, "description": 500, "management_response": 400, "limitation": 300},
    )
    major_changes = normalize_evidenced_list(
        "major_changes",
        10,
        {"change": 180, "period": 60, "business_effect": 400},
    )
    industry_metrics = normalize_evidenced_list(
        "industry_metrics",
        12,
        {"name": 100, "value": 100, "unit": 40, "period": 60},
    )
    follow_up_checks = normalize_evidenced_list(
        "follow_up_checks",
        8,
        {"question": 240, "reason": 300, "next_module": 60},
    )
    calculation_records = [revenue_total, *revenue_totals, *revenue_segments]
    if any(
        record.get("calculation_evidence_status")
        in {
            "calculation_inputs_not_fully_matched",
            "table_title_header_and_current_value_matched",
            "legacy_quote_matched_values_not_independently_checked",
        }
        and record.get("calculated_change")
        and any(record["calculated_change"].values())
        for record in calculation_records
    ):
        normalized_uncertainties.append(
            "部分收入期间变化是按已提取数字计算的，但原表标题、表头或对应金额摘录未全部匹配；请先复核出处再使用。"
        )
    if invalid_page:
        normalized_uncertainties.append("模型提交了本次未读取的页码；程序已移除这些引用，请人工复核。")
    if unverified_quote:
        normalized_uncertainties.append("部分原文摘录无法与 PDF 提取文字逐字匹配，请打开来源页核对。")
    if any(
        _business_money_in_yuan(item["previous_value"], item["previous_unit"], item["currency"]) is not None
        and _business_money_in_yuan(item["previous_value"], item["previous_unit"], item["currency"]) <= 0
        for item in revenue_segments
    ):
        normalized_uncertainties.append("部分收入分部上期基数为零或负数，程序未提供普通同比变化率。")
    total_previous_amount = _business_money_in_yuan(
        revenue_total["previous_value"], revenue_total["previous_unit"], revenue_total["currency"]
    )
    if total_previous_amount is not None and total_previous_amount <= 0:
        normalized_uncertainties.append("收入总额上期基数为零或负数，程序未提供普通同比变化率。")

    coverage_checks = _business_build_coverage(
        business_summary_evidence={
            "source_pages": business_summary_refs,
            "quote_verified": business_summary_verified,
        },
        business_flow=business_flow,
        industry_context=industry_context,
        strategy_competitiveness=strategy_competitiveness,
        revenue_total=revenue_total,
        revenue_totals=revenue_totals,
        revenue_segments=revenue_segments,
        dependencies=dependencies,
        risk_factors=risk_factors,
        follow_up_checks=follow_up_checks,
        retrieval=retrieval,
    )
    review_statuses = {"partial", "needs_review", "candidate_only", "search_miss", "not_generated"}
    needs_review = bool(interpretation_review_items) or any(
        item["status"] in review_statuses for item in coverage_checks
    )
    analysis_review_status = "存在待复核内容" if needs_review else "六类内容均有可回查出处"
    if needs_review:
        normalized_uncertainties.append(
            "模块一覆盖检查发现待复核或检索未命中的部分；检索未命中不能解释为年报未披露。"
        )
    if interpretation_review_items:
        normalized_uncertainties.append(
            f"解释阶段有 {len(interpretation_review_items)} 条行业/战略分析未能安全对应事实条目；已保存在待映射复核区，没有静默并入结论。"
        )

    return {
        "module": "business",
        "company": company,
        "period": period,
        "reporting_scope": reporting_scope,
        "currency": currency or "未能确认",
        "business_summary": clean_text(data.get("business_summary"), 1200),
        "business_summary_evidence": {
            "source_pages": business_summary_refs,
            "evidence_quote": business_summary_quote,
            "quote_verified": business_summary_verified,
        },
        "business_flow": business_flow,
        "revenue_total": revenue_total,
        "revenue_totals": revenue_totals,
        "revenue_metric_bridges": revenue_metric_bridges,
        "revenue_segments": revenue_segments,
        "growth_drivers": growth_drivers,
        "industry_context": industry_context,
        "strategy_competitiveness": strategy_competitiveness,
        "interpretation_review_items": interpretation_review_items,
        "topics": topics,
        "dependencies": dependencies,
        "risk_factors": risk_factors,
        "major_changes": major_changes,
        "industry_metrics": industry_metrics,
        "follow_up_checks": follow_up_checks,
        "coverage_checks": coverage_checks,
        "analysis_review_status": analysis_review_status,
        "coverage_note": "程序校验页码和摘录是否出现；仍需人工确认表格含义、口径和因果解释。",
        "summary": clean_text(data.get("summary"), 1800) or "模型未能生成模块一摘要。",
        "summary_evidence": {
            "source_pages": summary_refs,
            "evidence_quote": summary_quote,
            "quote_verified": summary_verified,
        },
        "uncertainties": normalized_uncertainties[:20],
        "read_pages": sorted(set(int(page) for page in read_pages)),
    }
