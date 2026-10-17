"""Compatibility semantics shared by the two existing analysis modules.

New modules may use, adapt, or ignore these helpers according to their needs.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from backend.core.legacy_runtime import _number


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()

def _business_evidence(
    item: dict[str, Any],
    allowed_pages: set[int],
    page_text: Callable[[int], str | None],
) -> tuple[list[int], str, bool, bool]:
    refs: list[int] = []
    invalid_page = False
    raw_refs = item.get("source_pages", [])
    for reference in raw_refs if isinstance(raw_refs, list) else []:
        try:
            page = int(reference)
        except (TypeError, ValueError):
            invalid_page = True
            continue
        if page in allowed_pages and page not in refs:
            refs.append(page)
        else:
            invalid_page = True
    quote = str(item.get("evidence_quote") or "").strip()[:360]
    normalized_quote = _normalize_space(quote)
    quote_verified = bool(
        normalized_quote
        and any(normalized_quote in _normalize_space(page_text(page) or "") for page in refs)
    )
    return refs[:3], quote, quote_verified, invalid_page

def _business_currency_family(value: Any) -> str:
    currency = re.sub(r"\s+", "", str(value or "")).upper()
    if any(token in currency for token in ("人民币", "RMB", "CNY")):
        return "CNY"
    return currency

def _business_scope_family(value: Any) -> str:
    scope = re.sub(r"\s+", "", str(value or "")).casefold()
    if not scope:
        return ""
    if any(token in scope for token in ("合并", "集团口径")):
        return "consolidated"
    if any(token in scope for token in ("母公司", "公司本部")):
        return "parent"
    return scope

def _business_period_year(value: Any) -> int | None:
    years = re.findall(r"(?:19|20)\d{2}", str(value or ""))
    return int(years[0]) if years and len(set(years)) == 1 else None

def _business_period_kind(value: Any) -> str:
    period = re.sub(r"\s+", "", str(value or "")).upper()
    quarter = re.search(r"(?:第)?([1-4])(?:季度|季)|Q([1-4])", period)
    if quarter:
        return f"Q{quarter.group(1) or quarter.group(2)}"
    if "上半年" in period or "1-6月" in period or "1—6月" in period:
        return "H1"
    if "下半年" in period or "7-12月" in period or "7—12月" in period:
        return "H2"
    if re.search(r"\d{1,2}月", period) and not any(token in period for token in ("1-12月", "1—12月", "全年")):
        return "partial"
    return "annual"

def _business_same_period(left: Any, right: Any) -> bool:
    left_year = _business_period_year(left)
    right_year = _business_period_year(right)
    if left_year is not None and right_year is not None:
        return left_year == right_year and _business_period_kind(left) == _business_period_kind(right)
    return bool(left and right and _normalize_space(str(left)) == _normalize_space(str(right)))

def _business_adjacent_annual_periods(current: Any, previous: Any) -> bool:
    current_year = _business_period_year(current)
    previous_year = _business_period_year(previous)
    return (
        current_year is not None
        and previous_year is not None
        and current_year == previous_year + 1
        and _business_period_kind(current) == "annual"
        and _business_period_kind(previous) == "annual"
    )

def _business_money_in_yuan(value: Any, unit: Any, currency: Any = "") -> Decimal | None:
    currency_family = _business_currency_family(currency)
    if currency_family and currency_family != "CNY":
        return None
    try:
        amount = _number(value)
    except (InvalidOperation, ValueError):
        return None
    normalized_unit = re.sub(r"\s+", "", str(unit or "")).upper()
    normalized_unit = normalized_unit.replace("人民币", "").replace("RMB", "").replace("CNY", "")
    normalized_unit = normalized_unit.replace("单位:", "").replace("单位：", "").replace(":", "").replace("：", "")
    factors = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000), "亿元": Decimal(100000000)}
    factor = factors.get(normalized_unit)
    return amount * factor if factor is not None else None
