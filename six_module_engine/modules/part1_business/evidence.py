"""Evidence helpers owned by the business-analysis module."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from backend.core.legacy_support import _normalize_space

_BUSINESS_NUMERIC_TOKEN = re.compile(
    r"(?P<raw>[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?P<unit>个百分点|亿元|万元|千元|GWh|MWh|万吨|吨|%|元|年|个|家|座|次|天|倍)?"
)
_BUSINESS_MONEY_UNIT_SCALE = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
}


def _business_topic_numeric_claims(record: dict[str, Any], page_text: Callable[[int], str | None]) -> list[str]:
    """Flag observation numbers that cannot be located on any cited source page.

    This is a lexical warning, not proof that a matched number has the right meaning.
    """
    pages = record.get("source_pages", [])
    source_text = "\n".join(page_text(int(page)) or "" for page in pages)
    source_values: list[tuple[Decimal, str]] = []
    for match in _BUSINESS_NUMERIC_TOKEN.finditer(source_text):
        try:
            value = abs(Decimal(match.group("raw").replace(",", "")))
        except InvalidOperation:
            continue
        source_values.append((value, match.group("unit") or ""))

    unlocated: list[str] = []
    observation = str(record.get("observation", ""))
    for match in _BUSINESS_NUMERIC_TOKEN.finditer(observation):
        raw = match.group("raw")
        unit = match.group("unit") or ""
        try:
            claim_value = abs(Decimal(raw.replace(",", "")))
        except InvalidOperation:
            continue
        if any(value == claim_value for value, _ in source_values):
            continue

        matched = False
        claim_scale = _BUSINESS_MONEY_UNIT_SCALE.get(unit)
        if claim_scale:
            decimals = len(raw.rsplit(".", 1)[1]) if "." in raw else 0
            quantum = Decimal(1).scaleb(-decimals)
            for source_value, source_unit in source_values:
                source_scale = _BUSINESS_MONEY_UNIT_SCALE.get(source_unit)
                if source_scale:
                    converted = source_value * source_scale / claim_scale
                    if converted.quantize(quantum, rounding=ROUND_HALF_UP) == claim_value:
                        matched = True
                        break
                elif source_value > 1_000_000:
                    for assumed_scale in _BUSINESS_MONEY_UNIT_SCALE.values():
                        converted = source_value * assumed_scale / claim_scale
                        if converted.quantize(quantum, rounding=ROUND_HALF_UP) == claim_value:
                            matched = True
                            break
                    if matched:
                        break
        if not matched:
            display = f"{raw}{unit}"
            if display not in unlocated:
                unlocated.append(display)
    return unlocated


def _business_unit_quote_matches(unit: Any, quote: Any) -> bool:
    """Check whether a reported monetary unit is present in its cited excerpt."""
    normalized_unit = re.sub(r"\s+", "", str(unit or "")).replace("单位：", "").replace("单位:", "")
    normalized_quote = re.sub(r"\s+", "", str(quote or ""))
    patterns = {
        # Match the base yuan unit without accepting a scaled unit such as
        # 亿元/万元 or a foreign-currency yuan suffix such as 美元/港元.
        "元": r"(?<![亿万千美港澳台新])元",
        "千元": r"千元",
        "万元": r"万元",
        "亿元": r"亿元",
    }
    pattern = patterns.get(normalized_unit)
    return bool(pattern and re.search(pattern, normalized_quote))


def _business_unit_is_verified(record: dict[str, Any], value_kind: str = "current") -> bool:
    unit = record.get(f"{value_kind}_unit")
    evidence = record.get("evidence")
    if not unit or not isinstance(evidence, list):
        return False
    unit_items = [
        item for item in evidence
        if isinstance(item, dict)
        and str(item.get("role") or "").casefold() == "unit"
        and item.get("quote_verified")
        and item.get("source_pages")
        and item.get("period") in (None, "", value_kind)
    ]
    candidates = unit_items or [
        item for item in evidence
        if isinstance(item, dict)
        and str(item.get("role") or "").casefold() == "table_header"
        and item.get("quote_verified")
        and item.get("source_pages")
    ]
    return any(_business_unit_quote_matches(unit, item.get("evidence_quote")) for item in candidates)


def _annotate_business_unit_evidence(record: dict[str, Any]) -> None:
    record["unit_match_current"] = _business_unit_is_verified(record, "current")
    has_previous = bool(str(record.get("previous_value") or "").strip())
    record["unit_match_previous"] = (
        _business_unit_is_verified(record, "previous") if has_previous else None
    )
    if not record["unit_match_current"]:
        record["unit_evidence_status"] = "current_unit_not_located"
    elif has_previous and not record["unit_match_previous"]:
        record["unit_evidence_status"] = "previous_unit_not_located"
    elif has_previous:
        record["unit_evidence_status"] = "current_and_previous_units_located"
    else:
        record["unit_evidence_status"] = "current_unit_located"


def _annotate_business_row_value_evidence(record: dict[str, Any]) -> None:
    evidence = record.get("evidence")
    if not isinstance(evidence, list):
        return
    for item in evidence:
        if not isinstance(item, dict) or str(item.get("role") or "").casefold() != "row_value":
            continue
        item["current_value_match"] = bool(
            item.get("quote_verified")
            and _business_quote_contains_values(item.get("evidence_quote", ""), record, ("current",))
        )
        item["previous_value_match"] = bool(
            item.get("quote_verified")
            and _business_quote_contains_values(item.get("evidence_quote", ""), record, ("previous",))
        )
        item["row_values_match"] = bool(
            item["current_value_match"]
            and (not str(record.get("previous_value") or "").strip() or item["previous_value_match"])
        )


def _business_quote_contains_values(
    quote: str, record: dict[str, Any], value_kinds: tuple[str, ...] = ("current", "previous")
) -> bool:
    normalized_quote = _normalize_space(quote)
    name = _normalize_space(record.get("name", ""))
    if name:
        source_name = re.sub(
            r"[（(](?:分)?(?:行业|地区|产品|报告分部|经营分部|销售模式)[）)]$",
            "",
            name,
        )
        if name not in normalized_quote and (not source_name or source_name not in normalized_quote):
            return False
    if not name:
        metric = _normalize_space(record.get("metric_name", ""))
        if metric and metric not in normalized_quote and not any(
            marker in normalized_quote for marker in ("合计", "总计")
        ):
            return False
    values = [record.get(f"{value_kind}_value") for value_kind in value_kinds]
    expected_values = [value for value in values if str(value or "").strip()]
    if not expected_values:
        return False
    quote_numbers: set[Decimal] = set()
    for token in re.findall(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?", quote):
        try:
            quote_numbers.add(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    for value in expected_values:
        try:
            expected = Decimal(str(value).replace(",", "").strip())
        except InvalidOperation:
            if _normalize_space(value) not in normalized_quote:
                return False
        else:
            if expected not in quote_numbers:
                return False
    return True
