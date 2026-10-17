"""精确现金流计算。只计算事实 ID 所引用且有合格数值来源的项目。"""

from __future__ import annotations

import re
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any


def _period_descriptor(fact: dict[str, Any]) -> tuple[int | None, str, str]:
    period = re.sub(r"\s+", "", str(fact.get("period", ""))).strip()
    kind = str(fact.get("period_kind", "unknown"))
    year_match = re.search(r"(?:19|20)\d{2}", period)
    year = int(year_match.group(0)) if year_match else None
    signature = re.sub(r"(?:19|20)\d{2}(?:年度|年)?", "", period)
    signature = signature.replace("年度", "").replace("年", "").replace("度", "")
    signature = signature.replace("至", "-").replace("—", "-").replace("－", "-")
    signature = signature.strip("-_")
    annual_date_range = re.fullmatch(r"0?1月(?:0?1日)?-12月(?:31日)?", signature)
    if kind == "period_amount" and (
        signature in {"", "全年", "1-12月", "1月-12月"} or annual_date_range
    ):
        signature = "annual"
    return year, signature, kind


def _same_interval(facts: list[dict[str, Any]]) -> bool:
    descriptors = [_period_descriptor(fact) for fact in facts]
    if any(year is None or not signature or kind not in {"period_amount", "instant_amount"}
           for year, signature, kind in descriptors):
        return False
    return len(set(descriptors)) == 1


def _matching_yoy_period(current: dict[str, Any], prior: dict[str, Any]) -> bool:
    current_year, current_signature, current_kind = _period_descriptor(current)
    prior_year, prior_signature, prior_kind = _period_descriptor(prior)
    return (
        current_year is not None
        and prior_year is not None
        and current_year == prior_year + 1
        and current_signature == prior_signature
        and current_kind == prior_kind
    )


