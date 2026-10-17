"""Module-two evidence normalization and source validation."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Callable


def normalize_quote(value: Any) -> str:
    text = str(value or "").translate(str.maketrans({
        "“": '"', "”": '"', "‘": "'", "’": "'",
        "−": "-", "－": "-", "﹣": "-", "–": "-", "—": "-",
        "：": ":", "，": ",", "（": "(", "）": ")",
    }))
    return re.sub(r"\s+", "", text).casefold()


def verify_evidence(
    item: dict[str, Any],
    allowed_pages: set[int],
    page_text: Callable[[int], str | None],
) -> dict[str, Any]:
    """Verify separate evidence fragments against the exact page text.

    Fragments remain separate so page labels, ellipses, and model commentary
    can never accidentally become part of a supposedly verbatim quotation.
    """
    raw_segments = item.get("evidence_segments")
    if not isinstance(raw_segments, list):
        raw_segments = [{
            "source_pages": item.get("source_pages", []),
            "quote": item.get("evidence_quote", ""),
        }]
    segments: list[dict[str, Any]] = []
    invalid_page = False
    verified_pages: list[int] = []
    quotes: list[str] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        quote = str(raw.get("quote", raw.get("evidence_quote", "")) or "").strip()[:700]
        quote_key = normalize_quote(quote)
        refs = raw.get("source_pages", raw.get("pages", []))
        if not isinstance(refs, list):
            refs = [refs]
        valid_refs: list[int] = []
        fragment_verified = False
        for ref in refs:
            try:
                page = int(ref)
            except (TypeError, ValueError):
                invalid_page = True
                continue
            if page not in allowed_pages or page < 1:
                invalid_page = True
                continue
            valid_refs.append(page)
            if quote_key and quote_key in normalize_quote(page_text(page)):
                fragment_verified = True
                if page not in verified_pages:
                    verified_pages.append(page)
        if quote:
            quotes.append(quote)
        segments.append({
            "source_pages": valid_refs,
            "quote": quote,
            "quote_verified": fragment_verified,
        })
    verified = [segment for segment in segments if segment["quote_verified"]]
    return {
        "source_pages": list(dict.fromkeys(page for seg in segments for page in seg["source_pages"]))[:8],
        "verified_pages": verified_pages[:8],
        "evidence_quote": "\n".join(quotes)[:1400],
        "evidence_segments": segments[:8],
        "quote_verified": bool(verified),
        "invalid_page_reference": invalid_page,
    }


def verify_financial_row_context(
    *,
    label: Any,
    current_value: Any,
    previous_value: Any,
    current_unit: Any,
    previous_unit: Any,
    current_period: Any,
    previous_period: Any,
    current_scope: str,
    previous_scope: str,
    current_currency: str,
    previous_currency: str,
    evidence_segments: list[dict[str, Any]],
    page_text: Callable[[int], str | None],
    scope_family: Callable[[Any], str],
    require_scope: bool = True,
) -> dict[str, Any]:
    """Verify row identity and attach each reported value to its stated period.

    A quote match alone proves only that text exists. This check anchors the
    reported label and amount(s) to a row in the full extracted page, then
    checks the nearby table header for unit, period, scope, and currency. Facts
    that lack any required context remain visible to the user but are blocked
    from calculations.
    """
    label_key = _financial_label_key(label)
    current_present = current_value not in (None, "")
    previous_present = previous_value not in (None, "")
    current_year = _period_year(current_period)
    previous_year = _period_year(previous_period)
    current_family = scope_family(current_scope)
    previous_family = scope_family(previous_scope)
    current_currency_family = _currency_family(current_currency)
    previous_currency_family = _currency_family(previous_currency)
    reasons: list[str] = []
    matched_pages: list[int] = []
    current_value_verified = False
    previous_value_verified = not previous_present
    current_unit_verified = False
    previous_unit_verified = not previous_present
    current_scope_verified = False
    previous_scope_verified = not previous_present
    current_currency_verified = False
    previous_currency_verified = not previous_present
    context_pages: list[int] = []
    same_table_period_pair_verified = False
    currency_evidence_modes: set[str] = set()

    for segment in evidence_segments:
        if not segment.get("quote_verified"):
            continue
        for raw_page in segment.get("source_pages", []):
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            text = page_text(page)
            if not text:
                continue
            required_units = [
                unit for unit, present in ((current_unit, current_present), (previous_unit, previous_present))
                if present
            ]
            for row_tail, local_header in _matching_financial_rows(text, label_key):
                local_header_normalized = normalize_quote(local_header)
                local_scope_context = f"{local_header_normalized} {label_key}"
                local_period_ok = (
                    current_year is not None and previous_year is not None
                    and _header_has_periods(local_header_normalized, current_year, previous_year)
                ) if previous_present else (
                    current_year is not None and _header_has_current_period(local_header_normalized, current_year)
                )
                local_header_complete = bool(
                    _header_has_units(local_header_normalized, required_units)
                    and local_period_ok
                    and (not require_scope or _header_has_scope(local_scope_context, current_family, previous_family))
                    and _header_has_currency(local_header_normalized, current_currency)
                )
                inherited_header = ""
                inherited_page: int | None = None
                if not local_header_complete and page > 1 and not _has_conflicting_table_header(
                    local_header_normalized, required_units, current_currency, current_family, previous_family, require_scope
                ):
                    prior_text = page_text(page - 1)
                    prior_header = _extract_inherited_header(
                        prior_text or "",
                        current_year=current_year,
                        previous_year=previous_year,
                        units=required_units,
                        current_scope=current_family,
                        previous_scope=previous_family,
                        currency=current_currency,
                        require_previous=previous_present,
                        require_scope=require_scope,
                    )
                    prior_header_normalized = normalize_quote(prior_header)
                    prior_period_ok = (
                        current_year is not None and previous_year is not None
                        and _header_has_periods(prior_header_normalized, current_year, previous_year)
                    ) if previous_present else (
                        current_year is not None and _header_has_current_period(prior_header_normalized, current_year)
                    )
                    if (
                        prior_header
                        and _header_has_units(prior_header_normalized, required_units)
                        and prior_period_ok
                        and (not require_scope or _header_has_scope(f"{prior_header_normalized} {label_key}", current_family, previous_family))
                        and _header_has_currency(prior_header_normalized, current_currency)
                    ):
                        inherited_header = prior_header
                        inherited_page = page - 1
                # Put an inherited table header after the continuation-page
                # preamble so a page title such as "2024 年年度报告" cannot
                # override the actual 2024/2023 column headings.
                header = f"{local_header} {inherited_header}".strip()
                scope_context = f"{header} {label_key}"
                matched_pages.append(page)
                current_position = _amount_position(row_tail, current_value) if current_present else -1
                previous_position = _amount_position(row_tail, previous_value) if previous_present else -1
                if inherited_page is not None and (current_position >= 0 or previous_position >= 0):
                    context_pages.append(inherited_page)
                both_values_on_row = current_present and previous_present and current_position >= 0 and previous_position >= 0
                periods_in_order = bool(
                    current_year is not None and previous_year is not None
                    and current_year == previous_year + 1
                    and _header_has_periods(header, current_year, previous_year)
                )
                if both_values_on_row:
                    # When two period values share a row, their left-to-right
                    # order must agree with the current/previous headers.
                    if current_position <= previous_position and periods_in_order:
                        current_value_verified = True
                        previous_value_verified = True
                        current_unit_match = _header_has_units(header, [current_unit])
                        previous_unit_match = _header_has_units(header, [previous_unit])
                        current_currency_match = _header_has_currency(header, current_currency)
                        previous_currency_match = _header_has_currency(header, previous_currency)
                        current_unit_verified |= current_unit_match
                        previous_unit_verified |= previous_unit_match
                        current_scope_verified |= _header_has_scope(scope_context, current_family, current_family)
                        previous_scope_verified |= _header_has_scope(scope_context, previous_family, previous_family)
                        current_currency_verified |= current_currency_match
                        previous_currency_verified |= previous_currency_match
                        currency_mode = _header_currency_mode(header, current_currency)
                        if current_currency_match and previous_currency_match and currency_mode:
                            currency_evidence_modes.add(currency_mode)
                        explicit_scope = _explicit_statement_scope(header)
                        declared_scope_conflict = bool(explicit_scope) and any(
                            family and family != explicit_scope
                            for family in (current_family, previous_family)
                        )
                        same_table_period_pair_verified |= bool(
                            current_unit_match and previous_unit_match
                            and current_currency_match and previous_currency_match
                            and not declared_scope_conflict
                        )
                    # A reversed pair is deliberately not accepted as two
                    # separate single-period matches from this same row.
                    continue

                if current_present and current_position >= 0 and current_year is not None:
                    if _header_has_current_period(header, current_year):
                        current_value_verified = True
                if previous_present and previous_position >= 0 and previous_year is not None:
                    if _header_has_previous_period(header, previous_year):
                        previous_value_verified = True

                if current_present and current_position >= 0 and _header_has_current_period(header, current_year):
                    current_unit_verified |= _header_has_units(header, [current_unit])
                    current_scope_verified |= _header_has_scope(scope_context, current_family, current_family)
                    current_currency_verified |= _header_has_currency(header, current_currency)
                if previous_present and previous_position >= 0 and _header_has_previous_period(header, previous_year):
                    previous_unit_verified |= _header_has_units(header, [previous_unit])
                    previous_scope_verified |= _header_has_scope(scope_context, previous_family, previous_family)
                    previous_currency_verified |= _header_has_currency(header, previous_currency)
                if current_currency_verified and previous_currency_verified:
                    currency_mode = _header_currency_mode(header, current_currency)
                    if currency_mode:
                        currency_evidence_modes.add(currency_mode)

                if both_values_on_row and current_position <= previous_position and periods_in_order:
                    current_unit_verified |= _header_has_units(header, [current_unit])
                    previous_unit_verified |= _header_has_units(header, [previous_unit])
                    current_scope_verified |= _header_has_scope(scope_context, current_family, current_family)
                    previous_scope_verified |= _header_has_scope(scope_context, previous_family, previous_family)
                    current_currency_verified |= _header_has_currency(header, current_currency)
                    previous_currency_verified |= _header_has_currency(header, previous_currency)

    row_verified = current_value_verified and previous_value_verified
    unit_verified = current_unit_verified and previous_unit_verified
    period_verified = current_value_verified and previous_value_verified
    scope_verified = current_scope_verified and previous_scope_verified and bool(current_family) and current_family == previous_family
    currency_verified = current_currency_verified and previous_currency_verified and bool(current_currency_family) and current_currency_family == previous_currency_family

    if not label_key or not matched_pages:
        reasons.append("年报原文中未找到与项目名称对应的引用页")
    elif not current_value_verified or not previous_value_verified:
        reasons.append("项目名称对应行中的金额与提取值或期间列未能一一对应")
    if current_present and not current_unit_verified or previous_present and not previous_unit_verified:
        reasons.append("对应金额附近的表头未能核实单位")
    if current_year is None or (previous_present and previous_year is None):
        reasons.append("期间未能识别为明确年度")
    elif previous_present and (current_year is None or previous_year != current_year - 1 or not period_verified):
        reasons.append("附近表头无法核实本期、上期及其顺序")
    if require_scope and (not current_family or current_family != previous_family or not current_scope_verified or (previous_present and not previous_scope_verified)):
        reasons.append("附近表头未能核实合并/母公司范围")
    if not current_currency_family or current_currency_family != previous_currency_family or not current_currency_verified or (previous_present and not previous_currency_verified):
        reasons.append("附近表头未能核实币种")
    supported = bool(
        current_present and current_value_verified and current_unit_verified and (current_scope_verified or not require_scope)
        and current_currency_verified
        and (not previous_present or (
            previous_value_verified and previous_unit_verified and (previous_scope_verified or not require_scope)
            and previous_currency_verified and current_year is not None and previous_year == current_year - 1
            and current_family == previous_family and current_currency_family == previous_currency_family
        ))
    )
    current_context_verified = bool(
        current_present and current_value_verified and current_unit_verified
        and (current_scope_verified or not require_scope) and current_currency_verified
    )
    previous_context_verified = bool(
        not previous_present or (
            previous_value_verified and previous_unit_verified
            and (previous_scope_verified or not require_scope) and previous_currency_verified
        )
    )
    return {
        "context_verified": supported,
        "current_context_verified": current_context_verified,
        "previous_context_verified": previous_context_verified,
        "row_verified": row_verified,
        "unit_verified": unit_verified,
        "period_verified": period_verified,
        "scope_verified": scope_verified,
        "current_scope_verified": current_scope_verified,
        "previous_scope_verified": previous_scope_verified,
        "same_table_period_pair_verified": same_table_period_pair_verified,
        "currency_verified": currency_verified,
        "currency_evidence_mode": (
            "mixed" if len(currency_evidence_modes) > 1 else
            next(iter(currency_evidence_modes), "unverified")
        ),
        "current_value_verified": current_value_verified,
        "previous_value_verified": previous_value_verified,
        "current_unit_verified": current_unit_verified,
        "previous_unit_verified": previous_unit_verified,
        "verified_context_pages": list(dict.fromkeys(matched_pages + context_pages))[:8],
        "context_reasons": reasons,
    }


def _financial_label_key(value: Any) -> str:
    label = str(value or "").strip()
    # Model output may append a scope/footnote qualifier. Keep accounting
    # qualifiers such as "losses shown with a minus sign" because they are
    # part of the printed row label.
    label = re.sub(r"[（(](?:合并|母公司)?(?:利润表|财务报表|附注|注释)[^）)]*[）)]$", "", label)
    return normalize_quote(label)


def _matching_financial_rows(text: str, label_key: str, inherited_header: str = "") -> list[tuple[str, str]]:
    lines = [normalize_quote(line) for line in str(text or "").splitlines()]
    matches: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        start = -1
        span = 1
        combined = ""
        if not label_key:
            continue
        for candidate_span in range(1, min(5, len(lines) - index + 1)):
            candidate = "".join(lines[index:index + candidate_span])
            candidate_start = candidate.find(label_key)
            if candidate_start >= 0:
                start = candidate_start
                span = candidate_span
                combined = candidate
                break
        if start < 0:
            continue
        row_parts = [combined[start + len(label_key):]]
        numeric_lines = 0
        for following in lines[index + span:index + span + 8]:
            if not following:
                continue
            if _is_note_reference_line(following):
                row_parts.append(following)
                continue
            if _is_amount_only_line(following):
                row_parts.append(following)
                numeric_lines += 1
                if numeric_lines >= 4:
                    break
                continue
            break
        unit_positions = [position for position, value in enumerate(lines[:index]) if "单位" in value]
        if unit_positions:
            unit_position = unit_positions[-1]
            marker_positions = [
                position for position, value in enumerate(lines[max(0, unit_position - 8):unit_position], max(0, unit_position - 8))
                if _is_financial_table_marker(value)
            ]
            header_start = marker_positions[-1] if marker_positions else max(0, unit_position - 2)
        else:
            marker_positions = [position for position, value in enumerate(lines[:index]) if _is_financial_table_marker(value)]
            header_start = marker_positions[-1] if marker_positions else max(0, index - 16)
        header = " ".join(lines[header_start:index])[-1200:]
        if inherited_header:
            header = f"{header} {inherited_header}"
        matches.append((" ".join(part for part in row_parts if part), header))
    return matches


def _is_financial_table_marker(value: str) -> bool:
    return any(token in value for token in (
        "合并利润表", "母公司利润表", "合并资产负债表", "母公司资产负债表",
        "合并现金流量表", "母公司现金流量表", "合并财务报表项目注释",
        "母公司财务报表项目注释", "非经常性损益项目", "主要会计数据", "主要财务数据",
    ))


def _has_conflicting_table_header(
    header: str,
    units: list[Any],
    currency: str,
    current_scope: str,
    previous_scope: str,
    require_scope: bool,
) -> bool:
    """Do not borrow a previous page's header over explicit local table metadata."""
    if "单位" in header and not _header_has_units(header, units):
        return True
    if "币种" in header and not _header_has_currency(header, currency):
        return True
    if _is_financial_table_marker(header) and not _header_has_scope(header, current_scope, previous_scope):
        return True
    return False


