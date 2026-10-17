"""Traceable Decimal calculations for debt, liquidity, and funding facts."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


def parse_decimal(value: Any) -> Decimal:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    text = text.replace("−", "-").replace("﹣", "-").replace("–", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise InvalidOperation
    return Decimal(text)


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def normalize_money(value: Any, unit: Any, currency: Any) -> tuple[str | None, str | None]:
    currency_text = re.sub(r"\s+", "", str(currency or "")).upper()
    unit_text = re.sub(r"\s+", "", str(unit or "")).upper()
    known_cny = any(token in currency_text or token in unit_text for token in ("人民币", "CNY", "RMB"))
    unit_text = unit_text.replace("人民币", "").replace("CNY", "").replace("RMB", "")
    factors = {
        "元": Decimal(1),
        "千元": Decimal(1000),
        "万元": Decimal(10000),
        "亿元": Decimal(100000000),
    }
    factor = factors.get(unit_text)
    if factor is None:
        return None, "金额单位未明确或当前不支持规范化。"
    if not known_cny:
        return None, "币种未由事实或报告明确为人民币。"
    try:
        amount = parse_decimal(value)
    except (InvalidOperation, ValueError):
        return None, "金额不是可解析的纯数字。"
    return decimal_text(amount * factor), None


def _fact(facts: dict[str, dict[str, Any]], fact_id: Any) -> dict[str, Any]:
    key = str(fact_id or "")
    if key not in facts:
        raise ValueError(f"找不到事实 {key or '(空)'}。请先提交并核验年报事实。")
    fact = facts[key]
    if fact.get("validation", {}).get("status") != "quote_and_number_matched":
        raise ValueError(f"事实 {key} 的原文摘录与数字未能自动匹配，不能用于计算。")
    if not fact.get("validation", {}).get("calculation_eligible"):
        details = fact.get("validation", {}).get("semantic_context_problems", [])
        reason = "；".join(str(item) for item in details[:3]) or "来源语义尚未确认。"
        raise ValueError(f"事实 {key} 的来源行列、单位、期间或口径尚未核实，不能用于计算：{reason}")
    return fact


def _amount(fact: dict[str, Any]) -> Decimal:
    if fact.get("normalized_unit") != "CNY" or fact.get("normalized_value") is None:
        raise ValueError(f"事实 {fact.get('fact_id')} 缺少可计算的人民币金额口径。")
    return Decimal(str(fact["normalized_value"]))


def _same_frame(items: list[dict[str, Any]], *, compare_period: bool = True) -> None:
    if not items:
        raise ValueError("没有可用于计算的事实。")
    for key, label in (("currency", "币种"), ("scope", "报表范围")):
        values = {str(item.get(key) or "unknown") for item in items}
        if len(values) != 1 or "unknown" in values:
            raise ValueError(f"输入事实的{label}不一致或未知。")
    if compare_period:
        instant_facts = [item for item in items if item.get("period_type") == "instant"]
        if len(instant_facts) == len(items):
            dates = {str(item.get("as_of_date") or "unknown") for item in items}
            if len(dates) != 1 or "unknown" in dates:
                raise ValueError("时点事实的日期不一致或未知。")
        else:
            periods = {
                (str(item.get("period_start") or "unknown"), str(item.get("period_end") or "unknown"))
                for item in items
            }
            if len(periods) != 1 or any("unknown" in part for part in next(iter(periods))):
                raise ValueError("期间事实的起止日期不一致或未知。")


def _validate_short_debt_frame(
    short_items: list[dict[str, Any]], total_items: list[dict[str, Any]]
) -> None:
    """Compare a reported maturity bucket with an as-of-date debt total.

    A maturity bucket is not a balance-sheet instant, so `_same_frame` cannot
    compare the two directly. Require the bucket to start at the same report
    date and end within the following year; keep currency and scope identical.
    """
    if not short_items or not total_items:
        raise ValueError("短期债务或债务合计缺少事实。")
    for key, label in (("currency", "币种"), ("scope", "报表范围")):
        values = {str(item.get(key) or "unknown") for item in [*short_items, *total_items]}
        if len(values) != 1 or "unknown" in values:
            raise ValueError(f"短期债务与债务合计的{label}不一致或未知。")

    def basis_family(item: dict[str, Any]) -> str:
        basis = re.sub(r"\s+", "", str(item.get("measurement_basis") or "")).lower()
        if basis in {"ending_balance", "statement_carrying_amount"}:
            return "statement_carrying_amount"
        if basis in {
            "contractual_undiscounted_cash_flow",
            "undiscounted_contractual_cash_flows",
        }:
            return "contractual_undiscounted_cash_flow"
        return basis

    measurement_bases = {basis_family(item) for item in [*short_items, *total_items]}
    if len(measurement_bases) != 1 or "" in measurement_bases:
        raise ValueError("短期债务占比的分子和分母必须具备一致且明确的金额口径。")

    if any(item.get("period_type") != "instant" for item in total_items):
        raise ValueError("短期债务占比的分母必须是报告日债务合计。")
    as_of_dates = {str(item.get("as_of_date") or "") for item in total_items}
    if len(as_of_dates) != 1 or not next(iter(as_of_dates)):
        raise ValueError("债务合计的报告日不一致或未知。")
    as_of_text = next(iter(as_of_dates))
    try:
        as_of = date.fromisoformat(as_of_text)
    except (ValueError, TypeError):
        raise ValueError("债务合计的报告日格式无效。")
    try:
        one_year_after = as_of.replace(year=as_of.year + 1)
    except ValueError:
        # A 29 February reporting date maps to 28 February in the next year.
        one_year_after = as_of.replace(year=as_of.year + 1, day=28)

    for item in short_items:
        period_type = item.get("period_type")
        if period_type == "instant":
            if str(item.get("as_of_date") or "") != as_of_text:
                raise ValueError("短期债务时点与债务合计报告日不一致。")
            continue
        if period_type != "maturity_range":
            raise ValueError("短期债务必须是同一报告日的余额或明确的一年内到期区间。")
        start_text = str(item.get("period_start") or item.get("as_of_date") or "")
        end_text = str(item.get("period_end") or "")
        try:
            start = date.fromisoformat(start_text) if start_text else as_of
            end = date.fromisoformat(end_text)
        except (ValueError, TypeError):
            raise ValueError("一年内到期区间缺少可核对的起止日期。")
        if start != as_of or end <= start or end > one_year_after:
            raise ValueError("短期债务区间必须从报告日开始，并在一年内结束。")


def _ids_contained(container: dict[str, Any], children: list[dict[str, Any]], facts: dict[str, dict[str, Any]]) -> bool:
    pending = [str(value) for value in container.get("included_fact_ids", [])]
    seen: set[str] = set()
    while pending:
        fact_id = pending.pop()
        if fact_id in seen:
            continue
        seen.add(fact_id)
        if fact_id in facts:
            pending.extend(str(value) for value in facts[fact_id].get("included_fact_ids", []))
    return all(str(item.get("fact_id")) in seen for item in children)


def _overlap_check(items: list[dict[str, Any]], facts: dict[str, dict[str, Any]]) -> None:
    selected = {str(item["fact_id"]) for item in items}
    for item in items:
        descendants: set[str] = set()
        pending = [str(value) for value in item.get("included_fact_ids", [])]
        while pending:
            fact_id = pending.pop()
            if fact_id in descendants:
                continue
            descendants.add(fact_id)
            nested = facts.get(fact_id, {})
            pending.extend(str(value) for value in nested.get("included_fact_ids", []))
        if selected.intersection(descendants):
            collisions = sorted(selected.intersection(descendants))
            raise ValueError(
                f"合计重复包含事实 {', '.join(collisions)}。请在合计和明细中择一，或先核对其组成关系。"
            )


def aggregate_facts(facts: dict[str, dict[str, Any]], fact_ids: list[Any]) -> tuple[Decimal, list[dict[str, Any]]]:
    if not fact_ids:
        raise ValueError("至少需要一个事实 ID。")
    if len({str(value) for value in fact_ids}) != len(fact_ids):
        raise ValueError("事实 ID 重复，请先去重。")
    items = [_fact(facts, value) for value in fact_ids]
    _same_frame(items)
    _overlap_check(items, facts)
    return sum((_amount(item) for item in items), Decimal(0)), items


def _metric_record(
    name: str,
    value: Decimal | None,
    formula: str,
    items: list[dict[str, Any]],
    *,
    unit: str = "倍",
    note: str = "",
    status: str = "calculated",
) -> dict[str, Any]:
    return {
        "name": name,
        "value": decimal_text(value) if value is not None else None,
        "unit": unit,
        "formula": formula,
        "input_fact_ids": [item["fact_id"] for item in items],
        "input_labels": [item.get("label", "") for item in items],
        "note": note,
        "status": status,
    }


def calculate_metric(facts: dict[str, dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
    metric = str(request.get("metric") or "").strip()
    fact_ids = request.get("fact_ids", [])
    if not isinstance(fact_ids, list):
        raise ValueError("fact_ids 必须是事实 ID 列表。")

    if metric in {"debt_total", "operating_liability_total"}:
        total, items = aggregate_facts(facts, fact_ids)
        expected = "financing" if metric == "debt_total" else "operating"
        if any(item.get("liability_type") != expected for item in items):
            raise ValueError(f"{metric} 只能汇总已完成来源匹配且分类为 {expected} 的负债事实；表格语义仍需人工复核。")
        return _metric_record(
            "已识别融资性债务小计（所列项目）" if expected == "financing" else "经营性负债合计（所列项目）",
            total,
            "互不重叠的同口径融资负债金额求和；该运算本身不证明债务清单完整",
            items,
            unit="元",
            note="仅合计传入的事实 ID；债务项目是否完整须依据核心数据清单和年报附注另行确认。" if expected == "financing" else "仅合计传入的经营性负债事实。",
        )

    if metric == "cash_after_restrictions":
        cash_id = request.get("cash_fact_id")
        restriction_ids = request.get("restriction_fact_ids", [])
        if not isinstance(restriction_ids, list):
            raise ValueError("restriction_fact_ids 必须为列表。")
        cash = _fact(facts, cash_id)
        restrictions_value, restrictions = aggregate_facts(facts, restriction_ids) if restriction_ids else (Decimal(0), [])
        all_items = [cash, *restrictions]
        _same_frame(all_items)
        if any(item.get("liability_type") != "cash_restriction" for item in restrictions):
            raise ValueError("扣减项必须是来源匹配且分类为受限资金的事实；其包含关系仍需复核。")
        if restrictions and not _ids_contained(cash, restrictions, facts):
            raise ValueError("受限项目没有被证明包含在这笔货币资金中，不能直接相减。")
        cash_value = _amount(cash)
        if restrictions_value < 0 or restrictions_value > cash_value:
            raise ValueError("受限资金合计超出货币资金，或受限金额为负；请核实范围和重复项。")
        note = str(request.get("note") or "")[:400]
        return _metric_record(
            "扣除已识别受限部分后的货币资金",
            cash_value - restrictions_value,
            "货币资金 - 已确认属于同一资金总额的受限项目",
            all_items,
            unit="元",
            note=note or "此结果只扣除了已识别限制，不证明其余资金均可随时支取。",
        )

    if metric == "net_debt":
        debt_ids = request.get("debt_fact_ids", [])
        cash_ids = request.get("cash_fact_ids", [])
        debt_value, debts = aggregate_facts(facts, debt_ids) if isinstance(debt_ids, list) else (None, [])
        cash_value, cash = aggregate_facts(facts, cash_ids) if isinstance(cash_ids, list) else (None, [])
        all_items = [*debts, *cash]
        _same_frame(all_items)
        if any(item.get("liability_type") != "financing" for item in debts):
            raise ValueError("净债务中的负债必须是来源匹配且分类为融资性的事实；语义仍需复核。")
        if any(item.get("liability_type") not in {"cash", "cash_restricted_net"} for item in cash):
            raise ValueError("净债务的资金侧只能使用明确说明口径的现金事实。")
        return _metric_record(
            "净债务",
            debt_value - cash_value,
            "指定口径的融资性债务 - 指定口径的货币资金",
            all_items,
            unit="元",
            note="仅按传入的债务和资金事实计算；负值表示本口径下为净现金。债务清单完整性需另行确认，现金口径不等同于全部可随时动用资金。",
        )

    if metric == "net_gearing":
        debt_ids = request.get("debt_fact_ids", [])
        cash_ids = request.get("cash_fact_ids", [])
        equity = _fact(facts, request.get("equity_fact_id"))
        if not isinstance(debt_ids, list) or not isinstance(cash_ids, list):
            raise ValueError("债务和资金输入必须是事实 ID 列表。")
        debt_value, debts = aggregate_facts(facts, debt_ids)
        cash_value, cash = aggregate_facts(facts, cash_ids)
        all_items = [*debts, *cash, equity]
        _same_frame(all_items)
        if any(item.get("liability_type") != "financing" for item in debts):
            raise ValueError("净负债率中的负债必须是融资性债务。")
        if any(item.get("liability_type") not in {"cash", "cash_restricted_net"} for item in cash):
            raise ValueError("净负债率只能使用明确说明口径的货币资金。")
        if equity.get("liability_type") != "equity" or _amount(equity) <= 0:
            return _metric_record(
                "净负债率",
                None,
                "(指定口径融资性债务 - 指定口径货币资金) / 同口径总权益",
                all_items,
                note="权益为零、负数或未识别为权益项目，净负债率不适用。",
                status="not_applicable",
            )
        return _metric_record(
            "净负债率",
            (debt_value - cash_value) / _amount(equity),
            "(指定口径融资性债务 - 指定口径货币资金) / 同口径总权益",
            all_items,
            unit="比值",
            note="仅按传入的债务和资金事实计算；负值表示本口径下净现金。债务清单完整性需另行确认；权益、债务和资金必须同范围、同一时点。",
        )

    if metric == "short_debt_share":
        short_ids = request.get("short_debt_fact_ids", [])
        total_ids = request.get("total_debt_fact_ids", [])
        if not isinstance(short_ids, list) or not isinstance(total_ids, list):
            raise ValueError("短期债务和债务合计输入必须为事实 ID 列表。")
        short_value, short_items = aggregate_facts(facts, short_ids)
        total_value, total_items = aggregate_facts(facts, total_ids)
        all_items = [*short_items, *total_items]
        _validate_short_debt_frame(short_items, total_items)
        if any(item.get("liability_type") != "financing" for item in all_items):
            raise ValueError("短期融资债务占比只使用融资性债务事实。")
        if not _ids_contained({"included_fact_ids": [item["fact_id"] for item in total_items]}, short_items, facts):
            raise ValueError("短期融资债务未能核实为融资债务合计的组成部分。")
        if total_value <= 0:
            raise ValueError("融资债务合计为零或负数，不计算占比。")
        if short_value > total_value:
            raise ValueError("一年内融资债务超过所列融资债务合计，组成关系或金额需复核。")
        return _metric_record(
            "短期融资债务占所列融资债务小计比例",
            short_value / total_value,
            "同一金额口径的不重叠一年内融资债务 / 所列融资债务事实小计（该运算不证明完整债务清单）",
            all_items,
            unit="比值",
            note="分子和分母必须采用相同金额口径；债务清单完整性仍需依据核心数据清单和年报附注确认。",
        )

    if metric in {"asset_liability_ratio", "current_ratio", "quick_ratio"}:
        minimum = 3 if metric == "quick_ratio" else 2
        if len(fact_ids) != minimum:
            raise ValueError(f"{metric} 需要按提示提供 {minimum} 个事实 ID。")
        items = [_fact(facts, value) for value in fact_ids]
        _same_frame(items)
        values = [_amount(item) for item in items]
        if metric == "quick_ratio":
            numerator = values[0] - values[1]
            denominator = values[2]
            formula = "(流动资产 - 存货) / 流动负债（简化速动比率，包含的其他流动资产需另行说明）"
            name = "速动比率（简化口径）"
        else:
            numerator, denominator = values[0], values[1]
            definitions = {
                "asset_liability_ratio": ("资产负债率", "总负债 / 总资产"),
                "current_ratio": ("流动比率", "流动资产 / 流动负债"),
            }
            name, formula = definitions[metric]
        if denominator == 0:
            raise ValueError("分母为零，不计算比率。")
        return _metric_record(name, numerator / denominator, formula, items)

    if metric == "operating_cashflow_to_current_liabilities":
        if len(fact_ids) != 3:
            raise ValueError("该指标按顺序需要全年经营现金流、期初流动负债和期末流动负债三个事实。")
        cashflow, opening, closing = [_fact(facts, value) for value in fact_ids]
        if cashflow.get("period_type") != "duration" or opening.get("period_type") != "instant" or closing.get("period_type") != "instant":
            raise ValueError("需要全年经营现金流与期初、期末流动负债余额。")
        if cashflow.get("measurement_basis") not in {"period_flow", "statement_cash_flow"}:
            raise ValueError("第一项必须明确为期间现金流，不可把资产负债时点余额误作经营现金流。")
        for key, label in (("currency", "币种"), ("scope", "报表范围")):
            if len({str(item.get(key) or "unknown") for item in (cashflow, opening, closing)}) != 1 or not cashflow.get(key):
                raise ValueError(f"经营现金流和流动负债的{label}不一致或未知。")
        year = str(cashflow.get("period_end") or "")[:4]
        if not year.isdigit():
            raise ValueError("经营现金流缺少可识别的期间截止日期。")
        if str(opening.get("as_of_date") or "")[:4] != str(int(year) - 1):
            raise ValueError("期初流动负债与经营现金流的年度不匹配。")
        if str(closing.get("as_of_date") or "")[:4] != year:
            raise ValueError("期末流动负债与经营现金流的年度不匹配。")
        average_liabilities = (_amount(opening) + _amount(closing)) / Decimal(2)
        if average_liabilities == 0:
            raise ValueError("平均流动负债为零，不计算该比率。")
        result = _metric_record(
            "经营现金流对平均流动负债保障倍数",
            _amount(cashflow) / average_liabilities,
            "全年经营活动现金流量净额 / [(期初流动负债 + 期末流动负债) / 2]",
            [cashflow, opening, closing],
            note="历史流量指标，不是未来现金预测；两端平均不反映季节性。",
        )
        return result

    if metric == "interest_coverage":
        if len(fact_ids) != 2:
            raise ValueError("该指标需要利润总额和与其同年度、同范围的费用化利息支出。")
        profit, interest = [_fact(facts, value) for value in fact_ids]
        _same_frame([profit, interest])
        if interest.get("liability_type") != "interest_expense":
            raise ValueError("第二项必须是披露的利息支出，不能用财务费用替代。")
        interest_value = _amount(interest)
        if interest_value <= 0:
            raise ValueError("利息支出为零或负数，不能计算通常意义的利息保障倍数。")
        pbt = _amount(profit)
        return _metric_record(
            "费用化利息保障倍数",
            (pbt + interest_value) / interest_value,
            "(利润总额 + 与其口径匹配的费用化利息支出) / 费用化利息支出",
            [profit, interest],
            note="利润总额加回费用化利息形成近似息税前收益；不代表本金偿付能力或实际付息现金覆盖。",
        )

    raise ValueError(f"暂不支持计算指标 {metric!r}。")