def _same_metric_key(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = re.sub(r"\s+", "", str(left.get("metric_key", "")).strip()).lower()
    right_key = re.sub(r"\s+", "", str(right.get("metric_key", "")).strip()).lower()
    return bool(left_key and left_key == right_key)


def _has_metric_role(fact: dict[str, Any], accepted_keys: set[str]) -> bool:
    key = re.sub(r"\s+", "", str(fact.get("metric_key", "")).strip()).lower()
    return bool(key and key in accepted_keys)


def _scope_key(value: Any) -> str:
    scope = re.sub(r"\s+", "", str(value or "").strip()).lower()
    if not scope or scope == "未知":
        return ""
    if "母公司" in scope or "母公司本部" in scope:
        return "parent"
    if "合并" in scope or "集团" in scope:
        return "consolidated"
    return scope


def calculate(
    request: dict[str, Any],
    facts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    operation = str(request.get("operation", ""))
    if operation == "net_by_direction":
        inflow_ids = [str(value) for value in request.get("inflow_fact_ids", [])]
        outflow_ids = [str(value) for value in request.get("outflow_fact_ids", [])]
        if not inflow_ids or not outflow_ids:
            raise ValueError("流入流出净额必须分别提供至少一条流入和流出事实；缺少项不能视为零")
        if len(set(inflow_ids + outflow_ids)) != len(inflow_ids + outflow_ids):
            raise ValueError("流入和流出编号不能重复或同时出现在两个方向")
        ids = inflow_ids + outflow_ids
    else:
        ids = [str(value) for value in request.get("fact_ids", [])]
    if not ids:
        raise ValueError("计算至少需要一条事实编号")
    missing = [fact_id for fact_id in ids if fact_id not in facts]
    if missing:
        raise ValueError(f"找不到事实编号：{', '.join(missing)}")
    if len(set(ids)) != len(ids):
        raise ValueError("同一事实编号不能重复参与计算，以免重复计入金额")
    expected_counts = {"difference": 2, "ratio": 2, "yoy": 2, "cash_bridge": 5, "cash_balance_bridge": 3, "operating_after_capex": 2}
    if operation in expected_counts and len(ids) != expected_counts[operation]:
        raise ValueError(f"{operation} 需要且只需要 {expected_counts[operation]} 条事实")
    if operation == "profit_to_cash" and len(ids) < 2:
        raise ValueError("利润调节至少需要净利润和披露的经营现金净额")
    selected = [facts[fact_id] for fact_id in ids]
    unusable = [fact["fact_id"] for fact in selected if not fact.get("calculable")]
    if unusable:
        return {
            "status": "not_calculated",
            "reason": "输入事实的页码、原文数字或人民币单位尚未核对",
            "fact_ids": ids,
            "unusable_fact_ids": unusable,
        }

    units = {fact["normalized_unit"] for fact in selected}
    if len(units) != 1:
        return {"status": "not_calculated", "reason": "输入事实的金额单位或币种不同", "fact_ids": ids}
    scopes = {_scope_key(fact.get("scope")) for fact in selected}
    if "" in scopes or len(scopes) != 1:
        return {"status": "not_calculated", "reason": "输入事实的合并/母公司范围未知或不一致", "fact_ids": ids}
    kinds = [str(fact.get("period_kind", "unknown")) for fact in selected]
    if any(kind not in {"period_amount", "instant_amount"} for kind in kinds):
        return {"status": "not_calculated", "reason": "事实的期间金额/时点余额类型未知或不支持", "fact_ids": ids}
    if operation == "cash_balance_bridge":
        if len(selected) != 3 or kinds != ["instant_amount", "instant_amount", "period_amount"]:
            return {
                "status": "not_calculated",
                "reason": "现金余额勾稽需依次提供期末时点余额、期初时点余额、本期期间变动",
                "fact_ids": ids,
            }
        ending_year, ending_signature, _ = _period_descriptor(selected[0])
        opening_year, opening_signature, _ = _period_descriptor(selected[1])
        flow_year, flow_signature, _ = _period_descriptor(selected[2])
        opening_period = str(selected[1].get("period", ""))
        opening_is_start = any(term in opening_period for term in ("期初", "年初", "1月1日", "01-01", "01/01"))
        ending_is_year_end = ending_signature in {"12-31", "12月31日", "末", "年末", "期末"}
        opening_is_prior_year_end = opening_signature in {"12-31", "12月31日", "末", "年末", "期末"}
        if (
            ending_year is None or opening_year is None or flow_year is None
            or flow_signature != "annual"
            or ending_year != flow_year
            or not ending_is_year_end
            or opening_year not in {ending_year, ending_year - 1}
            or (opening_year == ending_year and not opening_is_start)
            or (opening_year == ending_year - 1 and not opening_is_prior_year_end)
        ):
            return {
                "status": "not_calculated",
                "reason": "期初、期末余额日期与本期现金流期间无法确认对应关系",
                "fact_ids": ids,
            }
    elif operation == "yoy":
        if (
            len(selected) != 2
            or not _matching_yoy_period(selected[0], selected[1])
            or not _same_metric_key(selected[0], selected[1])
        ):
            return {
                "status": "not_calculated",
                "reason": "同比需按本期、上期顺序提供相邻年度、期间对应且 metric_key 相同的事实",
                "fact_ids": ids,
            }
    elif operation == "yoy_bridge":
        if len(selected) < 4 or len(selected) % 2:
            raise ValueError("同比贡献拆解需按本期、上期成对提供至少两组事实，最后一组为目标总额")
        current_periods: set[tuple[int | None, str, str]] = set()
        for current, prior in zip(selected[::2], selected[1::2]):
            if not _matching_yoy_period(current, prior):
                return {
                    "status": "not_calculated",
                    "reason": "同比贡献拆解中的每一对事实都必须是相邻年度的同类期间",
                    "fact_ids": ids,
                }
            current_periods.add(_period_descriptor(current))
            current_metric = str(current.get("metric_key", "")).strip()
            prior_metric = str(prior.get("metric_key", "")).strip()
            if not current_metric or current_metric != prior_metric:
                return {
                    "status": "not_calculated",
                    "reason": "同比贡献拆解的一对事实项目编号不一致",
                    "fact_ids": ids,
                }
        if len(current_periods) != 1:
            return {
                "status": "not_calculated",
                "reason": "同比贡献拆解中的各分项必须使用同一组本期和上期",
                "fact_ids": ids,
            }
        if any(kind != "period_amount" for kind in kinds):
            return {
                "status": "not_calculated",
                "reason": "同比贡献拆解只适用于年度期间流量，不适用于时点余额",
                "fact_ids": ids,
            }
        if len({fact["normalized_unit"] for fact in selected}) != 1:
            return {"status": "not_calculated", "reason": "同比贡献拆解的单位或币种不一致", "fact_ids": ids}
        bridge_scopes = {_scope_key(fact.get("scope")) for fact in selected}
        if "" in bridge_scopes or len(bridge_scopes) != 1:
            return {"status": "not_calculated", "reason": "同比贡献拆解的合并范围不一致", "fact_ids": ids}
    elif operation == "difference":
        if not (_same_interval(selected) or _matching_yoy_period(selected[0], selected[1])):
            return {
                "status": "not_calculated",
                "reason": "差额需使用同一期间项目，或按本期、上期顺序提供相邻年度的同类项目",
                "fact_ids": ids,
            }
        if _matching_yoy_period(selected[0], selected[1]) and not _same_metric_key(selected[0], selected[1]):
            return {
                "status": "not_calculated",
                "reason": "跨年度差额必须比较 metric_key 相同的同一项目",
                "fact_ids": ids,
            }
    elif operation not in {"difference", "yoy_bridge"} and not _same_interval(selected):
        return {
            "status": "not_calculated",
            "reason": "此公式要求所有事实属于同一年度、同一报告区间和同一种期间/时点类型",
            "fact_ids": ids,
        }
    elif operation in {"cash_bridge", "profit_to_cash", "operating_after_capex", "net_by_direction"} and any(
        kind != "period_amount" for kind in kinds
    ):
        return {
            "status": "not_calculated",
            "reason": "该公式只适用于期间流量，不能将时点余额混入",
            "fact_ids": ids,
        }

    metric_roles: dict[str, list[set[str]]] = {}
    if operation == "cash_bridge":
        metric_roles[operation] = [
            {"op_net_cash_cfs", "cfs_op_net", "operating_net_cash", "ocf_net"},
            {"inv_net_cash_cfs", "net_cash_investing", "investing_net_cash", "icf_net"},
            {"fin_net_cash_cfs", "net_cash_financing", "financing_net_cash"},
            {"fx_effect_cfs", "fx_effect_on_cash", "cash_fx_effect", "fx_effect", "fx_effect_cash"},
            {"net_increase_cfs", "net_increase_in_cash", "cash_net_increase", "net_increase_cash"},
        ]
    elif operation == "cash_balance_bridge":
        metric_roles[operation] = [
            {"end_cash_cfs", "ending_cash_balance", "cash_equivalents_ending", "ending_cash_equiv", "cash_end", "cash_equiv_end"},
            {"begin_cash_cfs", "beginning_cash_balance", "cash_equivalents_beginning", "beginning_cash_equiv", "cash_begin", "cash_equiv_begin"},
            {"net_increase_cfs", "net_increase_in_cash", "cash_net_increase", "net_increase_cash"},
        ]
    elif operation == "profit_to_cash":
        metric_roles[operation] = [
            {"net_income", "net_profit", "net_profit_cfs", "recon_net_profit"},
        ] + [set() for _ in range(max(0, len(selected) - 2))] + [
            {"op_net_cash_cfs", "cfs_op_net", "operating_net_cash", "ocf_net"},
        ]
    elif operation == "operating_after_capex":
        metric_roles[operation] = [
            {"op_net_cash_cfs", "cfs_op_net", "operating_net_cash", "ocf_net"},
            {
                "capex_paid", "capex_construction_payments", "long_term_asset_purchase_cash",
                "invest_capex", "capex_assets", "purchase_long_term_assets",
            },
        ]
    expected_roles = metric_roles.get(operation, [])
    if expected_roles and (
        len(selected) != len(expected_roles)
        or any(role and not _has_metric_role(fact, role) for fact, role in zip(selected, expected_roles))
    ):
        return {
            "status": "not_calculated",
            "reason": "输入项目与公式所需的财务角色不匹配，请核对科目编号及排列顺序",
            "fact_ids": ids,
        }
    values = [Decimal(str(fact["normalized_value"])) for fact in selected]
    name = str(request.get("name", operation))[:120]
    formula = ""
    details: dict[str, Any] = {}

    try:
        if operation == "sum":
            result = sum(values, Decimal(0))
            formula = "各已选事实带符号金额相加"
        elif operation == "difference":
            if len(values) != 2:
                raise ValueError("差额计算需要且只需要两条事实")
            result = values[0] - values[1]
            formula = "第一条金额-第二条金额"
        elif operation == "ratio":
            if len(values) != 2:
                raise ValueError("比率计算需要且只需要两条事实")
            if values[1] <= 0:
                return {
                    "status": "not_calculated",
                    "reason": "分母为零或负数，不计算常规比率；展示金额关系更合适",
                    "fact_ids": ids,
                }
            result = values[0] / values[1]
            formula = "第一条金额/第二条金额"
        elif operation == "yoy":
            if len(values) != 2:
                raise ValueError("同比计算需要本期和上期两条事实")
            change = values[0] - values[1]
            if values[1] <= 0:
                return {
                    "status": "not_calculated",
                    "reason": "上期基数为零或负数，保留金额变化，不计算常规同比",
                    "change": str(change),
                    "unit": selected[0]["normalized_unit"],
                    "fact_ids": ids,
                }
            result = change / values[1]
            formula = "(本期金额-上期金额)/上期金额"
            details["absolute_change"] = str(change)
        elif operation == "yoy_bridge":
            component_pairs = list(zip(selected[:-2:2], selected[1:-2:2]))
            target_current, target_prior = selected[-2:]
            component_rows = []
            component_change = Decimal(0)
            for current, prior in component_pairs:
                current_value = Decimal(str(current["normalized_value"]))
                prior_value = Decimal(str(prior["normalized_value"]))
                change = current_value - prior_value
                component_change += change
                component_rows.append({
                    "metric_key": str(current.get("metric_key", "")),
                    "label": str(current.get("original_label", "")),
                    "current_value": str(current_value),
                    "prior_value": str(prior_value),
                    "change": str(change),
                    "fact_ids": [current["fact_id"], prior["fact_id"]],
                })
            target_change = Decimal(str(target_current["normalized_value"])) - Decimal(str(target_prior["normalized_value"]))
            reconciliation_difference = target_change - component_change
            formula = "目标总额同比变化；同时核对已选分项同比变化合计"
            details.update({
                "target_current": str(target_current["normalized_value"]),
                "target_prior": str(target_prior["normalized_value"]),
                "target_change": str(target_change),
                "component_change_sum": str(component_change),
                "reconciliation_difference": str(reconciliation_difference),
                "reconciles": reconciliation_difference == 0,
                "components": component_rows,
                "instruction": "只有 reconciles 为 true 时，才可把这些分项作为目标总额同比变化的完整归因；否则只能列为部分贡献。",
            })
            result = target_change
        elif operation == "net_by_direction":
            inflow = sum((Decimal(facts[value]["normalized_value"]) for value in inflow_ids), Decimal(0))
            outflow = sum((abs(Decimal(facts[value]["normalized_value"])) for value in outflow_ids), Decimal(0))
            result = inflow - outflow
            formula = "已选流入金额合计-已选流出金额绝对值合计"
            details.update({"inflow_total": str(inflow), "outflow_total": str(outflow)})
        elif operation == "cash_bridge":
            if len(values) != 5:
                raise ValueError("现金变动核对需依次选择经营、投资、筹资、汇率影响和披露的净增加")
            computed = sum(values[:4], Decimal(0))
            result = values[4] - computed
            formula = "披露现金净增加-(经营净额+投资净额+筹资净额+汇率影响)"
            details.update({"computed_net_change": str(computed), "reported_net_change": str(values[4])})
        elif operation == "cash_balance_bridge":
            if len(values) != 3:
                raise ValueError("现金余额核对按期末现金、期初现金、披露净增加排列")
            balance_change = values[0] - values[1]
            result = values[2] - balance_change
            formula = "披露现金及现金等价物净增加-(期末现金及现金等价物-期初现金及现金等价物)"
            details.update({"cash_balance_change": str(balance_change), "reported_net_change": str(values[2])})
        elif operation == "profit_to_cash":
            if len(values) < 2:
                raise ValueError("利润调节至少需要净利润和披露的经营现金净额")
            if len(values) < 3:
                return {
                    "status": "not_calculated",
                    "reason": "调节表中除净利润外没有可核对的调节项目，不能判断这是否为完整调节",
                    "fact_ids": ids,
                }
            computed = sum(values[:-1], Decimal(0))
            result = values[-1] - computed
            formula = "披露经营现金净额-(对应净利润+已选带符号调节项目合计)"
            details.update({"computed_operating_cash": str(computed), "reported_operating_cash": str(values[-1])})
        elif operation == "operating_after_capex":
            if len(values) != 2:
                raise ValueError("经营现金扣长期资产购建支出需两条事实")
            result = values[0] - abs(values[1])
            formula = "经营活动现金流量净额-购建固定资产等长期资产支付现金的绝对值"
            details["metric_definition"] = "模块四观察指标，不等同于 FCFF、FCFE 或可分配现金"
        else:
            raise ValueError(f"不支持的现金流计算：{operation}")
    except (DivisionByZero, InvalidOperation, ValueError) as exc:
        raise ValueError("数值运算失败，输入可能超出可计算范围") from exc

    return {
        "status": "calculated",
        "name": name,
        "operation": operation,
        "formula": formula,
        "value": str(result),
        "unit": "ratio" if operation in {"ratio", "yoy"} else selected[0]["normalized_unit"],
        "fact_ids": ids,
        "details": details,
        "rule_version": "cashflow-calculations-v2",
    }