def _extract_inherited_header(
    text: str,
    *,
    current_year: int | None,
    previous_year: int | None,
    units: list[Any],
    current_scope: str,
    previous_scope: str,
    currency: str,
    require_previous: bool,
    require_scope: bool,
) -> str:
    """Carry only the preceding page's matching table headers to a continuation page."""
    lines = [normalize_quote(line) for line in str(text or "").splitlines()]
    if not lines:
        return ""

    unit_positions = [
        index for index, line in enumerate(lines)
        if "单位" in line and any(normalize_quote(unit).replace("人民币", "") in line for unit in units if unit)
    ]
    currency_key = normalize_quote(currency)
    if currency_key in {"人民币", "cny", "rmb"}:
        currency_lines = [index for index, line in enumerate(lines) if "币种" in line]
        if currency_lines:
            currency_positions = [index for index in currency_lines if any(token in lines[index] for token in ("人民币", "cny", "rmb"))]
        elif unit_positions and _header_currency_mode(" ".join(lines), currency) == "domestic_unit_default":
            currency_positions = list(unit_positions)
        else:
            currency_positions = []
    else:
        currency_positions = [index for index, line in enumerate(lines) if "币种" in line and currency_key in line]

    current_tokens = [f"{current_year}年度", f"{current_year}年", "本期发生额", "本期金额", "本期数", "本年金额"] if current_year is not None else []
    previous_tokens = [f"{previous_year}年度", f"{previous_year}年", "上期发生额", "上期金额", "上期数", "上年金额"] if previous_year is not None else []
    current_positions = [index for index, line in enumerate(lines) if any(token in line for token in current_tokens)]
    previous_positions = [index for index, line in enumerate(lines) if any(token in line for token in previous_tokens)]
    positions = [
        max(unit_positions) if unit_positions else None,
        max(currency_positions) if currency_positions else None,
        max(current_positions) if current_positions else None,
    ]
    if require_previous:
        positions.append(max(previous_positions) if previous_positions else None)
    if require_scope:
        if current_scope in {"parent_attributed", "minority_attributed"}:
            scope_positions = [index for index, line in enumerate(lines) if "非经常性损益项目" in line]
        else:
            scope_positions = [
                index for index, line in enumerate(lines)
                if _header_has_scope(line, current_scope, previous_scope)
            ]
        positions.append(max(scope_positions) if scope_positions else None)
    if any(position is None for position in positions):
        return ""
    present = [int(position) for position in positions if position is not None]
    start = max(0, min(present) - 8)
    end = min(len(lines), max(present) + 4)
    return " ".join(lines[start:end])


