"""Deterministic revenue calculations and evidence gates for module one."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from backend.core.legacy_runtime import _decimal_text
from backend.core.legacy_support import (
    _business_adjacent_annual_periods,
    _business_currency_family,
    _business_money_in_yuan,
    _business_same_period,
    _business_scope_family,
    _normalize_space,
)
from modules.part1_business.evidence import (
    _business_unit_is_verified,
    _business_unit_quote_matches,
    _business_quote_contains_values,
)


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


def _business_record_evidence_verified(record: dict[str, Any], value_kind: str = "current") -> bool:
    evidence = record.get("evidence")
    if isinstance(evidence, list) and evidence:
        if record.get("total_origin") == "derived_from_reported_complete_segments":
            if value_kind != "current":
                return False
            title_verified = any(
                isinstance(item, dict)
                and str(item.get("role") or "").casefold() == "table_title"
                and item.get("quote_verified")
                and item.get("source_pages")
                and _normalize_space(record.get("table_name", ""))
                and _normalize_space(record.get("table_name", ""))
                in _normalize_space(item.get("evidence_quote", ""))
                for item in evidence
            )
            header_verified = any(
                isinstance(item, dict)
                and str(item.get("role") or "").casefold() == "table_header"
                and item.get("quote_verified")
                and item.get("source_pages")
                for item in evidence
            )
            end_verified = any(
                isinstance(item, dict)
                and str(item.get("role") or "").casefold() == "table_end"
                and item.get("quote_verified")
                and item.get("source_pages")
                for item in evidence
            )
            return bool(
                title_verified and header_verified and end_verified
                and record.get("component_rows_verified")
                and _business_unit_is_verified(record, value_kind)
            )
        verified_roles = {
            str(item.get("role") or "").strip().casefold()
            for item in evidence
            if isinstance(item, dict)
            and item.get("quote_verified")
            and item.get("source_pages")
        }
        table_name = _normalize_space(record.get("table_name", ""))
        verified_title = any(
            isinstance(item, dict)
            and str(item.get("role") or "").casefold() == "table_title"
            and item.get("quote_verified")
            and item.get("source_pages")
            and table_name
            and table_name in _normalize_space(item.get("evidence_quote", ""))
            for item in evidence
        )
        verified_row_values = any(
            isinstance(item, dict)
            and str(item.get("role") or "").casefold() == "row_value"
            and item.get("quote_verified")
            and item.get("source_pages")
            and item.get(f"{value_kind}_value_match") is True
            for item in evidence
        )
        return (
            {"table_header", "row_value"}.issubset(verified_roles)
            and verified_title
            and verified_row_values
            and _business_unit_is_verified(record, value_kind)
        )
    return bool(record.get("quote_verified") and record.get("source_pages"))


def _business_has_verified_citation(record: dict[str, Any]) -> bool:
    evidence = record.get("evidence")
    if isinstance(evidence, list) and evidence:
        return any(
            isinstance(item, dict) and item.get("quote_verified") and item.get("source_pages")
            for item in evidence
        )
    return bool(record.get("quote_verified") and record.get("source_pages"))


def _business_table_header_signature(record: dict[str, Any]) -> tuple[str, ...]:
    evidence = record.get("evidence")
    if not isinstance(evidence, list):
        return ()
    return tuple(sorted({
        _normalize_space(item.get("evidence_quote", ""))
        for item in evidence
        if isinstance(item, dict)
        and str(item.get("role") or "").casefold() == "table_header"
        and item.get("quote_verified")
        and item.get("evidence_quote")
    }))


def _business_table_headers_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_signature = _business_table_header_signature(left)
    right_signature = _business_table_header_signature(right)
    if not left_signature or not right_signature:
        return False
    if left_signature == right_signature:
        return True
    if len(left_signature) != 1 or len(right_signature) != 1:
        return False

    left_quote = _normalize_space(left_signature[0])
    right_quote = _normalize_space(right_signature[0])
    shorter, longer = sorted((left_quote, right_quote), key=len)
    if len(shorter) < 24 or shorter not in longer:
        return False
    if _business_revenue_metric_family(left.get("metric_name", "")) != _business_revenue_metric_family(
        right.get("metric_name", "")
    ):
        return False

    dimensions = [
        _normalize_space(record.get("table_dimension", "") or record.get("basis", ""))
        for record in (left, right)
    ]
    dimension = next((item for item in dimensions if item and not item.startswith("总表")), "")
    dimension_markers = {
        "产品": ("分产品",),
        "地区": ("分地区",),
        "行业": ("分行业",),
        "销售模式": ("销售模式", "分销售模式"),
        "渠道": ("渠道",),
    }
    markers = dimension_markers.get(dimension)
    return bool(markers and any(marker in shorter for marker in markers) and "营业收入" in shorter)


def _business_is_overall_dimension(value: Any) -> bool:
    return _normalize_space(value).startswith("总表")


def _business_header_declares_overall_share(record: dict[str, Any]) -> bool:
    quote = " ".join(_business_table_header_signature(record))
    normalized = _normalize_space(quote)
    return any(marker in normalized for marker in ("占营业收入比重", "占收入比重", "营业收入占比", "收入占比"))




def _business_calculation_evidence_status(record: dict[str, Any]) -> str:
    evidence = record.get("evidence")
    if record.get("total_origin") == "derived_from_reported_complete_segments":
        return "derived_from_matched_rows_with_reported_table_boundary"
    if isinstance(evidence, list) and evidence:
        current_verified = _business_record_evidence_verified(record, "current")
        previous_verified = _business_record_evidence_verified(record, "previous")
        if current_verified and previous_verified:
            return "table_title_header_unit_and_both_values_matched"
        if current_verified:
            return "table_title_header_unit_and_current_value_matched"
        return "calculation_inputs_not_fully_matched"
    if record.get("quote_verified") and record.get("source_pages"):
        return "legacy_quote_matched_values_not_independently_checked"
    return "calculation_inputs_not_fully_matched"


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
    segment_metric_family = _business_revenue_metric_family(segment.get("metric_name", ""))
    if not segment_metric_family:
        return None
    candidates = [
        total
        for total in totals
        if _business_revenue_metric_family(total.get("metric_name", "")) == segment_metric_family
        and _business_money_in_yuan(total.get(value_key), total.get(f"{value_kind}_unit"), total.get("currency")) is not None
    ]
    segment_table = _normalize_space(segment.get("table_name", ""))
    named_candidates = [total for total in candidates if _normalize_space(total.get("table_name", ""))]
    if segment_table and named_candidates:
        exact_table_candidates = [
            total
            for total in named_candidates
            if _normalize_space(total.get("table_name", "")) == segment_table
        ]
        segment_dimension = _normalize_space(segment.get("table_dimension", ""))
        if segment_dimension:
            exact_table_candidates = [
                total
                for total in exact_table_candidates
                if _normalize_space(total.get("table_dimension", "")) == segment_dimension
                or (
                    _business_is_overall_dimension(total.get("table_dimension", ""))
                    and _business_header_declares_overall_share(total)
                    and _business_table_headers_match(segment, total)
                )
            ]
        segment_header = _business_table_header_signature(segment)
        segment_has_structured_evidence = bool(segment.get("evidence"))
        if segment_has_structured_evidence:
            exact_table_candidates = [
                total
                for total in exact_table_candidates
                if segment_header and _business_table_headers_match(segment, total)
            ]
        # When the model has identified source tables, never use a denominator
        # from a differently titled or differently headed table just because
        # its metric is similar.
        candidates = exact_table_candidates
    elif named_candidates:
        # A segment with no table identity cannot safely borrow a denominator
        # from a titled table sharing only the same metric family.
        candidates = []
    if not candidates:
        return None

    def rank(total: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
        period_match = _business_same_period(segment.get(period_key), total.get(period_key))
        scope_match = bool(_business_scope_family(segment.get("reporting_scope"))) and (
            _business_scope_family(segment.get("reporting_scope"))
            == _business_scope_family(total.get("reporting_scope"))
        )
        currency_match = _business_currency_family(segment.get("currency")) == _business_currency_family(
            total.get("currency")
        )
        has_evidence = _business_has_verified_citation(total)
        calculation_evidence_verified = _business_record_evidence_verified(total, value_kind)
        dimension_exact = _normalize_space(segment.get("table_dimension", "")) == _normalize_space(
            total.get("table_dimension", "")
        )
        directly_reported = total.get("total_origin") != "derived_from_reported_complete_segments"
        denominator_preference = (
            3 if dimension_exact and directly_reported
            else 2 if directly_reported
            else 1 if dimension_exact
            else 0
        )
        return (
            int(period_match), int(scope_match), int(currency_match),
            int(calculation_evidence_verified), denominator_preference, int(has_evidence),
        )

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


def _business_add_derived_table_totals(
    revenue_totals: list[dict[str, Any]], revenue_segments: list[dict[str, Any]]
) -> None:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for segment in revenue_segments:
        identity = (
            _normalize_space(segment.get("table_name", "")),
            _normalize_space(segment.get("table_dimension", "")),
            _business_revenue_metric_family(segment.get("metric_name", "")),
            _normalize_space(segment.get("current_unit", "")),
            _normalize_space(segment.get("current_period", "")),
            _business_scope_family(segment.get("reporting_scope", "")),
            _business_currency_family(segment.get("currency", "")),
        )
        if all(identity) and identity[2]:
            groups.setdefault(identity, []).append(segment)

    for identity, rows in groups.items():
        table_name, table_dimension, metric_family, unit, period, scope, currency = identity
        declared_counts = {row.get("table_row_count", 0) for row in rows}
        if (
            len(declared_counts) != 1
            or next(iter(declared_counts)) != len(rows)
            or not rows[0].get("table_row_count")
            or not all(row.get("table_rows_complete") is True for row in rows)
            or len({_normalize_space(row.get("name", "")) for row in rows}) != len(rows)
            or not all(_business_table_headers_match(rows[0], row) for row in rows)
            or not all(_business_record_evidence_verified(row, "current") for row in rows)
        ):
            continue
        boundary_evidence = [
            evidence
            for row in rows
            for evidence in row.get("evidence", [])
            if isinstance(evidence, dict)
            and str(evidence.get("role") or "").casefold() == "table_end"
            and evidence.get("quote_verified")
            and evidence.get("source_pages")
        ]
        if not boundary_evidence:
            continue
        if any(
            _normalize_space(total.get("table_name", "")) == table_name
            and _normalize_space(total.get("table_dimension", "")) == table_dimension
            and _business_revenue_metric_family(total.get("metric_name", "")) == metric_family
            and _business_same_period(total.get("current_period"), period)
            for total in revenue_totals
        ):
            continue

        values: list[Decimal] = []
        components: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = Decimal(str(row.get("current_value", "")).replace(",", "").strip())
            except InvalidOperation:
                values = []
                break
            if _business_money_in_yuan(row.get("current_value"), row.get("current_unit"), row.get("currency")) is None:
                values = []
                break
            values.append(value)
            row_evidence = [
                evidence for evidence in row.get("evidence", [])
                if isinstance(evidence, dict)
                and str(evidence.get("role") or "").casefold() == "row_value"
                and evidence.get("quote_verified")
                and evidence.get("current_value_match") is True
            ]
            components.append(
                {
                    "name": row.get("name", ""),
                    "current_value": row.get("current_value", ""),
                    "current_unit": row.get("current_unit", ""),
                    "source_pages": row.get("source_pages", []),
                    "evidence": [dict(evidence, role="component_row_value") for evidence in row_evidence],
                }
            )
        if len(values) != len(rows):
            continue

        first = rows[0]
        title_evidence = next(
            evidence for evidence in first["evidence"]
            if evidence.get("role") == "table_title" and evidence.get("quote_verified")
        )
        header_evidence = next(
            evidence for evidence in first["evidence"]
            if evidence.get("role") == "table_header" and evidence.get("quote_verified")
        )
        unit_evidence = [
            evidence for evidence in first["evidence"]
            if evidence.get("role") == "unit"
            and evidence.get("quote_verified")
            and evidence.get("period") in (None, "", "current")
            and _business_unit_quote_matches(first.get("current_unit"), evidence.get("evidence_quote"))
        ]
        if not unit_evidence and _business_unit_quote_matches(
            first.get("current_unit"), header_evidence.get("evidence_quote")
        ):
            unit_evidence = [header_evidence]
        component_evidence = [evidence for component in components for evidence in component["evidence"]]
        pages = sorted({
            int(page)
            for row in rows
            for page in row.get("source_pages", [])
        })
        derived_total = {
            "table_name": first.get("table_name", ""),
            "table_dimension": first.get("table_dimension", ""),
            "total_origin": "derived_from_reported_complete_segments",
            "metric_name": first.get("metric_name", ""),
            "current_value": _decimal_text(sum(values, Decimal(0))),
            "current_unit": first.get("current_unit", ""),
            "current_period": first.get("current_period", ""),
            "previous_value": "",
            "previous_unit": first.get("current_unit", ""),
            "previous_period": "",
            "reporting_scope": first.get("reporting_scope", ""),
            "currency": first.get("currency", ""),
            "reported_yoy": "",
            "comparability_note": "年报未列此拆分表合计；本数为程序加总已提取分项，不代表年报原文合计。",
            "source_pages": pages,
            "unit_match_current": True,
            "unit_match_previous": None,
            "unit_evidence_status": "current_unit_located",
            "evidence_quote": "",
            "quote_verified": False,
            "evidence": [
                dict(title_evidence), dict(header_evidence),
                *[dict(evidence) for evidence in unit_evidence],
                *[dict(evidence) for evidence in boundary_evidence[:1]],
                *component_evidence,
            ],
            "components": components,
            "table_rows_complete": True,
            "table_row_count": len(rows),
            "table_completeness_status": "model_asserted_and_boundary_quote_matched",
            "component_rows_verified": True,
            "calculation_evidence_status": "derived_from_matched_rows_with_reported_table_boundary",
        }
        revenue_totals.append(derived_total)


def _business_reconcile_derived_totals(
    revenue_totals: list[dict[str, Any]], uncertainties: list[str]
) -> None:
    for derived in revenue_totals:
        if derived.get("total_origin") != "derived_from_reported_complete_segments":
            continue
        matching_totals = [
            total for total in revenue_totals
            if total.get("total_origin") != "derived_from_reported_complete_segments"
            and _normalize_space(total.get("table_name", ""))
            == _normalize_space(derived.get("table_name", ""))
            and _business_is_overall_dimension(total.get("table_dimension", ""))
            and _business_revenue_metric_family(total.get("metric_name", ""))
            == _business_revenue_metric_family(derived.get("metric_name", ""))
            and _business_same_period(total.get("current_period"), derived.get("current_period"))
            and _business_scope_family(total.get("reporting_scope"))
            == _business_scope_family(derived.get("reporting_scope"))
            and _business_currency_family(total.get("currency"))
            == _business_currency_family(derived.get("currency"))
            and _normalize_space(total.get("current_unit", ""))
            == _normalize_space(derived.get("current_unit", ""))
            and _business_table_headers_match(derived, total)
        ]
        reported = [
            total for total in matching_totals
            if _business_record_evidence_verified(total, "current")
        ]
        if matching_totals and not reported:
            derived["reconciliation_to_reported_total"] = {
                "status": "reported_total_unit_or_evidence_not_verified",
                "reported_total_source_pages": sorted({
                    page for total in matching_totals for page in total.get("source_pages", [])
                }),
            }
            continue
        reported_versions = {
            (
                item.get("current_value"), item.get("current_unit"),
                item.get("previous_value"), item.get("previous_unit"),
                item.get("current_period"), item.get("previous_period"),
            )
            for item in reported
        }
        if not reported or len(reported_versions) != 1:
            continue
        total = max(reported, key=lambda item: len(item.get("evidence", [])))
        try:
            difference = Decimal(str(derived.get("current_value", "")).replace(",", "")) - Decimal(
                str(total.get("current_value", "")).replace(",", "")
            )
        except InvalidOperation:
            continue
        reconciliation = {
            "reported_total_value": total.get("current_value", ""),
            "reported_total_unit": total.get("current_unit", ""),
            "difference_value": _decimal_text(difference),
            "unit": derived.get("current_unit", ""),
            "status": "matches_reported_total" if difference == 0 else "differs_from_reported_total",
            "reported_total_source_pages": total.get("source_pages", []),
        }
        derived["reconciliation_to_reported_total"] = reconciliation
        if difference != 0:
            note = (
                f"{derived.get('table_dimension', '该')}分项加总为{derived.get('current_value')}"
                f"{derived.get('current_unit')}，较同表年报明确合计"
                f"{total.get('current_value')}{total.get('current_unit')}"
                f"差{_decimal_text(difference)}{derived.get('current_unit')}；"
                "保留两者并以年报明确合计计算结构占比，差异可能来自报表列示精度。"
            )
            if note not in uncertainties:
                uncertainties.append(note)


def _business_related_revenue_metric_bridges(
    revenue_totals: list[dict[str, Any]], uncertainties: list[str]
) -> list[dict[str, Any]]:
    bridges: list[dict[str, Any]] = []
    group_keys = {
        (
            _normalize_space(item.get("table_name", "")),
            _normalize_space(item.get("table_dimension", "")),
            _business_scope_family(item.get("reporting_scope")),
            _business_currency_family(item.get("currency")),
            _normalize_space(item.get("current_period", "")),
        )
        for item in revenue_totals
        if _normalize_space(item.get("table_name", ""))
        and _business_is_overall_dimension(item.get("table_dimension", ""))
        and _business_scope_family(item.get("reporting_scope"))
        and _business_currency_family(item.get("currency"))
        and _normalize_space(item.get("current_period", ""))
    }
    for title, dimension, scope, currency, period in group_keys:
        records = [
            item for item in revenue_totals
            if _normalize_space(item.get("table_name", "")) == title
            and _normalize_space(item.get("table_dimension", "")) == dimension
            and _business_scope_family(item.get("reporting_scope")) == scope
            and _business_currency_family(item.get("currency")) == currency
            and _normalize_space(item.get("current_period", "")) == period
            and _normalize_space(item.get("metric_name", "")) in {"营业总收入", "营业收入"}
            and item.get("total_origin") != "derived_from_reported_complete_segments"
            and _business_record_evidence_verified(item, "current")
        ]

        def select_metric(metric_name: str) -> dict[str, Any] | None:
            candidates = [item for item in records if _normalize_space(item.get("metric_name", "")) == metric_name]
            distinct = {
                (item.get("current_value"), item.get("current_unit"), item.get("previous_value"), item.get("previous_unit"))
                for item in candidates
            }
            return max(candidates, key=lambda item: len(item.get("evidence", []))) if candidates and len(distinct) == 1 else None

        gross = select_metric("营业总收入")
        revenue = select_metric("营业收入")
        if not gross or not revenue or not _business_table_headers_match(gross, revenue):
            continue
        gross_value = _business_money_in_yuan(gross.get("current_value"), gross.get("current_unit"), gross.get("currency"))
        revenue_value = _business_money_in_yuan(revenue.get("current_value"), revenue.get("current_unit"), revenue.get("currency"))
        if gross_value is None or revenue_value is None:
            continue
        bridge = {
            "table_name": gross.get("table_name", ""),
            "table_dimension": gross.get("table_dimension", ""),
            "higher_metric": "营业总收入",
            "higher_value": gross.get("current_value", ""),
            "higher_unit": gross.get("current_unit", ""),
            "lower_metric": "营业收入",
            "lower_value": revenue.get("current_value", ""),
            "lower_unit": revenue.get("current_unit", ""),
            "unit": "元",
            "current_period": gross.get("current_period", ""),
            "reporting_scope": gross.get("reporting_scope", ""),
            "currency": gross.get("currency", ""),
            "difference_yuan": _decimal_text(gross_value - revenue_value),
            "source_pages": sorted(set(gross.get("source_pages", []) + revenue.get("source_pages", []))),
            "evidence_status": "both_reported_rows_title_header_unit_and_values_matched",
            "evidence": {
                "higher_metric": gross.get("evidence", []),
                "lower_metric": revenue.get("evidence", []),
            },
        }
        bridges.append(bridge)

        conflicting_uncertainties = []
        expected_amounts = {
            Decimal(str(value).replace(",", ""))
            for value in (gross.get("current_value"), revenue.get("current_value"), bridge["difference_yuan"])
        }
        for uncertainty in uncertainties:
            if "营业总收入" not in uncertainty or "营业收入" not in uncertainty or not re.search(r"差(?:异|额)", uncertainty):
                continue
            decimal_tokens = re.findall(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*\.\d+", uncertainty)
            try:
                has_unmatched_amount = any(
                    Decimal(token.replace(",", "")) not in expected_amounts for token in decimal_tokens
                )
            except InvalidOperation:
                has_unmatched_amount = True
            if has_unmatched_amount:
                conflicting_uncertainties.append(uncertainty)
        if conflicting_uncertainties:
            uncertainties[:] = [item for item in uncertainties if item not in conflicting_uncertainties]
            note = (
                f"年报同一张{bridge['table_name']}分别列示营业总收入{gross.get('current_value')}"
                f"{gross.get('current_unit')}与营业收入{revenue.get('current_value')}"
                f"{revenue.get('current_unit')}；程序据原表计算差额为{bridge['difference_yuan']}元。"
                "模型对该差额的复述数值不一致，已以程序复算结果替代；差额具体构成应以利润表明细为准。"
            )
            uncertainties.append(note)
    return bridges


def _business_apply_segment_shares(
    segment: dict[str, Any], revenue_totals: list[dict[str, Any]]
) -> None:
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
        segment_metric_known and current_total_record
        and _business_revenue_metric_family(current_total_record.get("metric_name", ""))
        == _business_revenue_metric_family(segment["metric_name"])
    )
    previous_metric_matches = bool(
        segment_metric_known and previous_total_record
        and _business_revenue_metric_family(previous_total_record.get("metric_name", ""))
        == _business_revenue_metric_family(segment["metric_name"])
    )
    current_period_matches = bool(
        current_total_record
        and _business_same_period(segment["current_period"], current_total_record.get("current_period"))
    )
    previous_period_matches = bool(
        previous_total_record
        and _business_same_period(segment["previous_period"], previous_total_record.get("previous_period"))
    )
    current_scope_matches = bool(
        current_total_record and _business_scope_family(segment["reporting_scope"])
        and _business_scope_family(segment["reporting_scope"])
        == _business_scope_family(current_total_record.get("reporting_scope"))
    )
    previous_scope_matches = bool(
        previous_total_record and _business_scope_family(segment["reporting_scope"])
        and _business_scope_family(segment["reporting_scope"])
        == _business_scope_family(previous_total_record.get("reporting_scope"))
    )
    current_currency_matches = bool(
        current_total_record and _business_currency_family(segment["currency"])
        and _business_currency_family(segment["currency"])
        == _business_currency_family(current_total_record.get("currency"))
    )
    previous_currency_matches = bool(
        previous_total_record and _business_currency_family(segment["currency"])
        and _business_currency_family(segment["currency"])
        == _business_currency_family(previous_total_record.get("currency"))
    )
    current_quotes_verified = bool(
        _business_record_evidence_verified(segment, "current")
        and current_total_record
        and _business_record_evidence_verified(current_total_record, "current")
    )
    previous_quotes_verified = bool(
        _business_record_evidence_verified(segment, "previous")
        and previous_total_record
        and _business_record_evidence_verified(previous_total_record, "previous")
    )
    segment["current_share_percent"] = (
        _business_percentage(current_amount, total_current)
        if current_amount is not None and total_current is not None
        and current_period_matches and current_metric_matches and current_scope_matches
        and current_currency_matches and current_quotes_verified else None
    )
    segment["current_share_denominator"] = current_total_record
    segment["current_share_note"] = _business_share_note(
        current_amount, total_current, period_matches=current_period_matches,
        metric_matches=current_metric_matches, scope_matches=current_scope_matches,
        currency_matches=current_currency_matches, quotes_verified=current_quotes_verified,
    )
    segment["previous_share_percent"] = (
        _business_percentage(previous_amount, total_previous)
        if previous_amount is not None and total_previous is not None
        and previous_period_matches and previous_metric_matches and previous_scope_matches
        and previous_currency_matches and previous_quotes_verified else None
    )
    segment["previous_share_denominator"] = previous_total_record
    segment["previous_share_note"] = _business_share_note(
        previous_amount, total_previous, period_matches=previous_period_matches,
        metric_matches=previous_metric_matches, scope_matches=previous_scope_matches,
        currency_matches=previous_currency_matches, quotes_verified=previous_quotes_verified,
    )
    if (
        segment.get("current_share_percent") is not None
        and segment.get("previous_share_percent") is not None
        and segment.get("cross_period_comparable") is True
        and current_amount is not None and previous_amount is not None
        and total_current is not None and total_previous is not None
    ):
        segment["share_change_percentage_points"] = _business_percent_text(
            current_amount / total_current * Decimal(100)
            - previous_amount / total_previous * Decimal(100)
        )
    else:
        segment["share_change_percentage_points"] = None


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
        return "收入原文摘录或单位依据尚未通过核验。"
    if numerator is None or denominator is None:
        return "收入数值或人民币单位无法识别。"
    if denominator <= 0:
        return "总收入基数为零或负数。"
    return ""
