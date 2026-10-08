"""Evidence-first profitability analysis for the second financial-report module."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from backend.core.legacy_runtime import (
    MODEL,
    MAX_PAGE_CHARS,
    TOOLS,
    _client,
    _json_object,
    _run_tool_loop,
    _safe_model_error,
)
from backend.core.legacy_support import (
    _business_adjacent_annual_periods,
    _business_currency_family,
    _business_evidence,
    _business_money_in_yuan,
    _business_period_kind,
    _business_period_year,
    _business_scope_family,
)
from backend.core.model_io import recorded_completion
from backend.deepseek_client import ModelCallError


MAX_PROFIT_EXTRA_PAGES = 8
MAX_PROFIT_EXTRA_CHARS = 14_000
MAX_PROFIT_LINES = 100
MAX_SEGMENT_LINES = 40
MAX_DISCLOSURE_LINES = 40

PROFIT_SEARCHES = {
    "profit_statement": ("合并利润表", "利润总额", "归属于母公司所有者的净利润"),
    "business_margins": ("分产品营业收入", "分产品营业成本", "分部报告"),
    "expenses": ("营业总成本", "销售费用", "研发费用", "财务费用"),
    "other_gains_and_losses": ("投资收益", "信用减值损失", "资产减值损失", "其他收益"),
    "nonrecurring": ("非经常性损益项目", "扣除非经常性损益后的净利润"),
    "management_explanations": ("经营情况讨论与分析", "营业收入构成", "变动原因"),
}

# Multipliers express how a signed reported line contributes to profit.
# Subtotals and model-provided classifications never control these rules.
PROFIT_EFFECT = {
    "total_operating_revenue": Decimal(1),
    "operating_revenue": Decimal(1),
    "interest_income": Decimal(1),
    "other_operating_revenue": Decimal(1),
    "total_operating_cost": Decimal(-1),
    "operating_cost": Decimal(-1),
    "other_operating_expense": Decimal(-1),
    "tax_and_surcharges": Decimal(-1),
    "selling_expense": Decimal(-1),
    "administrative_expense": Decimal(-1),
    "research_development_expense": Decimal(-1),
    "financial_expense": Decimal(-1),
    "other_income": Decimal(1),
    "investment_income": Decimal(1),
    "fair_value_change": Decimal(1),
    "credit_impairment_loss": Decimal(1),
    "asset_impairment_loss": Decimal(1),
    "asset_disposal_income": Decimal(1),
    "non_operating_income": Decimal(1),
    "non_operating_expense": Decimal(-1),
    "income_tax_expense": Decimal(-1),
    "minority_profit": Decimal(-1),
}

PROFIT_SUBTOTALS = {
    "operating_profit",
    "profit_before_tax",
    "net_profit",
    "parent_profit",
    "deducted_parent_profit",
}

NONRECURRING_KEYS = {
    "nonrecurring_item",
    "nonrecurring_tax_effect",
    "nonrecurring_minority_effect",
    "nonrecurring_parent_net",
}

COVERAGE_RULES = (
    ("profit_statement", "利润总额", {"operating_profit", "profit_before_tax", "net_profit", "parent_profit"}),
    ("gross_profit", "营业毛利", {"operating_revenue", "operating_cost"}),
    (
        "expenses",
        "费用变化",
        {"total_operating_cost", "operating_cost", "selling_expense", "administrative_expense", "research_development_expense", "financial_expense"},
    ),
    (
        "other_gains_and_losses",
        "其他收益与损失",
        {"other_income", "investment_income", "fair_value_change", "credit_impairment_loss", "asset_impairment_loss", "asset_disposal_income"},
    ),
    ("nonrecurring", "归母与扣非", {"parent_profit", "deducted_parent_profit", *NONRECURRING_KEYS}),
    ("explanations", "重要变化解释", set()),
)


def _profit_prompt(stage: str | None = None) -> str:
    prompt_dir = __import__("pathlib").Path(__file__).resolve().parent / "prompts"
    content = (prompt_dir / "模块二_盈利来源与变化_v1.md").read_text(encoding="utf-8")
    stage_names = {
        "extract": "模块二_事实提取_v1.md",
        "interpret": "模块二_分析与交接_v1.md",
        "question": "模块二_追问_v1.md",
    }
    if stage in stage_names:
        content += "\n\n" + (prompt_dir / stage_names[stage]).read_text(encoding="utf-8")
    return content


def _parse_amount(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").replace("，", "")
    text = text.replace("人民币", "").replace("RMB", "").replace("CNY", "").strip()
    if text in {"", "-", "—", "–", "－", "--", "不适用", "未披露", "未找到"}:
        return None
    negative_parentheses = text.startswith("(") and text.endswith(")") or text.startswith("（") and text.endswith("）")
    if negative_parentheses:
        text = text[1:-1].strip()
    text = re.sub(r"(?:元|千元|万元|亿元)$", "", text).strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return -result if negative_parentheses else result


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _percent(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _profit_scope_family(value: Any) -> str:
    family = _business_scope_family(value)
    return family if family in {"consolidated", "parent"} else ""


def _quote_contains_amount(quote: str, amount: Decimal | None) -> bool:
    if amount is None:
        return True
    tokens = re.findall(r"(?<![A-Za-z])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", quote)
    values = {_parse_amount(token) for token in tokens}
    return amount in values


def _preflight_profit_search(
    *,
    search_pages: Callable[[str], list[dict[str, Any]]],
    get_page: Callable[[int], str | None],
    initial_page_numbers: set[int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    sections: dict[str, dict[str, Any]] = {}
    trace: list[dict[str, Any]] = []
    for key, queries in PROFIT_SEARCHES.items():
        by_page: dict[int, dict[str, Any]] = {}
        for query in queries:
            for item in search_pages(query)[:4]:
                try:
                    page_number = int(item["page"])
                except (KeyError, TypeError, ValueError):
                    continue
                if page_number >= 1:
                    prior = by_page.get(page_number)
                    if prior is None or int(item.get("score", 0) or 0) > int(prior.get("score", 0) or 0):
                        by_page[page_number] = item
        ordered = sorted(by_page.values(), key=lambda row: (-int(row.get("score", 0) or 0), int(row["page"])))
        sections[key] = {
            "queries": list(queries),
            "candidate_pages": [int(row["page"]) for row in ordered[:4]],
            "search_hit_count": len(by_page),
        }
        trace.append(
            {
                "tool": "module2_preflight_search",
                "arguments": {"section": key, "queries": list(queries)},
                "pages": [int(row["page"]) for row in ordered[:4]],
                "result": {"candidate_page_count": len(by_page)},
            }
        )

    selected: list[int] = []
    max_rank = max((len(section["candidate_pages"]) for section in sections.values()), default=0)
    for rank in range(max_rank):
        for section in sections.values():
            candidates = section["candidate_pages"]
            if rank < len(candidates):
                number = candidates[rank]
                if number not in initial_page_numbers and number not in selected:
                    selected.append(number)
                    if len(selected) >= MAX_PROFIT_EXTRA_PAGES:
                        break
        if len(selected) >= MAX_PROFIT_EXTRA_PAGES:
            break

    pages: list[dict[str, Any]] = []
    remaining = MAX_PROFIT_EXTRA_CHARS
    for number in selected:
        text = get_page(number)
        if not text or remaining <= 0:
            continue
        excerpt = str(text)[: min(2400, remaining)].strip()
        if excerpt:
            pages.append({"page": number, "text": excerpt})
            remaining -= len(excerpt)
    for section in sections.values():
        section["provided_pages"] = [
            number
            for number in section["candidate_pages"]
            if number in initial_page_numbers or number in selected
        ]
    return (
        {"sections": sections, "note": "候选页仅用于定位；检索未命中不代表年报未披露。"},
        pages,
        trace,
    )


def _normalize_lines(
    raw_lines: Any,
    *,
    key_field: str,
    allowed_pages: set[int],
    page_text: Callable[[int], str | None],
    limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw_lines, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_lines[:limit]:
        if not isinstance(item, dict):
            continue
        role = str(item.get(key_field, "")).strip().lower()
        current_value = _parse_amount(item.get("current_value"))
        previous_value = _parse_amount(item.get("previous_value"))
        current_unit = str(item.get("current_unit", item.get("unit", "")) or "").strip()[:24]
        previous_unit = str(item.get("previous_unit", item.get("unit", "")) or "").strip()[:24]
        current_period = str(item.get("current_period", "") or "").strip()[:64]
        previous_period = str(item.get("previous_period", "") or "").strip()[:64]
        current_scope = str(item.get("current_scope", item.get("reporting_scope", "")) or "").strip()[:40]
        previous_scope = str(item.get("previous_scope", item.get("reporting_scope", "")) or "").strip()[:40]
        current_currency = str(item.get("current_currency", item.get("currency", "未确认")) or "未确认").strip()[:24]
        previous_currency = str(item.get("previous_currency", item.get("currency", "未确认")) or "未确认").strip()[:24]
        evidence_item = {
            "source_pages": item.get("source_pages", []),
            "evidence_quote": item.get("evidence_quote", ""),
        }
        refs, quote, quote_verified, invalid_page = _business_evidence(evidence_item, allowed_pages, page_text)
        numeric_evidence_verified = (
            quote_verified
            and _quote_contains_amount(quote, current_value)
            and _quote_contains_amount(quote, previous_value)
        )
        current_yuan = _business_money_in_yuan(current_value, current_unit, current_currency)
        previous_yuan = _business_money_in_yuan(previous_value, previous_unit, previous_currency)
        scope_match = bool(_profit_scope_family(current_scope)) and (
            _profit_scope_family(current_scope) == _profit_scope_family(previous_scope)
        )
        currency_match = _business_currency_family(current_currency) == _business_currency_family(previous_currency)
        period_match = _business_adjacent_annual_periods(current_period, previous_period)
        comparable = (
            current_yuan is not None
            and previous_yuan is not None
            and scope_match
            and currency_match
            and period_match
        )
        diff = current_yuan - previous_yuan if comparable else None
        change_percent = diff / previous_yuan * Decimal(100) if diff is not None and previous_yuan > 0 else None
        value_status = "可计算" if comparable and numeric_evidence_verified else (
            "待核对出处或数值" if not numeric_evidence_verified else "口径或期间不可比"
        )
        rows.append(
            {
                key_field: role if role in (set(PROFIT_EFFECT) | PROFIT_SUBTOTALS | NONRECURRING_KEYS | {"segment_revenue", "segment_cost", "segment_gross_margin", "reported_nonrecurring_line"}) else "unmapped",
                "label": str(item.get("label", "未命名项目") or "未命名项目").strip()[:120],
                "current_value": str(item.get("current_value", "") or "").strip()[:60],
                "current_unit": current_unit,
                "previous_value": str(item.get("previous_value", "") or "").strip()[:60],
                "previous_unit": previous_unit,
                "current_period": current_period,
                "previous_period": previous_period,
                "reporting_scope": current_scope,
                "previous_reporting_scope": previous_scope,
                "currency": current_currency,
                "previous_currency": previous_currency,
                "source_pages": refs,
                "evidence_quote": quote,
                "quote_verified": quote_verified,
                "numeric_evidence_verified": numeric_evidence_verified,
                "invalid_page_reference": invalid_page,
                "current_yuan": _decimal_text(current_yuan),
                "previous_yuan": _decimal_text(previous_yuan),
                "change_yuan": _decimal_text(diff),
                "change_percent": _percent(change_percent) if change_percent is not None else None,
                "value_status": value_status,
                "comparability_note": str(item.get("comparability_note", "") or "").strip()[:240],
            }
        )
    return rows


def _unique_line(rows: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    matches = [row for row in rows if row.get("profit_role") == role and row.get("value_status") == "可计算"]
    if not matches:
        return None
    signatures = {
        (
            row["current_yuan"], row["previous_yuan"], row["reporting_scope"],
            row.get("previous_reporting_scope"), row.get("currency"), row.get("previous_currency"),
            row.get("current_period"), row.get("previous_period"),
        )
        for row in matches
    }
    return matches[0] if len(signatures) == 1 else None


def _same_period_year(left: Any, right: Any) -> bool:
    return (
        _business_period_kind(left) == "annual"
        and _business_period_kind(right) == "annual"
        and _business_period_year(left) is not None
        and _business_period_year(left) == _business_period_year(right)
    )


def _rows_comparable(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Require the same annual periods, reporting entity and currency on both rows."""
    return bool(
        left.get("value_status") == "可计算"
        and right.get("value_status") == "可计算"
        and _same_period_year(left.get("current_period"), right.get("current_period"))
        and _same_period_year(left.get("previous_period"), right.get("previous_period"))
        and bool(_profit_scope_family(left.get("reporting_scope")))
        and _profit_scope_family(left.get("reporting_scope"))
        == _profit_scope_family(right.get("reporting_scope"))
        and bool(_profit_scope_family(left.get("previous_reporting_scope")))
        and _profit_scope_family(left.get("previous_reporting_scope"))
        == _profit_scope_family(right.get("previous_reporting_scope"))
        and _business_currency_family(left.get("currency"))
        == _business_currency_family(right.get("currency"))
        and _business_currency_family(left.get("previous_currency"))
        == _business_currency_family(right.get("previous_currency"))
    )