def _is_note_reference_line(value: str) -> bool:
    return bool(re.fullmatch(r"(?:附注)?[一二三四五六七八九十百\d]+(?:\([一二三四五六七八九十百\d]+\))?", value))


def _is_amount_only_line(value: str) -> bool:
    return bool(re.fullmatch(r"[+\-−－﹣–—]?\(?\d[\d,.]*\)?", value))


def _currency_family(value: Any) -> str:
    key = normalize_quote(value)
    if key in {"人民币", "人民币元", "cny", "rmb", "元"}:
        return "cny"
    if key in {"美元", "usd", "美元元"}:
        return "usd"
    return key


def _period_year(value: Any) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(match.group(0)) if match else None


def _amount_position(text: str, value: Any) -> int:
    raw = str(value or "").strip().replace(",", "")
    negative = raw.startswith("-") or raw.startswith(("−", "－", "﹣"))
    if raw.startswith(("(", "（")) and raw.endswith((")", "）")):
        negative = True
        raw = raw[1:-1]
    raw = raw.lstrip("+-−－﹣")
    try:
        expected = Decimal(raw) * (Decimal(-1) if negative else Decimal(1))
    except Exception:
        return -1
    numeric = re.compile(r"(?<![\d,.])(?:\(\d[\d,]*(?:\.\d+)?\)|[-+]?\d[\d,]*(?:\.\d+)?)(?![\d,.])")
    for match in numeric.finditer(text):
        token = match.group(0)
        token_negative = token.startswith("-") or token.startswith(("−", "－", "﹣"))
        if token.startswith("(") and token.endswith(")"):
            token_negative = True
            token = token[1:-1]
        token = token.lstrip("+-−－﹣").replace(",", "")
        try:
            actual = Decimal(token) * (Decimal(-1) if token_negative else Decimal(1))
        except Exception:
            continue
        if actual == expected:
            return match.start()
    return -1


