"""Multi-step DeepSeek workflow for V2: bounded page tools and resumable questions."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from backend.core.config import PROJECT_ROOT, load_runtime_config

load_runtime_config()
from backend.deepseek_client import ModelCallError
from backend.core.legacy_runtime import (
    MAX_PAGE_CHARS, MAX_TOOL_ROUNDS, MODEL, TOOLS, _calculate_change, _client,
    _decimal_text, _json_object, _number, _run_tool_loop, _safe_model_error,
    _tool_result, recorded_completion as _run_recorded_completion,
)
from backend.core.legacy_support import (
    _business_adjacent_annual_periods, _business_currency_family, _business_evidence,
    _business_money_in_yuan, _business_period_kind, _business_period_year,
    _business_same_period, _business_scope_family, _normalize_space,
)


ROOT = PROJECT_ROOT
MAX_HISTORY_TURNS = 6
MAX_BUSINESS_EXTRA_PAGES = 8
MAX_BUSINESS_EXTRA_CHARS = 14000

BUSINESS_SECTION_SEARCHES = {
    "business_model": ("主营业务分析", "商业模式"),
    "industry_context": ("行业发展", "竞争格局"),
    "strategy_competitiveness": ("发展战略", "核心竞争力"),
    "revenue_structure": ("分产品收入", "分地区收入"),
    "dependencies_risks": ("前五名客户", "主要供应商", "风险因素"),
    "major_changes": ("重大变化", "产能建设"),
}

def _prompt(analysis_module: str = "overview", stage: str | None = None) -> str:
    prompt_name = (
        "模块一_业务与经营背景_v2.md"
        if analysis_module == "business"
        else "财报助手_v2.md"
    )
    prompt_dir = Path(__file__).resolve().parent / "prompts"
    prompt = (prompt_dir / prompt_name).read_text(encoding="utf-8")
    if analysis_module == "business" and stage in {"extract", "interpret", "question"}:
        stage_name = {
            "extract": "模块一_事实提取_v2.md",
            "interpret": "模块一_分析与交接_v2.md",
            "question": "模块一_追问_v2.md",
        }[stage]
        prompt += "\n\n" + (prompt_dir / stage_name).read_text(encoding="utf-8")
    return prompt












def _normalize_report(
    raw: str,
    allowed_pages: set[int],
    page_text: Callable[[int], str | None],
    read_pages: list[int],
) -> dict[str, Any]:
    data = _json_object(raw)
    if data is None:
        return {
            "company": "未能识别",
            "period": "未能识别",
            "summary": raw.strip()[:1200] or "模型未能生成概览。",
            "facts": [],
            "observations": [],
            "uncertainties": ["模型没有返回可识别的结构化结果；请查看原文并重新提问。"],
            "read_pages": sorted(allowed_pages),
        }

    uncertainties = data.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        uncertainties = []
    normalized_uncertainties = [str(item).strip()[:300] for item in uncertainties[:10] if str(item).strip()]
    facts: list[dict[str, Any]] = []
    invalid_page = False
    for item in data.get("facts", [])[:12] if isinstance(data.get("facts"), list) else []:
        if not isinstance(item, dict):
            continue
        refs: list[int] = []
        for reference in item.get("source_pages", []) if isinstance(item.get("source_pages"), list) else []:
            try:
                page = int(reference)
            except (TypeError, ValueError):
                invalid_page = True
                continue
            if page in allowed_pages and page not in refs:
                refs.append(page)
            else:
                invalid_page = True

        quote = str(item.get("evidence_quote", "")).strip()[:360]
        quote_verified = False
        if quote:
            normalized_quote = _normalize_space(quote)
            quote_verified = any(
                normalized_quote and normalized_quote in _normalize_space(page_text(page) or "")
                for page in refs
            )
        facts.append(
            {
                "name": str(item.get("name", "未命名指标"))[:80],
                "value": str(item.get("value", "未识别"))[:100],
                "unit": str(item.get("unit", ""))[:40],
                "period": str(item.get("period", ""))[:60],
                "change": str(item.get("change", ""))[:120],
                "source_pages": refs,
                "evidence_quote": quote,
                "quote_verified": quote_verified,
                "note": str(item.get("note", ""))[:240],
            }
        )
    if invalid_page:
        normalized_uncertainties.append("模型提交了本次未读取的页码；程序已移除这些引用，请人工复核。")
    if any(fact["evidence_quote"] and not fact["quote_verified"] for fact in facts):
        normalized_uncertainties.append("部分原文摘录无法与 PDF 提取文字逐字匹配，请打开来源页核对。")

    observations: list[dict[str, Any]] = []
    raw_observations = data.get("observations", [])
    if isinstance(raw_observations, list):
        for item in raw_observations[:10]:
            if isinstance(item, str):
                observations.append({"text": item[:500], "source_pages": []})
            elif isinstance(item, dict) and item.get("text"):
                refs = []
                for reference in item.get("source_pages", []) if isinstance(item.get("source_pages"), list) else []:
                    try:
                        page = int(reference)
                    except (TypeError, ValueError):
                        invalid_page = True
                        continue
                    if page in allowed_pages and page not in refs:
                        refs.append(page)
                    else:
                        invalid_page = True
                observations.append({"text": str(item["text"])[:500], "source_pages": refs})
    if invalid_page and "模型提交了本次未读取的页码；程序已移除这些引用，请人工复核。" not in normalized_uncertainties:
        normalized_uncertainties.append("模型提交了本次未读取的页码；程序已移除这些引用，请人工复核。")

    return {
        "company": str(data.get("company", "未能识别"))[:160],
        "period": str(data.get("period", "未能识别"))[:80],
        "summary": str(data.get("summary", "模型未能生成概览。"))[:1800],
        "facts": facts,
        "observations": observations,
        "uncertainties": normalized_uncertainties,
        "read_pages": sorted(set(int(page) for page in read_pages)),
    }


















def _business_change(
    current_value: Any,
    current_unit: Any,
    current_period: Any,
    previous_value: Any,
    previous_unit: Any,
    previous_period: Any,
    *,
    comparable: bool,
    current_currency: Any = "",
    previous_currency: Any = "",
    current_scope: Any = "",
    previous_scope: Any = "",
) -> dict[str, str | None]:
    result: dict[str, str | None] = {"difference_yuan": None, "change_percent": None}
    if not comparable:
        return result
    if not _business_adjacent_annual_periods(current_period, previous_period):
        return result
    if _business_currency_family(current_currency) != _business_currency_family(previous_currency):
        return result
    if not _business_scope_family(current_scope) or _business_scope_family(current_scope) != _business_scope_family(previous_scope):
        return result
    current = _business_money_in_yuan(current_value, current_unit, current_currency)
    previous = _business_money_in_yuan(previous_value, previous_unit, previous_currency)
    if current is None or previous is None:
        return result
    if not str(current_period or "").strip() or not str(previous_period or "").strip():
        return result
    if _normalize_space(str(current_period)) == _normalize_space(str(previous_period)):
        return result
    difference = current - previous
    result["difference_yuan"] = _decimal_text(difference)
    if previous > 0:
        result["change_percent"] = _business_percent_text(difference / previous * Decimal(100))
    return result


def _business_percent_text(value: Decimal) -> str:
    return _decimal_text(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _business_percentage(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator <= 0:
        return None
    return _business_percent_text(numerator / denominator * Decimal(100))


def _business_revenue_metric_family(value: str) -> str:
    """Recognize only explicit revenue totals and their split-table labels."""
    metric = re.sub(r"\s+", "", value)
    metric = re.sub(r"[（(](?:按)?(?:分)?(?:产品|业务|行业|地区|销售模式)(?:划分|构成|口径)?[）)]$", "", metric)
    metric = re.sub(r"(?:合计|总额)$", "", metric)
    return metric if metric in {"营业收入", "主营业务收入", "营业总收入"} else ""


def _business_cross_period_issue(note: str) -> bool:
    """A note about overlapping split dimensions is not a year-on-year break."""
    return bool(
        re.search(
            r"分部重组|分类调整|口径(?:调整|变更|变化|不一致)|合并范围(?:调整|变更|变化)"
            r"|期间(?:不同|不一致)|币种(?:不同|不一致)|(?:本期|上期|前期|同比).{0,15}不可比"
            r"|不可比.{0,15}(?:本期|上期|前期|同比)",
            note,
        )
    )


def _business_find_revenue_total(
    segment: dict[str, Any], totals: list[dict[str, Any]], value_kind: str
) -> dict[str, Any] | None:
    period_key = f"{value_kind}_period"
    value_key = f"{value_kind}_value"
    candidates = [
        total
        for total in totals
        if _business_revenue_metric_family(total.get("metric_name", ""))
        == _business_revenue_metric_family(segment.get("metric_name", ""))
        and _business_money_in_yuan(total.get(value_key), total.get(f"{value_kind}_unit"), total.get("currency")) is not None
    ]
    if not candidates:
        return None

    def rank(total: dict[str, Any]) -> tuple[int, int, int, int]:
        period_match = _business_same_period(segment.get(period_key), total.get(period_key))
        scope_match = bool(_business_scope_family(segment.get("reporting_scope"))) and (
            _business_scope_family(segment.get("reporting_scope"))
            == _business_scope_family(total.get("reporting_scope"))
        )
        currency_match = _business_currency_family(segment.get("currency")) == _business_currency_family(
            total.get("currency")
        )
        has_evidence = bool(total.get("source_pages") and total.get("quote_verified"))
        return (int(period_match), int(scope_match), int(currency_match), int(has_evidence))

    best_rank = max(rank(total) for total in candidates)
    best_candidates = [total for total in candidates if rank(total) == best_rank]
    comparable_values = {
        _business_money_in_yuan(
            total.get(value_key), total.get(f"{value_kind}_unit"), total.get("currency")
        )
        for total in best_candidates
    }
    if len(comparable_values) > 1:
        return None
    return best_candidates[0]


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
    has_citation = has_direct_summary or any(
        item.get("quote_verified") and item.get("source_pages") for item in records
    )
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
    if segment_entry["status"] == "search_miss" and revenue_total.get("quote_verified"):
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


def _business_share_note(
    numerator: Decimal | None,
    denominator: Decimal | None,
    *,
    period_matches: bool,
    metric_matches: bool,
    scope_matches: bool,
    currency_matches: bool,
    quotes_verified: bool,
) -> str:
    if not metric_matches:
        return "没有找到唯一且同指标的收入合计分母，或分母口径存在冲突。"
    if not period_matches:
        return "分部期间与总收入期间不一致或未披露。"
    if not scope_matches:
        return "分部收入与分母的合并/母公司口径不一致或未确认。"
    if not currency_matches:
        return "分部收入与分母的币种不一致或未确认。"
    if not quotes_verified:
        return "收入原文摘录尚未通过逐字核验。"
    if numerator is None or denominator is None:
        return "收入数值或人民币单位无法识别。"
    if denominator <= 0:
        return "总收入基数为零或负数。"
    return ""


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
            invalid_page = invalid_page or invalid
            unverified_quote = unverified_quote or bool(quote and not verified)
            normalized = {
                name: clean_text(item.get(name, (defaults or {}).get(name, "")), max_length)
                for name, max_length in fields.items()
            }
            normalized.update(
                {
                    "source_pages": refs,
                    "evidence_quote": quote,
                    "quote_verified": verified,
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
    invalid_page = invalid_page or invalid
    unverified_quote = unverified_quote or bool(quote and not verified)
    revenue_total = {
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
    }
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

    revenue_totals: list[dict[str, Any]] = []

    def append_revenue_total(item: dict[str, Any], *, include_if_empty: bool = False) -> None:
        if not item.get("metric_name") and not include_if_empty:
            return
        if not _business_revenue_metric_family(item.get("metric_name", "")):
            return
        identity = (
            _business_revenue_metric_family(item.get("metric_name", "")),
            item.get("current_value", ""),
            item.get("previous_value", ""),
            item.get("current_period", ""),
            item.get("previous_period", ""),
            _business_scope_family(item.get("reporting_scope", "")),
        )
        if any(
            identity
            == (
                _business_revenue_metric_family(existing.get("metric_name", "")),
                existing.get("current_value", ""),
                existing.get("previous_value", ""),
                existing.get("current_period", ""),
                existing.get("previous_period", ""),
                _business_scope_family(existing.get("reporting_scope", "")),
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
            invalid_page = invalid_page or invalid
            unverified_quote = unverified_quote or bool(quote and not verified)
            total = {
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
            }
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
            append_revenue_total(total)

    revenue_segments: list[dict[str, Any]] = []
    raw_segments = data.get("revenue_segments", [])
    if isinstance(raw_segments, list):
        for item in raw_segments[:80]:
            if not isinstance(item, dict):
                continue
            refs, quote, verified, invalid = _business_evidence(item, allowed_pages, page_text)
            invalid_page = invalid_page or invalid
            unverified_quote = unverified_quote or bool(quote and not verified)
            segment = {
                "name": clean_text(item.get("name"), 120),
                "basis": clean_text(item.get("basis"), 80),
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
                "source_pages": refs,
                "evidence_quote": quote,
                "quote_verified": verified,
            }
            cross_period_comparable = (
                item.get("cross_period_comparable") is not False
                and not _business_cross_period_issue(segment["comparability_note"])
            )
            segment["cross_period_comparable"] = cross_period_comparable
            current_total_record = _business_find_revenue_total(segment, revenue_totals, "current")
            previous_total_record = _business_find_revenue_total(segment, revenue_totals, "previous")
            current_amount = _business_money_in_yuan(
                segment["current_value"], segment["current_unit"], segment["currency"]
            )
            previous_amount = _business_money_in_yuan(
                segment["previous_value"], segment["previous_unit"], segment["currency"]
            )
            total_current = _business_money_in_yuan(
                current_total_record.get("current_value") if current_total_record else "",
                current_total_record.get("current_unit") if current_total_record else "",
                current_total_record.get("currency") if current_total_record else "",
            )
            total_previous = _business_money_in_yuan(
                previous_total_record.get("previous_value") if previous_total_record else "",
                previous_total_record.get("previous_unit") if previous_total_record else "",
                previous_total_record.get("currency") if previous_total_record else "",
            )
            segment_metric_known = bool(_business_revenue_metric_family(segment["metric_name"]))
            current_metric_matches = bool(
                segment_metric_known
                and current_total_record
                and _business_revenue_metric_family(current_total_record.get("metric_name", ""))
                == _business_revenue_metric_family(segment["metric_name"])
            )
            previous_metric_matches = bool(
                segment_metric_known
                and previous_total_record
                and _business_revenue_metric_family(previous_total_record.get("metric_name", ""))
                == _business_revenue_metric_family(segment["metric_name"])
            )
            current_period_matches = bool(
                current_total_record
                and _business_same_period(
                    segment["current_period"], current_total_record.get("current_period")
                )
            )
            previous_period_matches = bool(
                previous_total_record
                and _business_same_period(
                    segment["previous_period"], previous_total_record.get("previous_period")
                )
            )
            current_scope_matches = bool(
                current_total_record
                and _business_scope_family(segment["reporting_scope"])
                and _business_scope_family(segment["reporting_scope"])
                == _business_scope_family(current_total_record.get("reporting_scope"))
            )
            previous_scope_matches = bool(
                previous_total_record
                and _business_scope_family(segment["reporting_scope"])
                and _business_scope_family(segment["reporting_scope"])
                == _business_scope_family(previous_total_record.get("reporting_scope"))
            )
            current_currency_matches = bool(
                current_total_record
                and _business_currency_family(segment["currency"])
                and _business_currency_family(segment["currency"])
                == _business_currency_family(current_total_record.get("currency"))
            )
            previous_currency_matches = bool(
                previous_total_record
                and _business_currency_family(segment["currency"])
                and _business_currency_family(segment["currency"])
                == _business_currency_family(previous_total_record.get("currency"))
            )
            current_quotes_verified = bool(
                verified
                and current_total_record
                and current_total_record.get("quote_verified")
                and current_total_record.get("source_pages")
            )
            previous_quotes_verified = bool(
                verified
                and previous_total_record
                and previous_total_record.get("quote_verified")
                and previous_total_record.get("source_pages")
            )
            segment["current_share_percent"] = (
                _business_percentage(current_amount, total_current)
                if current_amount is not None
                and total_current is not None
                and current_period_matches
                and current_metric_matches
                and current_scope_matches
                and current_currency_matches
                and current_quotes_verified
                else None
            )
            segment["current_share_denominator"] = current_total_record
            segment["current_share_note"] = _business_share_note(
                current_amount,
                total_current,
                period_matches=current_period_matches,
                metric_matches=current_metric_matches,
                scope_matches=current_scope_matches,
                currency_matches=current_currency_matches,
                quotes_verified=current_quotes_verified,
            )
            segment["previous_share_percent"] = (
                _business_percentage(previous_amount, total_previous)
                if previous_amount is not None
                and total_previous is not None
                and previous_period_matches
                and previous_metric_matches
                and previous_scope_matches
                and previous_currency_matches
                and previous_quotes_verified
                else None
            )
            segment["previous_share_denominator"] = previous_total_record
            segment["previous_share_note"] = _business_share_note(
                previous_amount,
                total_previous,
                period_matches=previous_period_matches,
                metric_matches=previous_metric_matches,
                scope_matches=previous_scope_matches,
                currency_matches=previous_currency_matches,
                quotes_verified=previous_quotes_verified,
            )
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
            if segment["name"]:
                revenue_segments.append(segment)

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
        revenue_segments=revenue_segments,
        dependencies=dependencies,
        risk_factors=risk_factors,
        follow_up_checks=follow_up_checks,
        retrieval=retrieval,
    )
    review_statuses = {"partial", "needs_review", "candidate_only", "search_miss", "not_generated"}
    needs_review = any(item["status"] in review_statuses for item in coverage_checks)
    analysis_review_status = "存在待复核内容" if needs_review else "六类内容均有可回查出处"
    if needs_review:
        normalized_uncertainties.append(
            "模块一覆盖检查发现待复核或检索未命中的部分；检索未命中不能解释为年报未披露。"
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
        "revenue_segments": revenue_segments,
        "growth_drivers": growth_drivers,
        "industry_context": industry_context,
        "strategy_competitiveness": strategy_competitiveness,
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






def _business_preflight_search(
    *,
    search_pages: Callable[[str], list[dict[str, Any]]],
    get_page: Callable[[int], str | None],
    already_provided: set[int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Search the required module-one sections before asking the model to synthesize them."""
    sections: dict[str, dict[str, Any]] = {}
    trace: list[dict[str, Any]] = []
    ranked_pages: dict[int, tuple[int, str]] = {}
    for key, queries in BUSINESS_SECTION_SEARCHES.items():
        hits_by_page: dict[int, dict[str, Any]] = {}
        for query in queries:
            hits = search_pages(query)
            for hit in hits[:4]:
                try:
                    page_number = int(hit["page"])
                except (KeyError, TypeError, ValueError):
                    continue
                if page_number < 1:
                    continue
                score = int(hit.get("score", 0) or 0)
                prior = hits_by_page.get(page_number)
                if prior is None or score > int(prior.get("score", 0) or 0):
                    hits_by_page[page_number] = hit
                if page_number not in ranked_pages or score > ranked_pages[page_number][0]:
                    ranked_pages[page_number] = (score, str(hit.get("text", "")))
        ordered = sorted(hits_by_page.values(), key=lambda item: (-int(item.get("score", 0) or 0), int(item["page"])))
        candidate_pages = [int(item["page"]) for item in ordered[:4]]
        sections[key] = {
            "queries": list(queries),
            "candidate_pages": candidate_pages,
            "search_hit_count": len(hits_by_page),
        }
        trace.append(
            {
                "tool": "module1_preflight_search",
                "arguments": {"section": key, "queries": list(queries)},
                "pages": candidate_pages,
                "result": {"candidate_page_count": len(hits_by_page)},
            }
        )

    selected_extra: list[int] = []
    # Give each section its strongest candidate first, then use remaining room for follow-up hits.
    max_candidates = max((len(item["candidate_pages"]) for item in sections.values()), default=0)
    for index in range(max_candidates):
        for section in sections.values():
            candidates = section["candidate_pages"]
            if index >= len(candidates):
                continue
            page_number = candidates[index]
            if page_number in already_provided or page_number in selected_extra:
                continue
            if len(selected_extra) >= MAX_BUSINESS_EXTRA_PAGES:
                break
            selected_extra.append(page_number)
        if len(selected_extra) >= MAX_BUSINESS_EXTRA_PAGES:
            break

    extra_pages: list[dict[str, Any]] = []
    remaining_chars = MAX_BUSINESS_EXTRA_CHARS
    for page_number in selected_extra:
        text = get_page(page_number)
        if not text or remaining_chars <= 0:
            continue
        excerpt = str(text)[: min(3200, remaining_chars)].strip()
        if excerpt:
            extra_pages.append({"page": page_number, "text": excerpt})
            remaining_chars -= len(excerpt)
    for section in sections.values():
        section["provided_pages"] = [page for page in section["candidate_pages"] if page in already_provided or page in selected_extra]
    retrieval = {
        "sections": sections,
        "note": "检索结果仅用于定位候选页；未命中不代表年报未披露。",
    }
    return retrieval, extra_pages, trace


