"""现金流模块自己的事实格式、页码与原文证据核验。"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Any


UNIT_TO_YUAN = {
    "元": Decimal("1"),
    "人民币元": Decimal("1"),
    "CNY": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
}


def parse_decimal(value: Any) -> Decimal:
    """Parse a model-provided numeric string without accepting prose or NaN."""
    if isinstance(value, bool) or value is None:
        raise ValueError("数值必须是明确的十进制数字")
    cleaned = str(value).strip().replace(",", "").replace("，", "")
    if not cleaned or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        raise ValueError("数值格式无效，请只提供原报表数字，不附带单位或解释")
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("数值无法转换为十进制") from exc
    if not number.is_finite():
        raise ValueError("不接受无穷值或非数")
    return number


def normalize_quote(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("\u3000", "")


def _numbers_in_quote(quote: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for match in re.finditer(r"(?<![A-Za-z])[-+]?(?:\d[\d,，]*(?:\.\d+)?|\.\d+)", quote):
        token = match.group(0).replace(",", "").replace("，", "")
        try:
            number = Decimal(token)
        except InvalidOperation:
            continue
        before = quote[max(0, match.start() - 2):match.start()]
        after = quote[match.end():match.end() + 2]
        if "(" in before and ("）" in after or ")" in after):
            number = -abs(number)
        found.add(number)
    return found


def validate_evidence(
    evidence: Any,
    *,
    pages_by_number: dict[int, str],
    raw_value: Decimal | None = None,
) -> list[dict[str, Any]]:
    """Check physical-page bounds, quote occurrence and number occurrence.

    A textual match is deliberately not represented as proof of table-row,
    period or consolidation-scope identity; those need separate review.
    """
    if not isinstance(evidence, list):
        evidence = []
    validated: list[dict[str, Any]] = []
    for item in evidence[:6]:
        if not isinstance(item, dict):
            continue
        try:
            page_number = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        quote = str(item.get("quote", "")).strip()[:1200]
        source_page = pages_by_number.get(page_number, "")
        page_valid = bool(source_page)
        quote_matches = bool(quote and source_page and normalize_quote(quote) in normalize_quote(source_page))
        table_label = str(item.get("table_label", "")).strip()[:180]
        row_label = str(item.get("row_label", "")).strip()[:180]
        column_label = str(item.get("column_label", "")).strip()[:180]
        row_matches_quote = (
            normalize_quote(row_label) in normalize_quote(quote)
            if row_label else None
        )
        column_matches_page = (
            normalize_quote(column_label) in normalize_quote(source_page)
            if column_label and source_page else None
        )
        table_matches_page = (
            normalize_quote(table_label) in normalize_quote(source_page)
            if table_label and source_page else None
        )
        numeric_matches: bool | None = None
        if raw_value is not None:
            numeric_matches = raw_value in _numbers_in_quote(quote)
        validated.append(
            {
                "page": page_number,
                "quote": quote,
                "table_label": table_label,
                "row_label": row_label,
                "column_label": column_label,
                "page_valid": page_valid,
                "quote_matches_page": quote_matches,
                "number_appears_in_quote": numeric_matches,
                "row_label_matches_quote": row_matches_quote,
                "column_label_matches_page": column_matches_page,
                "table_label_matches_page": table_matches_page,
                "row_column_identity": "model_asserted_unverified",
                "reporting_scope_identity": "model_asserted_unverified",
            }
        )
    return validated


def build_fact(
    item: dict[str, Any],
    *,
    report_id: str,
    pages_by_number: dict[int, str],
) -> dict[str, Any]:
    label = str(item.get("label", "")).strip()[:220]
    metric_key = str(item.get("metric_key", "")).strip()[:120]
    period = str(item.get("period", "")).strip()[:80]
    scope = str(item.get("scope", "未知")).strip()[:80] or "未知"
    currency = str(item.get("currency", "未知")).strip()[:24] or "未知"
    unit = str(item.get("unit", "未知")).strip()[:24] or "未知"
    if not label:
        raise ValueError("每条事实都必须填写报告中的项目名称")
    raw_value = parse_decimal(item.get("value"))
    evidence = validate_evidence(item.get("evidence"), pages_by_number=pages_by_number, raw_value=raw_value)
    valid_evidence = [
        ref for ref in evidence
        if ref["page_valid"] and ref["quote_matches_page"] and ref["number_appears_in_quote"]
    ]

    multiplier = UNIT_TO_YUAN.get(unit)
    normalized_value: str | None = None
    normalized_unit = "unknown"
    if multiplier is not None and currency.upper() in {"CNY", "RMB", "人民币"}:
        normalized_value = str(raw_value * multiplier)
        normalized_unit = "CNY_yuan"

    dimensions = str(item.get("dimensions", ""))[:240]
    period_kind = str(item.get("period_kind", "unknown"))[:32]
    source_table = str(item.get("source_table", "未知"))[:160]
    evidence_identity = ";".join(
        f"{ref['page']}:{ref['row_label']}:{ref['column_label']}:{ref['quote']}"
        for ref in evidence
    )
    identity = "|".join((
        report_id,
        metric_key or label,
        label,
        str(raw_value),
        period,
        period_kind,
        scope,
        currency,
        unit,
        dimensions,
        source_table,
        evidence_identity,
    ))
    fact_id = "cf_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return {
        "fact_id": fact_id,
        "report_id": report_id,
        "metric_key": metric_key,
        "original_label": label,
        "dimensions": dimensions,
        "value": str(raw_value),
        "unit": unit,
        "normalized_value": normalized_value,
        "normalized_unit": normalized_unit,
        "currency": currency,
        "period": period or "未知",
        "period_kind": period_kind,
        "scope": scope,
        "measurement_basis": str(item.get("measurement_basis", "未知"))[:100],
        "source_table": source_table,
        "evidence": evidence,
        "evidence_status": "numeric_quote_page_matched" if valid_evidence else "needs_source_review",
        "calculable": bool(valid_evidence and normalized_value is not None),
        "identity_status": "period_scope_row_column_need_review",
    }