def _header_has_units(header: str, units: list[Any]) -> bool:
    if not units:
        return False
    for unit in units:
        expected = normalize_quote(unit).replace("人民币", "")
        if not expected:
            return False
        candidates = (
            f"单位:{expected}",
            f"单位:人民币{expected}",
            f"单位人民币{expected}",
            f"单位({expected})",
        )
        if any(candidate in header for candidate in candidates):
            continue
        return False
    return True


def _header_has_periods(header: str, current_year: int, previous_year: int) -> bool:
    current_tokens = (f"{current_year}年度", f"{current_year}年")
    previous_tokens = (f"{previous_year}年度", f"{previous_year}年")
    current_positions = [header.rfind(token) for token in current_tokens if header.rfind(token) >= 0]
    previous_positions = [header.rfind(token) for token in previous_tokens if header.rfind(token) >= 0]
    if any(current < previous for current in current_positions for previous in previous_positions):
        return True
    current_labels = ("本期发生额", "本期金额", "本期数", "本年金额")
    previous_labels = ("上期发生额", "上期金额", "上期数", "上年金额")
    current_label_positions = [header.find(token) for token in current_labels if header.find(token) >= 0]
    previous_label_positions = [header.find(token) for token in previous_labels if header.find(token) >= 0]
    return bool(
        str(current_year) in header
        and current_label_positions
        and previous_label_positions
        and min(current_label_positions) < min(previous_label_positions)
    )


