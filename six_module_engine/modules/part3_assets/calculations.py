"""Exact asset comparisons and turnover calculations with explicit applicability rules."""

from __future__ import annotations

import re
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


DAYS_PER_YEAR = Decimal(365)
UNIT_MULTIPLIERS = {
    "元": Decimal(1), "人民币元": Decimal(1), "cny": Decimal(1), "rmb": Decimal(1),
    "千元": Decimal(1_000), "万元": Decimal(10_000), "亿元": Decimal(100_000_000),
}

SNAPSHOT_METRICS = {
    "total_assets", "current_assets", "noncurrent_assets", "cash_and_equivalents",
    "accounts_receivable", "notes_receivable", "receivables_financing", "contract_assets",
    "other_receivables", "prepayments", "inventory", "inventory_component", "fixed_assets",
    "construction_in_progress", "goodwill", "intangible_assets", "right_of_use_assets",
    "investment_property", "long_term_equity_investment", "other_asset",
}

FLOW_METRICS = {"operating_revenue", "operating_cost", "asset_impairment_loss", "credit_impairment_loss"}


def _decimal(value: Any) -> Decimal | None:
    raw = str(value or "").replace(",", "").replace("，", "").strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", raw):
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def amount_in_yuan(fact: dict[str, Any]) -> Decimal | None:
    if not fact.get("calculation_ready") or str(fact.get("period_type")) not in {"instant", "flow"}:
        return None
    currency = str(fact.get("currency") or "").strip().upper()
    if currency not in {"CNY", "RMB", "人民币"}:
        return None
    unit = re.sub(r"\s+", "", str(fact.get("unit") or "")).upper()
    normalized = unit.replace("CNY", "人民币").replace("RMB", "人民币")
    multiplier = None
    for candidate, factor in sorted(UNIT_MULTIPLIERS.items(), key=lambda item: len(item[0]), reverse=True):
        if normalized == candidate.upper():
            multiplier = factor
            break
        if candidate in {"元", "千元", "万元", "亿元"} and normalized.endswith(candidate.upper()):
            multiplier = factor
            break
    raw = _decimal(fact.get("value"))
    return raw * multiplier if raw is not None and multiplier is not None else None


def _as_of(fact: dict[str, Any]) -> str:
    return str(fact.get("as_of_date") or fact.get("period_end") or "")[:10]


def _opening_balance_date(period_start: str) -> str:
    """Map a flow's first calendar day to the previous closing balance date."""
    try:
        return (date.fromisoformat(str(period_start)[:10]) - timedelta(days=1)).isoformat()
    except ValueError:
        return ""


def _group_key(fact: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(fact.get("metric_key") or ""),
        str(fact.get("measurement_basis") or "unknown"),
        str(fact.get("dimension") or ""),
        str(fact.get("reporting_scope") or "unknown"),
        str(fact.get("currency") or "unknown"),
    )