def analyze_report(
    *,
    file_name: str,
    page_count: int,
    initial_pages: list[dict[str, Any]],
    get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
    on_tool: Callable[[str, str], None] | None = None,
    on_stage: Callable[[str, str, str], None] | None = None,
    recorder: Any = None,
    analysis_module: str = "overview",
) -> dict[str, Any]:
    provided_pages = {int(page["page"]) for page in initial_pages}
    retrieval: dict[str, Any] | None = None
    retrieval_trace: list[dict[str, Any]] = []
    model_pages = list(initial_pages)
    if analysis_module == "business":
        if on_stage:
            on_stage("定位模块一必查内容", "进行中", "按业务、行业、战略、收入、依赖和风险主题检索全文。")
        retrieval, extra_pages, retrieval_trace = _business_preflight_search(
            search_pages=search_pages,
            get_page=get_page,
            already_provided=provided_pages,
        )
        model_pages.extend(extra_pages)
        provided_pages.update(int(page["page"]) for page in extra_pages)
        if on_stage:
            hit_sections = sum(bool(item["candidate_pages"]) for item in retrieval["sections"].values())
            on_stage("定位模块一必查内容", "已完成", f"6 类检索主题中 {hit_sections} 类找到候选页；候选不等于已证实。")
            on_stage("DeepSeek提取年报事实", "进行中", "先提取原文事实、报告口径和收入表，不做经营归因。")
    material = "\n\n".join(
        f"【文档：{file_name}；PDF第{int(page['page'])}页】\n{page['text']}" for page in model_pages
    )
    retrieval_summary = (
        "\n\n程序预检到的模块一候选页（检索未命中不代表年报未披露）：\n"
        + json.dumps(retrieval, ensure_ascii=False)
        if retrieval
        else ""
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": _prompt(analysis_module, "extract" if analysis_module == "business" else None),
        },
        {
            "role": "user",
            "content": (
                (
                    f"这是模块一第一阶段：仅从年报提取事实和出处，不做原因归纳或经营评价。文件：{file_name}；PDF 共 {page_count} 页。"
                    if analysis_module == "business"
                    else f"请阅读并分析这份年报。文件：{file_name}；PDF 共 {page_count} 页。"
                )
                + "以下包含首批页面和程序预检的分主题候选页；如证据不足仍可调用工具补查。\n"
                + retrieval_summary
                + "\n\n"
                + material
            ),
        },
    ]
    content, trace, usage = _run_tool_loop(
        messages=messages,
        page_count=page_count,
        get_page=get_page,
        search_pages=search_pages,
        on_tool=on_tool,
        available_tools=TOOLS[:2] if analysis_module == "business" else None,
        recorder=recorder,
    )
    used_pages = provided_pages | set(usage.pop("_tool_pages", []))
    if analysis_module != "business":
        result = _normalize_report(content, used_pages, get_page, sorted(used_pages))
        result["model"] = MODEL
        result["usage"] = usage
        return {"result": result, "trace": trace, "read_pages": sorted(used_pages)}

    extraction_data = _json_object(content)
    if extraction_data is None:
        result = _normalize_business_analysis(content, used_pages, get_page, sorted(used_pages), retrieval)
        result["model"] = MODEL
        result["usage"] = usage
        return {"result": result, "trace": retrieval_trace + trace, "read_pages": sorted(used_pages)}

    if on_stage:
        extracted_count = len(extraction_data.get("revenue_segments", [])) if isinstance(extraction_data.get("revenue_segments"), list) else 0
        on_stage("DeepSeek提取年报事实", "已完成", f"已整理公司业务事实和 {extracted_count} 项收入分部候选。")
        on_stage("DeepSeek分析经营变化和交接问题", "进行中", "第二阶段只根据已提取并带出处的事实分析变化与后续问题。")

    extracted_result = _normalize_business_analysis(
        content, used_pages, get_page, sorted(used_pages), retrieval
    )
    segment_context = [
        {key: value for key, value in item.items() if key not in {"current_share_denominator", "previous_share_denominator"}}
        for item in extracted_result.get("revenue_segments", [])
    ]
    interpretation_context = {
        "company": extracted_result.get("company"),
        "period": extracted_result.get("period"),
        "reporting_scope": extracted_result.get("reporting_scope"),
        "currency": extracted_result.get("currency"),
        "business_summary": extracted_result.get("business_summary"),
        "business_summary_evidence": extracted_result.get("business_summary_evidence"),
        "business_flow": extracted_result.get("business_flow"),
        "industry_context": extracted_result.get("industry_context"),
        "strategy_competitiveness": extracted_result.get("strategy_competitiveness"),
        "revenue_total": extracted_result.get("revenue_total"),
        "revenue_totals": extracted_result.get("revenue_totals"),
        "revenue_segments": segment_context,
        "growth_drivers_extracted": extracted_result.get("growth_drivers"),
        "dependencies": extracted_result.get("dependencies"),
        "risk_factors": extracted_result.get("risk_factors"),
        "major_changes": extracted_result.get("major_changes"),
        "industry_metrics": extracted_result.get("industry_metrics"),
        "coverage_search": retrieval,
        "uncertainties": extracted_result.get("uncertainties"),
    }
    interpretation_messages = [
        {"role": "system", "content": _prompt("business", "interpret")},
        {
            "role": "user",
            "content": (
                "这是模块一第二阶段。只分析下面已从当前年报提取的材料事实；不要重新提取或修改收入数字。"
                "请给出有边界的经营解释、行业/战略分析、后续模块问题和摘要。摘录与页码只可从输入事实中选择。\n\n"
                + json.dumps(interpretation_context, ensure_ascii=False)
            ),
        },
    ]
    interpretation_raw = ""
    interpretation_data: dict[str, Any] | None = None
    interpretation_error = ""
    try:
        response = _run_recorded_completion(
            recorder=recorder,
            client=_client(),
            model=MODEL,
            messages=interpretation_messages,
            stream=False,
        )
        if response.usage:
            usage["prompt_tokens"] += response.usage.prompt_tokens or 0
            usage["completion_tokens"] += response.usage.completion_tokens or 0
        if not response.choices:
            raise ModelCallError("DeepSeek 没有返回分析阶段结果。")
        interpretation_raw = response.choices[0].message.content or ""
        interpretation_data = _json_object(interpretation_raw)
    except ModelCallError as exc:
        interpretation_error = str(exc)
    except Exception as exc:
        interpretation_error = str(_safe_model_error(exc))

    merged_data = dict(extraction_data)
    if interpretation_data is not None:
        for source_key, target_key, match_key in (
            ("industry_context_analysis", "industry_context", "topic"),
            ("strategy_analysis", "strategy_competitiveness", "aspect"),
        ):
            updates = interpretation_data.get(source_key, [])
            if not isinstance(updates, list):
                continue
            update_map = {
                str(item.get(match_key, "")).strip(): item
                for item in updates
                if isinstance(item, dict) and str(item.get(match_key, "")).strip()
            }
            source_items = merged_data.get(target_key, [])
            if isinstance(source_items, list):
                merged_data[target_key] = [
                    {
                        **item,
                        **{
                            field: update_map.get(str(item.get(match_key, "")).strip(), {}).get(field, item.get(field, ""))
                            for field in ("analysis", "limitation")
                        },
                    }
                    if isinstance(item, dict)
                    else item
                    for item in source_items
                ]
        for key in ("growth_drivers", "follow_up_checks"):
            value = interpretation_data.get(key)
            if isinstance(value, list):
                merged_data[key] = value
        for key in ("summary", "summary_source_pages", "summary_evidence_quote"):
            if key in interpretation_data:
                merged_data[key] = interpretation_data[key]
        interpretation_uncertainties = interpretation_data.get("uncertainties", [])
        if isinstance(interpretation_uncertainties, list):
            merged_data["uncertainties"] = list(merged_data.get("uncertainties", [])) + interpretation_uncertainties
    else:
        issue = interpretation_error or "模型分析阶段未返回可识别的结构化结果。"
        merged_data["uncertainties"] = list(merged_data.get("uncertainties", [])) + [
            f"经营解释阶段没有完成，已保留第一阶段抽取结果：{issue}"
        ]

    result = _normalize_business_analysis(
        json.dumps(merged_data, ensure_ascii=False),
        used_pages,
        get_page,
        sorted(used_pages),
        retrieval,
    )
    if interpretation_data is None:
        result["analysis_review_status"] = "事实已提取，解释阶段未完成"
    if on_stage:
        on_stage(
            "DeepSeek分析经营变化和交接问题",
            "已完成" if interpretation_data is not None else "需要复核",
            "第二阶段结果已合并；引用和口径由程序再次校验。" if interpretation_data is not None else "已保留第一阶段事实，经营解释需重试。",
        )
        on_stage("程序校验出处、收入口径与计算", "进行中", "正在检查页码、摘录、单位、期间、范围及可比条件。")
    if interpretation_data is not None:
        trace.append(
            {
                "tool": "module1_interpretation_stage",
                "arguments": {"phase": "analysis_and_handoff"},
                "pages": [],
                "result": {"status": "completed"},
            }
        )
    else:
        trace.append(
            {
                "tool": "module1_interpretation_stage",
                "arguments": {"phase": "analysis_and_handoff"},
                "pages": [],
                "result": {"status": "needs_review"},
            }
        )
    if on_stage:
        on_stage("程序校验出处、收入口径与计算", "已完成", "程序已执行引用匹配和收入计算门槛。")
        on_stage("核对六类模块覆盖与待查问题", "已完成", result.get("analysis_review_status", "需要复核"))
    result["model"] = MODEL
    result["usage"] = usage
    return {"result": result, "trace": retrieval_trace + trace, "read_pages": sorted(used_pages)}