def _header_has_current_period(header: str, year: int) -> bool:
    return any(token in header for token in (f"{year}年度", f"{year}年", "本期发生额", "本期金额", "本期数", "本年金额"))


def _header_has_previous_period(header: str, year: int) -> bool:
    return any(token in header for token in (f"{year}年度", f"{year}年", "上期发生额", "上期金额", "上期数", "上年金额"))


def _header_has_scope(header: str, current_scope: str, previous_scope: str) -> bool:
    if not current_scope:
        return False
    if current_scope != previous_scope:
        return False
    if current_scope == "consolidated":
        consolidated_tokens = ("合并利润表", "合并报表", "合并财务报表", "合并资产负债表", "合并财务报表项目注释", "主要会计数据", "主要财务数据")
        parent_tokens = ("母公司利润表", "母公司报表", "母公司财务报表", "母公司资产负债表", "母公司财务报表项目注释")
        positions = [(header.rfind(token), "consolidated") for token in consolidated_tokens if header.rfind(token) >= 0]
        positions.extend((header.rfind(token), "parent") for token in parent_tokens if header.rfind(token) >= 0)
        return bool(positions) and max(positions)[1] == "consolidated"
    if current_scope == "parent":
        consolidated_tokens = ("合并利润表", "合并报表", "合并财务报表", "合并资产负债表", "合并财务报表项目注释")
        parent_tokens = ("母公司利润表", "母公司报表", "母公司财务报表", "母公司资产负债表", "母公司财务报表项目注释")
        positions = [(header.rfind(token), "consolidated") for token in consolidated_tokens if header.rfind(token) >= 0]
        positions.extend((header.rfind(token), "parent") for token in parent_tokens if header.rfind(token) >= 0)
        return bool(positions) and max(positions)[1] == "parent"
    if current_scope == "parent_attributed":
        # The standard non-recurring schedule is its own attribution context.
        # Keep this marker table-local; a shareholder phrase elsewhere on the
        # same PDF page must not validate a row in another table.
        return "非经常性损益项目" in header or any(token in header for token in ("归属于上市公司股东", "归属于母公司股东", "归母口径"))
    if current_scope == "minority_attributed":
        return "非经常性损益项目" in header and "少数股东" in header
    return False