def _calculation(name: str, formula: str, inputs: list[dict[str, Any]], output: Any, note: str = "") -> dict[str, Any]:
    input_fact_ids = [str(item["fact_id"]) for item in inputs if str(item.get("fact_id") or "").startswith("assets-f")]
    input_calculation_ids = [
        str(item.get("calculation_id") or item.get("fact_id"))
        for item in inputs
        if str(item.get("calculation_id") or item.get("fact_id") or "").startswith("assets-c")
    ]
    identity = hashlib.sha256(
        json.dumps([name, formula, input_fact_ids, input_calculation_ids, output], ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16]
    if name.startswith("每100元收入"):
        unit = "元/百元收入"
    elif name.endswith("金额变化"):
        unit = "元"
    elif name.endswith("资产占比") or name.endswith("变化率"):
        unit = "比值"
    elif name.endswith("周转次数"):
        unit = "次/年"
    elif name.endswith("周转天数"):
        unit = "日"
    else:
        unit = "数值"
    return {
        "calculation_id": f"assets-c{identity}",
        "name": name,
        "formula": formula,
        "input_fact_ids": input_fact_ids,
        "input_calculation_ids": input_calculation_ids,
        "inputs": [{
            "fact_id": item.get("fact_id") if str(item.get("fact_id") or "").startswith("assets-f") else None,
            "calculation_id": item.get("calculation_id") or (item.get("fact_id") if str(item.get("fact_id") or "").startswith("assets-c") else None),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "as_of_date": _as_of(item),
        } for item in inputs],
        "output": output,
        "unit": unit,
        "period_start": next((item.get("period_start") for item in inputs if item.get("period_start")), ""),
        "period_end": next((item.get("period_end") or item.get("as_of_date") for item in inputs if item.get("period_end") or item.get("as_of_date")), ""),
        "reporting_scope": next((item.get("reporting_scope") for item in inputs if item.get("reporting_scope")), "unknown"),
        "currency": next((item.get("currency") for item in inputs if item.get("currency")), "unknown"),
        "rule_version": "assets-calculations-v1",
        "note": note,
    }


def calculate_asset_metrics(facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calculations: list[dict[str, Any]] = []
    limitations: list[dict[str, str]] = []
    usable = [fact for fact in facts if fact.get("calculation_ready") and amount_in_yuan(fact) is not None]
    by_group: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for fact in usable:
        if fact.get("metric_key") in SNAPSHOT_METRICS and _as_of(fact):
            by_group.setdefault(_group_key(fact), []).append(fact)

    for (metric_key, basis, dimension, scope, _currency), rows in by_group.items():
        rows = sorted(rows, key=_as_of)
        if not rows:
            continue
        current = rows[-1]
        current_value = amount_in_yuan(current)
        if current_value is None:
            continue
        label = current.get("original_label") or metric_key
        current_date = _as_of(current)
        total_fact = _matching_snapshot(by_group, "total_assets", "carrying_value", current_date, scope, _currency)
        if not total_fact and metric_key == "total_assets":
            total_fact = current
        if total_fact and basis in {"carrying_value", "net"}:
            total_value = amount_in_yuan(total_fact)
            if total_value is not None and total_value != 0:
                share = current_value / total_value
                calculations.append(_calculation(f"{label}资产占比", "同一时点科目净额÷总资产", [current, total_fact], _decimal_text(share)))

        if len(rows) >= 2:
            previous = rows[-2]
            previous_value = amount_in_yuan(previous)
            if previous_value is None:
                continue
            delta = current_value - previous_value
            calculations.append(_calculation(f"{label}金额变化", "本期末金额－上期末金额", [previous, current], _decimal_text(delta)))
            if previous_value > 0:
                calculations.append(_calculation(f"{label}同比变化率", "(本期末－上期末)÷上期末", [previous, current], _decimal_text(delta / previous_value)))
            else:
                limitations.append({"topic": str(label), "reason": "上期基数为零或负数，未计算普通增长率。"})

    # Validate and calculate gross less allowance only when the model explicitly ties both facts to one report row.
    ar_by_date = _basis_sets(usable, "accounts_receivable")
    for group, bases in ar_by_date.items():
        gross, allowance, net = bases.get("gross"), bases.get("allowance"), bases.get("net")
        _reconcile_bases("应收账款总额-准备-净额", group[0], gross, allowance, net, calculations, limitations)
    inventory_by_date = _basis_sets(usable, "inventory")
    for group, bases in inventory_by_date.items():
        gross, allowance, net = bases.get("gross"), bases.get("allowance"), bases.get("net")
        _reconcile_bases("存货总额-跌价准备-净额", group[0], gross, allowance, net, calculations, limitations)

    snapshot_lookup = {
        (fact.get("metric_key"), fact.get("measurement_basis"), fact.get("dimension"), fact.get("reporting_scope"), _as_of(fact)): fact
        for fact in usable if fact.get("metric_key") in SNAPSHOT_METRICS
    }
    for flow in usable:
        metric_key = flow.get("metric_key")
        if metric_key not in FLOW_METRICS:
            continue
        end_date = str(flow.get("period_end") or "")[:10]
        start_date = str(flow.get("period_start") or "")[:10]
        if not end_date or not start_date:
            continue
        opening_date = _opening_balance_date(start_date) or start_date
        for metric, basis, result_label in _turnover_targets(metric_key):
            start = (
                snapshot_lookup.get((metric, basis, flow.get("dimension", ""), flow.get("reporting_scope"), opening_date))
                or snapshot_lookup.get((metric, basis, flow.get("dimension", ""), flow.get("reporting_scope"), start_date))
            )
            end = snapshot_lookup.get((metric, basis, flow.get("dimension", ""), flow.get("reporting_scope"), end_date))
            if not start or not end:
                continue
            average = (amount_in_yuan(start) + amount_in_yuan(end)) / Decimal(2)
            numerator = amount_in_yuan(flow)
            if average <= 0 or numerator is None or numerator <= 0:
                limitations.append({"topic": result_label, "reason": "分母或期间数值不为正，未计算周转指标。"})
                continue
            inputs = [flow, start, end]
            turnover = numerator / average
            turnover_note = "使用两端平均余额近似。"
            if metric == "accounts_receivable":
                turnover_note += "以营业收入近似赊销额，年报未提供赊销额时不能视为精确回款周转。"
            calculations.append(_calculation(f"{result_label}周转次数", "期间营业收入或营业成本÷[(期初资产+期末资产)÷2]", inputs, _decimal_text(turnover), turnover_note))
            day_note = "采用365日；并非逐笔回款/库存停留天数。"
            if metric == "accounts_receivable":
                day_note += "应收账款周转天数同样受营业收入替代赊销额的影响。"
            calculations.append(_calculation(f"{result_label}周转天数", "365÷周转次数", inputs, _decimal_text(DAYS_PER_YEAR / turnover), day_note))

    occupancy_rows, occupancy_notes = _asset_occupancy_per_100_revenue(usable)
    calculations.extend(occupancy_rows)
    limitations.extend(occupancy_notes)
    calculations.extend(_occupancy_period_changes(occupancy_rows))

    return calculations, limitations


def _matching_snapshot(by_group, metric, basis, as_of, scope, currency):
    return next((rows[-1] for key, rows in by_group.items() if key[0] == metric and key[1] == basis and key[2] == "" and key[3] == scope and key[4] == currency and _as_of(rows[-1]) == as_of), None)


def _basis_sets(facts, metric):
    result: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = {}
    for fact in facts:
        if fact.get("metric_key") == metric and _as_of(fact):
            key = (
                _as_of(fact),
                str(fact.get("reporting_scope") or "unknown"),
                str(fact.get("currency") or "unknown"),
                str(fact.get("dimension") or ""),
            )
            result.setdefault(key, {}).setdefault(str(fact.get("measurement_basis")), fact)
    return result


def _reconcile_bases(name, as_of, gross, allowance, net, calculations, limitations):
    if not (gross and allowance and net):
        return
    gross_value, allowance_value, net_value = map(amount_in_yuan, (gross, allowance, net))
    if None in (gross_value, allowance_value, net_value):
        return
    difference = gross_value - abs(allowance_value) - net_value
    calculations.append(_calculation(name, "账面总额－相关准备绝对额－账面净额", [gross, allowance, net], _decimal_text(difference), f"{as_of}；若报告以负数列示准备则使用绝对额；零表示数值勾稽，准备列不等于本期减值损失。"))
    if difference != 0:
        limitations.append({"topic": name, "reason": f"{as_of}总额－准备－净额差额为 {_decimal_text(difference)} 元，需回查口径或披露精度。"})


def _turnover_targets(flow_metric):
    if flow_metric == "operating_revenue":
        return (
            ("accounts_receivable", "gross", "应收账款总额"),
            ("accounts_receivable", "net", "应收账款净额（近似）"),
            ("fixed_assets", "carrying_value", "固定资产净额"),
            ("total_assets", "carrying_value", "总资产"),
        )
    if flow_metric == "operating_cost":
        return (("inventory", "net", "存货净额"), ("inventory", "gross", "存货总额"))
    return ()


def _asset_occupancy_per_100_revenue(facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Explain asset usage per 100 yuan of revenue from matched two-endpoint averages."""
    calculations: list[dict[str, Any]] = []
    limitations: list[dict[str, str]] = []
    revenues = [
        fact for fact in facts
        if fact.get("metric_key") == "operating_revenue"
        and fact.get("period_type") == "flow"
        and fact.get("period_start")
        and fact.get("period_end")
        and amount_in_yuan(fact) is not None
    ]
    for revenue in revenues:
        start_date = str(revenue.get("period_start") or "")[:10]
        end_date = str(revenue.get("period_end") or "")[:10]
        opening_date = _opening_balance_date(start_date) or start_date
        scope = str(revenue.get("reporting_scope") or "unknown")
        currency = str(revenue.get("currency") or "unknown")
        revenue_value = amount_in_yuan(revenue)
        if not start_date or not end_date or not revenue_value or revenue_value <= 0:
            continue

        def find(metric: str, basis: str, at_date: str, dimension: str = ""):
            return next((fact for fact in facts if fact.get("metric_key") == metric
                and fact.get("measurement_basis") == basis
                and str(fact.get("dimension") or "") == dimension
                and str(fact.get("reporting_scope") or "unknown") == scope
                and str(fact.get("currency") or "unknown") == currency
                and _as_of(fact) == at_date), None)

        total_start = find("total_assets", "carrying_value", opening_date) or find("total_assets", "carrying_value", start_date)
        total_end = find("total_assets", "carrying_value", end_date)
        if not total_start or not total_end:
            limitations.append({"topic": "每100元收入资产占用", "reason": f"缺少 {opening_date} 或 {end_date} 同范围总资产，未计算两端平均资产占用。"})
            continue
        total_start_value, total_end_value = amount_in_yuan(total_start), amount_in_yuan(total_end)
        total_average = (total_start_value + total_end_value) / Decimal(2)
        if total_average <= 0:
            continue

        component_specs = (
            ("cash_and_equivalents", "carrying_value"),
            ("accounts_receivable", "net"), ("notes_receivable", "carrying_value"),
            ("receivables_financing", "carrying_value"), ("contract_assets", "carrying_value"),
            ("other_receivables", "carrying_value"), ("prepayments", "carrying_value"),
            ("inventory", "net"), ("fixed_assets", "carrying_value"),
            ("construction_in_progress", "carrying_value"), ("goodwill", "carrying_value"),
            ("intangible_assets", "carrying_value"), ("right_of_use_assets", "carrying_value"),
            ("investment_property", "carrying_value"), ("long_term_equity_investment", "carrying_value"),
        )
        components: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for metric, basis in component_specs:
            start, end = find(metric, basis, opening_date) or find(metric, basis, start_date), find(metric, basis, end_date)
            if start and end:
                components.append((metric, start, end))

        # Prefer the disaggregated inventory categories only if the report did not provide total inventory.
        if not (find("inventory", "net", opening_date) or find("inventory", "net", start_date)) or not find("inventory", "net", end_date):
            seen_dimensions: set[str] = set()
            for fact in facts:
                if fact.get("metric_key") != "inventory_component" or fact.get("measurement_basis") != "carrying_value":
                    continue
                if str(fact.get("reporting_scope") or "unknown") != scope or str(fact.get("currency") or "unknown") != currency:
                    continue
                dimension = str(fact.get("dimension") or "")
                if not dimension or dimension in seen_dimensions:
                    continue
                start = find("inventory_component", "carrying_value", opening_date, dimension) or find("inventory_component", "carrying_value", start_date, dimension)
                end = find("inventory_component", "carrying_value", end_date, dimension)
                if start and end:
                    components.append((f"inventory_component:{dimension}", start, end))
                    seen_dimensions.add(dimension)

        used_average = Decimal(0)
        used_inputs = []
        for metric, start, end in components:
            start_value, end_value = amount_in_yuan(start), amount_in_yuan(end)
            if start_value is None or end_value is None:
                continue
            average = (start_value + end_value) / Decimal(2)
            used_average += average
            used_inputs.extend((start, end))
            calculations.append(_calculation(
                f"每100元收入对应{metric}平均占用_{str(revenue.get('period_end') or '')[:4]}",
                "100×(资产期初余额+资产期末余额)÷2÷营业收入",
                [revenue, start, end],
                _decimal_text(Decimal(100) * average / revenue_value),
                "同报告期、范围及币种；用于定位资产占用，不是现金周转天数。",
            ))
        remainder = total_average - used_average
        if remainder < Decimal("-1"):
            limitations.append({
                "topic": "每100元收入未单列资产占用",
                "reason": "已列示资产项目的平均余额合计超过平均总资产，可能存在重复或口径不一致；未生成负数剩余项。",
            })
        elif components:
            if remainder < 0:
                remainder = Decimal(0)
            calculations.append(_calculation(
                f"每100元收入对应未单列资产占用_{str(revenue.get('period_end') or '')[:4]}",
                "100×(平均总资产－已单列且不重复的平均资产)÷营业收入",
                [revenue, total_start, total_end, *used_inputs],
                _decimal_text(Decimal(100) * remainder / revenue_value),
                "剩余项含未单列科目；资产项必须互斥，净额/账面价值应与总资产一致。",
            ))
        calculations.append(_calculation(
            f"每100元收入对应平均总资产占用_{str(revenue.get('period_end') or '')[:4]}",
            "100×(总资产期初余额+总资产期末余额)÷2÷营业收入",
            [revenue, total_start, total_end],
            _decimal_text(Decimal(100) * total_average / revenue_value),
            "使用两端平均余额近似。",
        ))
    return calculations, limitations


def _occupancy_period_changes(calculations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in calculations:
        if not str(item.get("name", "")).startswith("每100元收入"):
            continue
        base = re.sub(r"_\d{4}$", "", str(item.get("name") or ""))
        group = (base, str(item.get("reporting_scope") or "unknown"), str(item.get("currency") or "unknown"))
        groups.setdefault(group, []).append(item)
    changes: list[dict[str, Any]] = []
    for (base, _scope, _currency), rows in groups.items():
        rows = sorted(rows, key=lambda item: str(item.get("period_end") or ""))
        for previous, current in zip(rows, rows[1:]):
            previous_value = _decimal(previous.get("output"))
            current_value = _decimal(current.get("output"))
            if previous_value is None or current_value is None or previous.get("period_end") == current.get("period_end"):
                continue
            changes.append(_calculation(
                f"{base}变化",
                "本期每百元收入占用－上期每百元收入占用",
                [
                    {"calculation_id": current["calculation_id"], "value": str(current_value), "unit": current["unit"], "period_end": current["period_end"], "reporting_scope": current["reporting_scope"], "currency": current["currency"]},
                    {"calculation_id": previous["calculation_id"], "value": str(previous_value), "unit": previous["unit"], "period_end": previous["period_end"], "reporting_scope": previous["reporting_scope"], "currency": previous["currency"]},
                ],
                _decimal_text(current_value - previous_value),
                "仍以元/百元收入表示；换算为百分点需再乘100。",
            ))
    return changes