def answer_question(
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
    analysis_module: str = "overview",
) -> dict[str, Any]:
    context = {
        "file_name": file_name,
        "page_count": page_count,
        "initial_summary": initial_result.get("summary", ""),
        "company": initial_result.get("company", ""),
        "period": initial_result.get("period", ""),
        "known_facts": initial_result.get("facts", [])[:12],
        "business_analysis": (
            {
                "business_summary": initial_result.get("business_summary", ""),
                "revenue_total": initial_result.get("revenue_total", {}),
                "revenue_totals": initial_result.get("revenue_totals", [])[:24],
                "revenue_segments": initial_result.get("revenue_segments", [])[:12],
                "growth_drivers": initial_result.get("growth_drivers", [])[:8],
                "industry_context": initial_result.get("industry_context", [])[:8],
                "strategy_competitiveness": initial_result.get("strategy_competitiveness", [])[:8],
                "dependencies": initial_result.get("dependencies", [])[:8],
                "risk_factors": initial_result.get("risk_factors", [])[:8],
                "coverage_checks": initial_result.get("coverage_checks", []),
            }
            if analysis_module == "business"
            else None
        ),
        "already_read_pages": initial_result.get("read_pages", []),
        "recent_questions_and_answers": [
            {"question": turn.get("question", ""), "answer": turn.get("answer", "")[:1200]}
            for turn in history[-MAX_HISTORY_TURNS:]
            if turn.get("status") == "completed"
        ],
    }
    if analysis_module == "complete":
        modules = initial_result.get("modules", {})
        business_result = (modules.get("business") or {}).get("result") or {}
        profit_result = (modules.get("profit") or {}).get("result") or {}
        context["business_analysis"] = {
            "status": (modules.get("business") or {}).get("status", "missing"),
            "business_summary": business_result.get("business_summary", ""),
            "business_flow": business_result.get("business_flow", [])[:8],
            "revenue_total": business_result.get("revenue_total", {}),
            "revenue_segments": business_result.get("revenue_segments", [])[:18],
            "growth_drivers": business_result.get("growth_drivers", [])[:8],
            "industry_context": business_result.get("industry_context", [])[:8],
            "strategy_competitiveness": business_result.get("strategy_competitiveness", [])[:8],
            "dependencies": business_result.get("dependencies", [])[:8],
            "risk_factors": business_result.get("risk_factors", [])[:8],
            "major_changes": business_result.get("major_changes", [])[:8],
            "coverage_checks": business_result.get("coverage_checks", []),
        }
        context["profit_analysis"] = {
            "status": (modules.get("profit") or {}).get("status", "missing"),
            "summary": profit_result.get("summary", ""),
            "profit_lines": profit_result.get("profit_lines", [])[:45],
            "profit_metrics": profit_result.get("profit_metrics", []),
            "profit_bridge": profit_result.get("profit_bridge", {}),
            "profit_checks": profit_result.get("profit_checks", []),
            "business_segments": profit_result.get("business_segments", [])[:20],
            "nonrecurring_items": profit_result.get("nonrecurring_items", [])[:20],
            "findings": profit_result.get("findings", [])[:8],
            "uncertainties": profit_result.get("uncertainties", [])[:10],
        }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _prompt(analysis_module, "question" if analysis_module == "business" else None)},
        {
            "role": "user",
            "content": (
                (
                    "请综合模块一经营背景与模块二盈利结构回答。金额、口径及计算以模块二程序结果为准；"
                    "经营原因只有年报解释和对应出处支持时才确认，模块一业务变化只能帮助定位，不能单独当作因果证据。"
                    "需要新证据时使用工具查阅年报，区分已披露事实、会计贡献和有限推断。任务上下文如下："
                    if analysis_module == "complete"
                    else
                    "请围绕模块一结果回答业务、经营和收入结构问题；若用户询问盈利、资产、现金或债务判断，"
                    "说明需要后续模块并指出应查资料。已知任务信息如下；只能把明确列出的来源页作为已有证据，"
                    if analysis_module == "business"
                    else "请围绕用户当前这份年报回答追问。已知任务信息如下；只能把明确列出的来源页作为已有证据，"
                )
                + "需要新证据时使用工具查阅全文。简明回答，区分原文事实与推断，引用 PDF 页码，证据不足要明说。\n\n"
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
        available_tools=TOOLS[:2] if analysis_module == "business" else None,
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