def _explicit_statement_scope(header: str) -> str:
    consolidated_tokens = ("合并利润表", "合并报表", "合并财务报表", "合并资产负债表", "合并财务报表项目注释")
    parent_tokens = ("母公司利润表", "母公司报表", "母公司财务报表", "母公司资产负债表", "母公司财务报表项目注释")
    positions = [(header.rfind(token), "consolidated") for token in consolidated_tokens if header.rfind(token) >= 0]
    positions.extend((header.rfind(token), "parent") for token in parent_tokens if header.rfind(token) >= 0)
    return max(positions)[1] if positions else ""


def _header_has_currency(header: str, currency: str) -> bool:
    return bool(_header_currency_mode(header, currency))


def _header_currency_mode(header: str, currency: str) -> str:
    key = normalize_quote(currency)
    if key in {"", "未确认", "未知", "不确定"}:
        return ""
    if key in {"人民币", "cny", "rmb"}:
        if "币种" in header:
            return "explicit_header" if any(token in header for token in ("币种:人民币", "币种:rmb", "币种:cny")) else ""
        domestic_units = (
            "单位:元", "单位:千元", "单位:万元", "单位:亿元",
            "单位(元)", "单位(千元)", "单位(万元)", "单位(亿元)",
        )
        foreign_currency_tokens = ("美元", "港元", "港币", "欧元", "日元", "英镑", "usd", "hkd", "eur", "jpy", "gbp")
        if any(token in header for token in domestic_units) and not any(token in header for token in foreign_currency_tokens):
            return "domestic_unit_default"
        return ""
    if key in {"美元", "usd"}:
        return "explicit_header" if "币种:美元" in header or "币种:usd" in header else ""
    return "explicit_header" if key in header else ""