def _metric_change(
    *, name: str, numerator: Decimal | None, denominator: Decimal | None, period: str, prior_period: str,
    current_evidence: dict[str, Any], previous_evidence: dict[str, Any],
) -> dict[str, Any]:
    value = numerator / denominator * Decimal(100) if numerator is not None and denominator is not None and denominator > 0 else None
    return {
        "name": name,
        "current_value": _percent(value) if value is not None else None,
        "previous_value": None,
        "change_percentage_points": None,
        "unit": "%",
        "period": period,
        "previous_period": prior_period,
        "source_rows": [current_evidence, previous_evidence],
        "status": "程序复算" if value is not None else "未计算：分母缺失、非正或口径不匹配",
    }


def _calculate_profit_metrics(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    selected_roles = {
        role: _unique_line(rows, role)
        for role in {
            "operating_revenue", "operating_cost", "operating_profit", "net_profit", "parent_profit",
            "deducted_parent_profit", "total_operating_revenue", "total_operating_cost",
        }
    }
    revenue = selected_roles.get("operating_revenue")
    cost = selected_roles.get("operating_cost")
    if revenue and cost and _rows_comparable(revenue, cost):
        current_r, previous_r = Decimal(revenue["current_yuan"]), Decimal(revenue["previous_yuan"])
        current_c, previous_c = Decimal(cost["current_yuan"]), Decimal(cost["previous_yuan"])
        if current_r > 0 and previous_r > 0:
            current_gp, previous_gp = current_r - current_c, previous_r - previous_c
            current_margin, previous_margin = current_gp / current_r, previous_gp / previous_r
            metrics.append(
                {
                    "name": "营业毛利率",
                    "current_value": _percent(current_margin * Decimal(100)),
                    "previous_value": _percent(previous_margin * Decimal(100)),
                    "change_percentage_points": _percent((current_margin - previous_margin) * Decimal(100)),
                    "unit": "%",
                    "current_gross_profit_yuan": _decimal_text(current_gp),
                    "previous_gross_profit_yuan": _decimal_text(previous_gp),
                    "source_rows": [revenue, cost],
                    "status": "程序复算",
                }
            )
    if revenue:
        revenue_values = (Decimal(revenue["current_yuan"]), Decimal(revenue["previous_yuan"]))
        for role, name in (
            ("selling_expense", "销售费用率"),
            ("administrative_expense", "管理费用率"),
            ("research_development_expense", "研发费用率"),
            ("financial_expense", "财务费用率"),
        ):
            expense = selected_roles.get(role) or _unique_line(rows, role)
            if not expense or not _rows_comparable(expense, revenue):
                continue
            amounts = (Decimal(expense["current_yuan"]), Decimal(expense["previous_yuan"]))
            if min(revenue_values) <= 0:
                continue
            current_ratio, previous_ratio = amounts[0] / revenue_values[0], amounts[1] / revenue_values[1]
            metrics.append(
                {
                    "name": name,
                    "current_value": _percent(current_ratio * Decimal(100)),
                    "previous_value": _percent(previous_ratio * Decimal(100)),
                    "change_percentage_points": _percent((current_ratio - previous_ratio) * Decimal(100)),
                    "unit": "%",
                    "source_rows": [expense, revenue],
                    "status": "程序复算；财务费用可为负数",
                }
            )
    bridges = _build_profit_bridge(rows)
    return metrics, bridges


def _build_profit_bridge(rows: list[dict[str, Any]]) -> dict[str, Any]:
    parent = _unique_line(rows, "parent_profit") or _unique_line(rows, "net_profit")
    if not parent:
        return {"status": "未计算：归母净利润及合并净利润均缺失、不可比或引用待核", "contributions": [], "detail_rows": [], "residual_yuan": None}
    target_change = Decimal(parent["current_yuan"]) - Decimal(parent["previous_yuan"])
    raw_candidates = [
        row for row in rows
        if row.get("profit_role") in PROFIT_EFFECT
        and row.get("value_status") == "可计算"
        and row.get("numeric_evidence_verified")
        and _rows_comparable(row, parent)
    ]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for row in raw_candidates:
        by_role.setdefault(row["profit_role"], []).append(row)

    candidates: dict[str, dict[str, Any]] = {}
    ambiguous_roles: set[str] = set()
    for role, role_rows in by_role.items():
        signatures: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in role_rows:
            signature = (
                row["current_yuan"], row["previous_yuan"], row["reporting_scope"],
                row.get("previous_reporting_scope"), row.get("currency"), row.get("previous_currency"),
                row.get("current_period"), row.get("previous_period"),
            )
            signatures.setdefault(signature, row)
        if len(signatures) == 1:
            candidates[role] = next(iter(signatures.values()))
        else:
            ambiguous_roles.add(role)

    # Prefer detailed revenue/cost rows only when they add exactly back to the
    # disclosed subtotal in both periods. Otherwise use the subtotal in the
    # bridge and retain its children as visible, non-additive detail.
    groups = {
        "total_operating_revenue": {"operating_revenue", "interest_income", "other_operating_revenue"},
        "total_operating_cost": {
            "operating_cost", "other_operating_expense", "tax_and_surcharges", "selling_expense",
            "administrative_expense", "research_development_expense", "financial_expense",
        },
    }


    included: set[str] = set(candidates) - ambiguous_roles
    inclusion_notes: dict[str, str] = {}
    for subtotal_role, child_roles in groups.items():
        subtotal = candidates.get(subtotal_role)
        available_children = child_roles & included
        if not subtotal or not available_children:
            continue
        current_sum = sum((Decimal(candidates[role]["current_yuan"]) for role in available_children), Decimal(0))
        previous_sum = sum((Decimal(candidates[role]["previous_yuan"]) for role in available_children), Decimal(0))
        if current_sum == Decimal(subtotal["current_yuan"]) and previous_sum == Decimal(subtotal["previous_yuan"]):
            included.remove(subtotal_role)
            inclusion_notes[subtotal_role] = "未计入合计：子项在本期和上期均与合计一致。"
            for role in available_children:
                inclusion_notes[role] = "按可核对子项计入。"
        else:
            for role in available_children:
                included.discard(role)
                inclusion_notes[role] = "未计入合计：披露子项未能在两期同时复现小计，避免重复或不完整求和。"
            inclusion_notes[subtotal_role] = "按年报披露小计计入；子项保留作明细。"

    if parent.get("profit_role") == "net_profit":
        included.discard("minority_profit")
        inclusion_notes["minority_profit"] = "未计入合并净利润桥：少数股东损益用于从合并净利润核到归母净利润。"

    detail_rows: list[dict[str, Any]] = []
    contribution_rows: list[dict[str, Any]] = []
    ordered_roles = [role for role in PROFIT_EFFECT if role in candidates]
    for role in ordered_roles:
        row = candidates[role]
        change = Decimal(row["change_yuan"])
        effect = change * PROFIT_EFFECT[role]
        detail = {
            "label": row["label"],
            "role": role,
            "change_yuan": _decimal_text(change),
            "profit_effect_yuan": _decimal_text(effect),
            "included_in_total": role in included,
            "source_pages": row["source_pages"],
            "evidence_quote": row["evidence_quote"],
            "quote_verified": row["quote_verified"],
            "note": inclusion_notes.get(role, "按年报金额变化及项目在利润表中的加减方向复算；属于会计贡献，不等同经营原因。"),
        }
        detail_rows.append(detail)
        if role in included:
            contribution_rows.append(detail)
    identified = sum((Decimal(row["profit_effect_yuan"]) for row in contribution_rows), Decimal(0))
    residual = target_change - identified
    return {
        "target_label": "归属于母公司股东的净利润" if parent.get("profit_role") == "parent_profit" else "合并净利润",
        "target_change_yuan": _decimal_text(target_change),
        "identified_contribution_yuan": _decimal_text(identified),
        "residual_yuan": _decimal_text(residual),
        "contributions": contribution_rows,
        "detail_rows": detail_rows,
        "excluded_ambiguous_roles": sorted(ambiguous_roles),
        "status": "已计算，仍需解释未分类差额" if residual else "算术差额为零；业务原因仍须有年报证据",
        "formula": (
            "归母净利润变化 − 已识别利润项目贡献 = 未分类差额"
            if parent.get("profit_role") == "parent_profit"
            else "合并净利润变化 − 已识别利润项目贡献 = 未分类差额"
        ),
    }


def _reconcile_operating_profit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    target = _unique_line(rows, "operating_profit")
    if not target:
        return {"name": "营业利润", "formula": "已识别营业收入项目 − 已识别成本项目 + 其他已列示经营损益", "status": "未复算：缺少可核验的本期/上期营业利润"}
    candidates: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    excluded_roles = {"non_operating_income", "non_operating_expense", "income_tax_expense", "minority_profit"}
    for role in PROFIT_EFFECT:
        if role in excluded_roles:
            continue
        matching = [
            row for row in rows
            if row.get("profit_role") == role and row.get("value_status") == "可计算"
            and row.get("numeric_evidence_verified") and _rows_comparable(row, target)
        ]
        signatures: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in matching:
            signature = (
                row["current_yuan"], row["previous_yuan"], row["reporting_scope"],
                row.get("previous_reporting_scope"), row.get("currency"), row.get("previous_currency"),
                row.get("current_period"), row.get("previous_period"),
            )
            signatures.setdefault(signature, row)
        if len(signatures) == 1:
            candidates[role] = next(iter(signatures.values()))
        elif len(signatures) > 1:
            ambiguous.add(role)

    included = set(candidates)
    groups = {
        "total_operating_revenue": {"operating_revenue", "interest_income", "other_operating_revenue"},
        "total_operating_cost": {
            "operating_cost", "other_operating_expense", "tax_and_surcharges", "selling_expense",
            "administrative_expense", "research_development_expense", "financial_expense",
        },
    }
    for subtotal_role, child_roles in groups.items():
        subtotal = candidates.get(subtotal_role)
        children = child_roles & included
        if not subtotal or not children:
            continue
        sums_match = all(
            sum((Decimal(candidates[role][value_key]) for role in children), Decimal(0))
            == Decimal(subtotal[value_key])
            for value_key in ("current_yuan", "previous_yuan")
        )
        if sums_match:
            included.remove(subtotal_role)
        else:
            included.difference_update(children)

    used_rows = [candidates[role] for role in PROFIT_EFFECT if role in included]
    formula = "已识别营业收入项目 − 已识别成本项目 + 其他已列示经营损益"
    if not used_rows:
        return {"name": "营业利润", "formula": formula, "status": "未复算：没有通过引用、范围和期间核验的组成项目", "source_rows": [target]}
    result: dict[str, Any] = {"name": "营业利润", "formula": formula, "source_rows": [target, *used_rows]}
    statuses: list[str] = []
    for period in ("current", "previous"):
        key = "current_yuan" if period == "current" else "previous_yuan"
        reported = Decimal(target[key])
        calculated = sum(
            (Decimal(row[key]) * PROFIT_EFFECT[row["profit_role"]] for row in used_rows), Decimal(0)
        )
        difference = reported - calculated
        tolerance = _display_precision_tolerance(target, period) + sum(
            (_display_precision_tolerance(row, period) for row in used_rows), Decimal(0)
        )
        status = "在披露精度范围内" if abs(difference) <= tolerance else "有未分类差额"
        statuses.append(status)
        result[f"{period}_reported_yuan"] = _decimal_text(reported)
        result[f"{period}_calculated_yuan"] = _decimal_text(calculated)
        result[f"{period}_difference_yuan"] = _decimal_text(difference)
        result[f"{period}_tolerance_yuan"] = _decimal_text(tolerance)
        result[f"{period}_status"] = status
    result["status"] = "；".join(f"{period}{status}" for period, status in zip(("本期", "上期"), statuses))
    result["status"] += "；只复算已识别项目，差额仍需核查未提取或未匹配科目"
    result["excluded_ambiguous_roles"] = sorted(ambiguous)
    return result


def _display_precision_tolerance(row: dict[str, Any], period: str) -> Decimal:
    raw_value = row.get("current_value" if period == "current" else "previous_value")
    unit = row.get("current_unit" if period == "current" else "previous_unit")
    currency = row.get("currency" if period == "current" else "previous_currency")
    amount = _parse_amount(raw_value)
    unit_yuan = _business_money_in_yuan(Decimal(1), unit, currency)
    if amount is None or unit_yuan is None:
        return Decimal(0)
    return abs(unit_yuan * (Decimal(10) ** amount.as_tuple().exponent) / Decimal(2))


def _calculate_profit_reconciliations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconcile disclosed bottom-line layers without assuming missing rows are zero."""
    formulas = (
        ("profit_before_tax", "利润总额", ("operating_profit", "non_operating_income", "non_operating_expense"), (Decimal(1), Decimal(1), Decimal(-1)), "营业利润 + 营业外收入 − 营业外支出"),
        ("net_profit", "合并净利润", ("profit_before_tax", "income_tax_expense"), (Decimal(1), Decimal(-1)), "利润总额 − 所得税费用"),
        ("parent_profit", "归母净利润", ("net_profit", "minority_profit"), (Decimal(1), Decimal(-1)), "合并净利润 − 少数股东损益"),
    )
    checks: list[dict[str, Any]] = []
    for target_role, label, operand_roles, multipliers, formula in formulas:
        target = _unique_line(rows, target_role)
        operands = [_unique_line(rows, role) for role in operand_roles]
        if not target:
            checks.append({"name": label, "formula": formula, "status": "未复算：缺少可核验的本期/上期目标行"})
            continue
        if any(row is None for row in operands):
            missing = [role for role, row in zip(operand_roles, operands) if row is None]
            checks.append({
                "name": label, "formula": formula,
                "status": "未复算：缺少可核验项目 " + "、".join(missing), "source_rows": [target],
            })
            continue
        complete_operands = [row for row in operands if row is not None]
        if any(not _rows_comparable(target, row) for row in complete_operands):
            checks.append({"name": label, "formula": formula, "status": "未复算：期间、范围或币种不一致", "source_rows": [target, *complete_operands]})
            continue
        values: dict[str, Any] = {"name": label, "formula": formula, "source_rows": [target, *complete_operands]}
        period_statuses: list[str] = []
        for period in ("current", "previous"):
            value_key = "current_yuan" if period == "current" else "previous_yuan"
            reported = Decimal(target[value_key])
            calculated = sum((Decimal(row[value_key]) * multiplier for row, multiplier in zip(complete_operands, multipliers)), Decimal(0))
            difference = reported - calculated
            tolerance = _display_precision_tolerance(target, period) + sum(
                (_display_precision_tolerance(row, period) for row in complete_operands), Decimal(0)
            )
            status = "在披露精度范围内" if abs(difference) <= tolerance else "存在差额"
            period_statuses.append(status)
            values[f"{period}_reported_yuan"] = _decimal_text(reported)
            values[f"{period}_calculated_yuan"] = _decimal_text(calculated)
            values[f"{period}_difference_yuan"] = _decimal_text(difference)
            values[f"{period}_tolerance_yuan"] = _decimal_text(tolerance)
            values[f"{period}_status"] = status
        values["status"] = "；".join(f"{period}{status}" for period, status in zip(("本期", "上期"), period_statuses))
        checks.append(values)
    return [_reconcile_operating_profit(rows), *checks]


def _reconcile_deducted_profit(rows: list[dict[str, Any]], special_items: list[dict[str, Any]]) -> dict[str, Any]:
    parent = _unique_line(rows, "parent_profit")
    deducted = _unique_line(rows, "deducted_parent_profit")
    special_net = _unique_line(special_items, "nonrecurring_parent_net")
    formula = "归母净利润 − 扣非归母净利润 = 年报披露的归母口径非经常性损益净额"
    if not parent or not deducted or not special_net:
        missing = []
        if not parent:
            missing.append("归母净利润")
        if not deducted:
            missing.append("扣非归母净利润")
        if not special_net:
            missing.append("归母口径非经常性损益净额")
        return {"name": "归母与扣非核对", "formula": formula, "status": "未复算：缺少可核验项目 " + "、".join(missing)}
    if not _rows_comparable(parent, deducted):
        return {"name": "归母与扣非核对", "formula": formula, "status": "未复算：归母与扣非的期间、范围或币种不一致", "source_rows": [parent, deducted, special_net]}
    same_periods = all(
        _same_period_year(left.get(period_key), right.get(period_key))
        for left in (parent, deducted)
        for right in (special_net,)
        for period_key in ("current_period", "previous_period")
    )
    same_currency = (
        _business_currency_family(parent.get("currency")) == _business_currency_family(special_net.get("currency"))
        and _business_currency_family(parent.get("previous_currency")) == _business_currency_family(special_net.get("previous_currency"))
    )
    if not same_periods or not same_currency:
        return {"name": "归母与扣非核对", "formula": formula, "status": "未复算：非经常性损益披露期间或币种不一致", "source_rows": [parent, deducted, special_net]}
    result: dict[str, Any] = {"name": "归母与扣非核对", "formula": formula, "source_rows": [parent, deducted, special_net]}
    statuses: list[str] = []
    for period in ("current", "previous"):
        key = "current_yuan" if period == "current" else "previous_yuan"
        calculated = Decimal(parent[key]) - Decimal(deducted[key])
        reported = Decimal(special_net[key])
        difference = calculated - reported
        tolerance = sum((_display_precision_tolerance(row, period) for row in (parent, deducted, special_net)), Decimal(0))
        status = "在披露精度范围内" if abs(difference) <= tolerance else "存在差额"
        statuses.append(status)
        result[f"{period}_calculated_yuan"] = _decimal_text(calculated)
        result[f"{period}_reported_yuan"] = _decimal_text(reported)
        result[f"{period}_difference_yuan"] = _decimal_text(difference)
        result[f"{period}_tolerance_yuan"] = _decimal_text(tolerance)
        result[f"{period}_status"] = status
    result["status"] = "；".join(f"{period}{status}" for period, status in zip(("本期", "上期"), statuses))
    return result


def _normalize_profit_result(
    extraction: dict[str, Any],
    interpretation: dict[str, Any] | None,
    *,
    allowed_pages: set[int],
    page_text: Callable[[int], str | None],
    read_pages: list[int],
    retrieval: dict[str, Any],
    interpretation_issue: str = "",
) -> dict[str, Any]:
    rows = _normalize_lines(
        extraction.get("profit_lines"),
        key_field="profit_role",
        allowed_pages=allowed_pages,
        page_text=page_text,
        limit=MAX_PROFIT_LINES,
    )
    segments = _normalize_lines(
        extraction.get("business_segments"),
        key_field="profit_role",
        allowed_pages=allowed_pages,
        page_text=page_text,
        limit=MAX_SEGMENT_LINES,
    )
    special_items = _normalize_lines(
        extraction.get("nonrecurring_items"),
        key_field="profit_role",
        allowed_pages=allowed_pages,
        page_text=page_text,
        limit=MAX_DISCLOSURE_LINES,
    )
    metrics, bridge = _calculate_profit_metrics(rows)
    reconciliations = [
        *_calculate_profit_reconciliations(rows),
        _reconcile_deducted_profit(rows, special_items),
    ]

    retrieval_sections = retrieval.get("sections", {})
    coverage: list[dict[str, Any]] = []
    for key, label, required_roles in COVERAGE_RULES:
        if key == "explanations":
            records = (interpretation or {}).get("findings", [])
        elif key == "gross_profit":
            records = [row for row in rows if row.get("profit_role") in required_roles]
        elif key == "nonrecurring":
            records = [row for row in rows + special_items if row.get("profit_role") in required_roles]
        else:
            records = [row for row in rows if row.get("profit_role") in required_roles]
        supported = any(row.get("quote_verified") and row.get("source_pages") for row in records)
        has_content = bool(records)
        retrieval_key = {"gross_profit": "business_margins", "explanations": "management_explanations"}.get(key, key)
        candidates = retrieval_sections.get(retrieval_key, {}).get("candidate_pages", [])
        status = "supported" if supported else "needs_review" if has_content else "candidate_only" if candidates else "search_miss"
        note = {
            "supported": "至少一项有可回查出处；不代表该部分所有项目均已完整。",
            "needs_review": "已提取内容，但引用或数值需要人工核对。",
            "candidate_only": "检索到候选页，但尚未提取到可核对内容。",
            "search_miss": "本次未命中；不能据此判断年报未披露。",
        }[status]
        coverage.append({"key": key, "name": label, "status": status, "candidate_pages": candidates, "note": note})

    normalized_findings: list[dict[str, Any]] = []
    if interpretation and isinstance(interpretation.get("findings"), list):
        for item in interpretation["findings"][:8]:
            if not isinstance(item, dict):
                continue
            refs, quote, verified, invalid = _business_evidence(item, allowed_pages, page_text)
            normalized_findings.append(
                {
                    "title": str(item.get("title", "") or "").strip()[:140],
                    "observation": str(item.get("observation", "") or "").strip()[:700],
                    "accounting_contribution": str(item.get("accounting_contribution", "") or "").strip()[:600],
                    "company_explanation": str(item.get("company_explanation", "") or "").strip()[:700],
                    "analysis": str(item.get("analysis", "") or "").strip()[:700],
                    "alternative_explanation": str(item.get("alternative_explanation", "") or "").strip()[:500],
                    "limitation": str(item.get("limitation", "") or "").strip()[:400],
                    "source_pages": refs,
                    "evidence_quote": quote,
                    "quote_verified": verified,
                    "invalid_page_reference": invalid,
                }
            )

    questions: list[dict[str, Any]] = []
    if interpretation and isinstance(interpretation.get("follow_up_checks"), list):
        for item in interpretation["follow_up_checks"][:8]:
            if not isinstance(item, dict):
                continue
            refs, quote, verified, invalid = _business_evidence(item, allowed_pages, page_text)
            questions.append(
                {
                    "question": str(item.get("question", "") or "").strip()[:360],
                    "reason": str(item.get("reason", "") or "").strip()[:500],
                    "next_module": str(item.get("next_module", "") or "").strip()[:100],
                    "source_pages": refs,
                    "evidence_quote": quote,
                    "quote_verified": verified,
                    "invalid_page_reference": invalid,
                }
            )

    uncertainties = extraction.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        uncertainties = []
    uncertainty_list = [str(item).strip()[:300] for item in uncertainties[:12] if str(item).strip()]
    if interpretation_issue:
        uncertainty_list.append(f"盈利变化解释阶段未完成，已保留事实及程序计算：{interpretation_issue}")
    if any(not row["numeric_evidence_verified"] for row in rows if row["current_value"] or row["previous_value"]):
        uncertainty_list.append("部分利润数值未能同时在引用摘录中核实；这些数值已从会计贡献计算中排除。")
    if any(row["profit_role"] == "unmapped" for row in rows):
        uncertainty_list.append("有利润表项目未匹配到受支持的计算类别；原始项目保留并列为待核。")
    if bridge.get("excluded_ambiguous_roles"):
        uncertainty_list.append(
            "有多个互相冲突的同类利润项目，程序未将其计入利润变化桥："
            + "、".join(bridge["excluded_ambiguous_roles"])
        )
    review_status = (
        "六类分析均有可回查内容，仍需核对解释"
        if all(item["status"] == "supported" for item in coverage)
        else "存在待核对或未覆盖内容"
    )
    company = str(extraction.get("company", "未能确认") or "未能确认").strip()[:160]
    period = str(extraction.get("period", "未能确认") or "未能确认").strip()[:80]
    summary = str((interpretation or {}).get("summary", "") or "").strip()[:1400]
    if not summary:
        summary = (
            f"已从年报提取 {company} {period} 的利润表事实并完成符合条件的程序复算。"
            "下方列出利润变化贡献、未分类差额和出处；经营原因仍需依据原文逐项核对。"
        )
    summary_item = {
        "source_pages": (interpretation or {}).get("summary_source_pages", []),
        "evidence_quote": (interpretation or {}).get("summary_evidence_quote", ""),
    }
    summary_refs, summary_quote, summary_verified, _ = _business_evidence(summary_item, allowed_pages, page_text)
    return {
        "module": "profit",
        "company": company or "未能确认",
        "period": period or "未能确认",
        "reporting_scope": str(extraction.get("reporting_scope", "未能确认") or "未能确认")[:60],
        "currency": str(extraction.get("currency", "未确认") or "未确认")[:32],
        "summary": summary,
        "summary_evidence": {
            "source_pages": summary_refs,
            "evidence_quote": summary_quote,
            "quote_verified": summary_verified,
        },
        "profit_lines": rows,
        "profit_metrics": metrics,
        "profit_bridge": bridge,
        "profit_checks": reconciliations,
        "business_segments": segments,
        "nonrecurring_items": special_items,
        "findings": normalized_findings,
        "follow_up_checks": questions,
        "coverage_checks": coverage,
        "analysis_review_status": review_status,
        "search_coverage": retrieval,
        "uncertainties": uncertainty_list,
        "read_pages": sorted(read_pages),
        "prompt_version": "模块二_盈利来源与变化_v1",
    }


def _compact_business_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep the Module 1 handoff small enough to survive prompt limits."""
    def rows(key: str, fields: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
        source = context.get(key)
        if not isinstance(source, list):
            return []
        compacted: list[dict[str, Any]] = []
        for item in source[:limit]:
            if not isinstance(item, dict):
                continue
            entry: dict[str, Any] = {}
            for field in fields:
                value = item.get(field)
                if value is None or value == "":
                    continue
                if field == "source_pages" and isinstance(value, list):
                    entry[field] = value[:4]
                elif field == "evidence_quote":
                    entry[field] = str(value)[:280]
                elif isinstance(value, str):
                    entry[field] = value[:320]
                else:
                    entry[field] = value
            if entry:
                compacted.append(entry)
        return compacted

    total = context.get("revenue_total")
    total_fields = (
        "metric_name", "current_value", "current_unit", "current_period", "previous_value",
        "previous_unit", "previous_period", "reported_yoy", "reporting_scope", "currency",
        "comparability_note", "source_pages", "evidence_quote",
    )
    compact_total: dict[str, Any] = {}
    if isinstance(total, dict):
        for field in total_fields:
            value = total.get(field)
            if value is None or value == "":
                continue
            if field == "source_pages" and isinstance(value, list):
                compact_total[field] = value[:4]
            elif field == "evidence_quote":
                compact_total[field] = str(value)[:280]
            elif isinstance(value, str):
                compact_total[field] = value[:220]
            else:
                compact_total[field] = value

    return {
        "summary": str(context.get("summary") or context.get("business_summary") or "")[:900],
        "reporting_scope": str(context.get("reporting_scope") or "")[:60],
        "currency": str(context.get("currency") or "")[:40],
        "business_flow": rows(
            "business_flow", ("stage", "description", "fact_type", "source_pages", "evidence_quote"), 4
        ),
        "revenue_total": compact_total,
        "revenue_segments": rows(
            "revenue_segments",
            (
                "name", "basis", "metric_name", "current_value", "current_unit", "current_period",
                "previous_value", "previous_unit", "previous_period", "reported_yoy",
                "gross_margin_current", "gross_margin_previous", "company_explanation",
                "comparability_note", "source_pages", "evidence_quote",
            ),
            8,
        ),
        "growth_drivers": rows(
            "growth_drivers", ("driver", "fact_type", "description", "limitation", "source_pages", "evidence_quote"), 4
        ),
        "industry_context": rows(
            "industry_context", ("topic", "period", "source_type", "company_statement", "analysis", "limitation", "source_pages", "evidence_quote"), 3
        ),
        "strategy_competitiveness": rows(
            "strategy_competitiveness", ("aspect", "management_statement", "action_or_result", "analysis", "limitation", "source_pages", "evidence_quote"), 3
        ),
        "dependencies": rows(
            "dependencies", ("name", "description", "source_pages", "evidence_quote"), 4
        ),
        "risk_factors": rows(
            "risk_factors", ("risk", "description", "management_response", "limitation", "source_pages", "evidence_quote"), 4
        ),
        "major_changes": rows(
            "major_changes", ("change", "period", "business_effect", "source_pages", "evidence_quote"), 4
        ),
        "follow_up_checks": rows(
            "follow_up_checks", ("question", "reason", "next_module", "source_pages", "evidence_quote"), 4
        ),
    }


def analyze_profit_report(
    *,
    file_name: str,
    page_count: int,
    initial_pages: list[dict[str, Any]],
    get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
    business_context: dict[str, Any] | None = None,
    on_tool: Callable[[str, str], None] | None = None,
    on_stage: Callable[[str, str, str], None] | None = None,
    recorder: Any = None,
) -> dict[str, Any]:
    initial_numbers = {int(page["page"]) for page in initial_pages}
    if on_stage:
        on_stage("定位模块二必查内容", "进行中", "检索利润表、业务毛利、费用、特殊项目和管理层说明。")
    retrieval, extra_pages, retrieval_trace = _preflight_profit_search(
        search_pages=search_pages,
        get_page=get_page,
        initial_page_numbers=initial_numbers,
    )
    model_pages = [*initial_pages, *extra_pages]
    provided_pages = {int(page["page"]) for page in model_pages}
    hit_count = sum(bool(section["candidate_pages"]) for section in retrieval["sections"].values())
    if on_stage:
        on_stage("定位模块二必查内容", "已完成", f"{hit_count}/{len(PROFIT_SEARCHES)} 类检索到候选页；候选不等于已经核实。")
        on_stage("DeepSeek提取利润事实", "进行中", f"首批读取 {len(model_pages)} 页；提取金额、期间、层级和出处。")
    material = "\n\n".join(
        f"【年报：{file_name}；PDF第{int(page['page'])}页】\n{page['text']}" for page in model_pages
    )
    extract_messages: list[dict[str, Any]] = [
        {"role": "system", "content": _profit_prompt("extract")},
        {
            "role": "user",
            "content": (
                f"只从本年报提取盈利事实。文件：{file_name}；PDF 共 {page_count} 页。"
                "以下含首批页面和程序检索的候选页，可调用 PDF 搜索/读页工具补查。"
                "候选页不代表已验证；未命中也不表示未披露。\n"
                + json.dumps(retrieval, ensure_ascii=False)
                + "\n\n"
                + material
            ),
        },
    ]
    raw_extraction, trace, usage = _run_tool_loop(
        messages=extract_messages,
        page_count=page_count,
        get_page=get_page,
        search_pages=search_pages,
        on_tool=on_tool,
        available_tools=TOOLS[:2],
        max_rounds=3,
        recorder=recorder,
    )
    used_pages = provided_pages | set(usage.pop("_tool_pages", []))
    extraction = _json_object(raw_extraction)
    if extraction is None:
        raise ModelCallError("DeepSeek 没有返回可读取的利润事实表，请保留文件并重试。")
    preliminary = _normalize_profit_result(
        extraction,
        None,
        allowed_pages=used_pages,
        page_text=get_page,
        read_pages=sorted(used_pages),
        retrieval=retrieval,
    )
    if on_stage:
        line_count = len(preliminary["profit_lines"])
        on_stage("DeepSeek提取利润事实", "已完成", f"已整理 {line_count} 项利润表及损益事实。")
        on_stage("程序核对口径与复算", "进行中", "正在核对引用、期间、范围、币种和利润变化贡献。")
    on_stage and on_stage("程序核对口径与复算", "已完成", preliminary["profit_bridge"]["status"])
    on_stage and on_stage("定向补查差额和原因", "进行中", "根据已核对的利润项目查找年报解释和仍待确认的差额。")
    interpretation_issue = ""
    module_one_context = _compact_business_context(business_context) if business_context else None
    analysis_context = {
        "company": preliminary["company"],
        "period": preliminary["period"],
        "reporting_scope": preliminary["reporting_scope"],
        **({"module_one_business_context": module_one_context} if module_one_context else {}),
        "profit_lines": [
            {
                key: row.get(key)
                for key in (
                    "profit_role", "label", "current_value", "current_unit", "previous_value",
                    "previous_unit", "current_period", "previous_period", "reporting_scope",
                    "currency", "current_yuan", "previous_yuan", "change_yuan", "value_status",
                    "source_pages", "evidence_quote",
                )
            }
            for row in preliminary["profit_lines"][:70]
        ],
        "profit_metrics": preliminary["profit_metrics"],
        "profit_bridge": preliminary["profit_bridge"],
        "profit_checks": preliminary["profit_checks"],
        "business_segments": preliminary["business_segments"],
        "nonrecurring_items": preliminary["nonrecurring_items"],
        "coverage_checks": preliminary["coverage_checks"],
        "uncertainties": preliminary["uncertainties"],
    }
    supplement_trace: list[dict[str, Any]] = []
    supplement_pages: set[int] = set()
    supplement_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        _, supplement_trace, supplement_usage_raw = _run_tool_loop(
            messages=[
                {"role": "system", "content": _profit_prompt("interpret")},
                {
                    "role": "user",
                    "content": (
                        "现在只做年报定向补查，不先写完整解释。根据以下已复核利润结构，"
                        "优先搜索归母利润桥中的未分类差额、同比贡献较大的项目、毛利率或费用率显著变化、"
                        "归母与扣非差异；读取最相关的年报原文页。不能仅凭页名下结论。"
                        "这是一次独立调用，请使用以下完整任务规则与数据，不依赖历史上下文。\n"
                    "如提供了模块一经营背景，它只用于定位盈利变化可能涉及的业务；必须回到本年报核实，并区分业务线索与已证实原因。\n"
                        + json.dumps(analysis_context, ensure_ascii=False)[:22000]
                    ),
                },
            ],
            page_count=page_count,
            get_page=get_page,
            search_pages=search_pages,
            on_tool=on_tool,
            available_tools=TOOLS[:2],
            max_rounds=2,
            recorder=recorder,
        )
        supplement_pages = set(supplement_usage_raw.pop("_tool_pages", []))
        supplement_usage = supplement_usage_raw
    except ModelCallError as exc:
        interpretation_issue = f"定向补查未完成：{exc}"
    except Exception as exc:
        interpretation_issue = f"定向补查未完成：{_safe_model_error(exc)}"
    used_pages.update(supplement_pages)
    for key in ("prompt_tokens", "completion_tokens"):
        usage[key] = usage.get(key, 0) + supplement_usage.get(key, 0)
    if on_stage:
        on_stage(
            "定向补查差额和原因",
            "已完成" if supplement_pages else "需要复核",
            f"按需读取 {len(supplement_pages)} 页；没有取得新页时保留现有证据与原因缺口。",
        )
    on_stage and on_stage("DeepSeek解释盈利变化", "进行中", "只解释已提取事实与程序计算；不修改原始数字。")
    interpretation: dict[str, Any] | None = None
    try:
        explanation_pages: list[dict[str, Any]] = []
        remaining_chars = MAX_PROFIT_EXTRA_CHARS
        for page_number in sorted(used_pages):
            if remaining_chars <= 0:
                break
            text = get_page(page_number)
            if not text:
                continue
            excerpt = str(text)[: min(2600, remaining_chars)].strip()
            if excerpt:
                explanation_pages.append({"page": page_number, "text": excerpt})
                remaining_chars -= len(excerpt)
        explanation_context = {
            **analysis_context,
            "verified_annual_report_pages": explanation_pages,
            "additional_search_trace": supplement_trace,
        }
        response = recorded_completion(
            recorder=recorder,
            client=_client(),
            model=MODEL,
            messages=[
                {"role": "system", "content": _profit_prompt("interpret")},
                {
                    "role": "user",
                    "content": (
                        "这是盈利来源与变化分析的解释阶段。下面的金额、口径及程序计算已经整理。"
                        "不要修改数值、重新分类特殊项目或补造事实；如果给出解释，要用年报原文出处支持。"
                        "对无法解释的差额明确保留。\n\n"
                        + json.dumps(explanation_context, ensure_ascii=False)
                    ),
                },
            ],
            response_format={"type": "json_object"},
            stream=False,
        )
        if not response.choices or not response.choices[0].message.content:
            raise ModelCallError("DeepSeek 没有返回盈利解释。")
        interpretation = _json_object(response.choices[0].message.content or "")
        if response.usage:
            usage["prompt_tokens"] = usage.get("prompt_tokens", 0) + (response.usage.prompt_tokens or 0)
            usage["completion_tokens"] = usage.get("completion_tokens", 0) + (response.usage.completion_tokens or 0)
        if interpretation is None:
            interpretation_issue = "; ".join(
                filter(None, [interpretation_issue, "第二阶段未返回可识别的结构化解释。"])
            )
    except ModelCallError as exc:
        interpretation_issue = "; ".join(filter(None, [interpretation_issue, str(exc)]))
    except Exception as exc:
        interpretation_issue = "; ".join(filter(None, [interpretation_issue, str(_safe_model_error(exc))]))

    if on_stage:
        on_stage(
            "DeepSeek解释盈利变化",
            "已完成" if interpretation is not None else "需要复核",
            "已返回带来源的解释；程序会再次核对引用。" if interpretation is not None else "事实表和程序计算已保留，解释阶段可单独重试。",
        )
        on_stage("展示依据、差额和交接问题", "进行中", "整理利润表、程序复算、发现卡和覆盖状态。")
    result = _normalize_profit_result(
        extraction,
        interpretation,
        allowed_pages=used_pages,
        page_text=get_page,
        read_pages=sorted(used_pages),
        retrieval=retrieval,
        interpretation_issue=interpretation_issue,
    )
    result["model"] = MODEL
    result["usage"] = usage
    result["read_pages"] = sorted(used_pages)
    result["module_one_context_status"] = "已提供模块一经营背景供交叉核查" if business_context else "未提供模块一结果；本模块依据年报独立分析"
    if on_stage:
        on_stage("展示依据、差额和交接问题", "已完成", result["analysis_review_status"])
    trace.extend(supplement_trace)
    trace.append({"tool": "module2_program_calculation", "arguments": {"rules": "profit_v1"}, "pages": [], "result": {"bridge": result["profit_bridge"]["status"]}})
    return {"result": result, "trace": retrieval_trace + trace, "read_pages": sorted(used_pages)}


def answer_profit_question(
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
) -> dict[str, Any]:
    """Answer a follow-up with the module-two rules and only evidence from this report."""
    context = {
        "file_name": file_name,
        "page_count": page_count,
        "company": initial_result.get("company"),
        "period": initial_result.get("period"),
        "reporting_scope": initial_result.get("reporting_scope"),
        "summary": initial_result.get("summary"),
        "profit_lines": initial_result.get("profit_lines", [])[:45],
        "profit_metrics": initial_result.get("profit_metrics", []),
        "profit_bridge": initial_result.get("profit_bridge", {}),
        "business_segments": initial_result.get("business_segments", [])[:20],
        "nonrecurring_items": initial_result.get("nonrecurring_items", [])[:20],
        "findings": initial_result.get("findings", [])[:6],
        "uncertainties": initial_result.get("uncertainties", [])[:10],
        "already_read_pages": initial_result.get("read_pages", []),
        "recent_questions_and_answers": [
            {"question": turn.get("question", "")[:1200], "answer": turn.get("answer", "")[:1400]}
            for turn in history[-6:]
            if turn.get("status") == "completed"
        ],
    }
    content, trace, usage = _run_tool_loop(
        messages=[
            {"role": "system", "content": _profit_prompt("question")},
            {
                "role": "user",
                "content": (
                    "请仅围绕当前年报和模块二既有事实回答。金额、会计计算和符号规则以程序结果为准；"
                    "不能重算后覆盖，也不能把会计贡献说成已经证实的经营原因。需要新出处时先搜索并读取 PDF 页面；"
                    "引用本次实际读取的页码，证据不足就明确说未知。以下是完整上下文：\n"
                    + json.dumps(context, ensure_ascii=False)[:22000]
                    + "\n\n本轮问题："
                    + question[:1200]
                ),
            },
        ],
        page_count=page_count,
        get_page=get_page,
        search_pages=search_pages,
        on_tool=on_tool,
        available_tools=TOOLS[:2],
        recorder=recorder,
        max_rounds=3,
    )
    tool_pages = set(usage.pop("_tool_pages", []))
    allowed_pages = set(int(page) for page in initial_result.get("read_pages", [])) | tool_pages
    cited_pages = sorted(set(int(page) for page in re.findall(r"(?:PDF\s*第\s*|第\s*)(\d+)\s*页", content)))
    unsupported_pages = [page for page in cited_pages if page not in allowed_pages]
    answer = content[:6000]
    if unsupported_pages:
        answer += "\n\n有引用页码不在本次已读取范围内，程序无法确认这些出处；请人工打开原文复核。"
    return {
        "answer": answer,
        "trace": trace,
        "read_pages": sorted(tool_pages),
        "usage": usage,
        "unsupported_pages": unsupported_pages,
    }
