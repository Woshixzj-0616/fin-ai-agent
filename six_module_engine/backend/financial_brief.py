"""Small deterministic financial brief for the report editor.

Only module values already marked usable for calculation enter the brief. The
brief does not turn missing business extraction into a company disclosure claim.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


def _number(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def _yi(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return str((value / Decimal("100000000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _pages(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return sorted({int(item) for item in value if str(item).isdigit() and int(item) > 0})


def _evidence_pages(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return _pages([item.get("page") for item in value if isinstance(item, dict)])


def _result(report: dict[str, Any], module_id: str) -> dict[str, Any]:
    for task in report.get("modules", []):
        if task.get("module_id") == module_id and task.get("status") == "completed":
            envelope = task.get("result") or {}
            return envelope.get("result") or {}
    return {}


def _record(name: str, current: Decimal, previous: Decimal | None, module: str,
            pages: list[int], *, note: str = "") -> dict[str, Any]:
    difference = current - previous if previous is not None else None
    growth = difference / previous * 100 if previous is not None and previous > 0 else None
    return {
        "metric": name, "current_yuan": str(current),
        "previous_yuan": str(previous) if previous is not None else None,
        "current_yi": _yi(current), "previous_yi": _yi(previous),
        "change_yi": _yi(difference),
        "growth_pct": str(growth.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if growth is not None else None,
        "source_module": module, "source_pages": pages, "note": note,
    }


def _asset_amount(fact: dict[str, Any]) -> Decimal | None:
    if fact.get("calculation_ready") is not True:
        return None
    if "合并" not in str(fact.get("reporting_scope") or ""):
        return None
    value = _number(fact.get("value"))
    factor = {"元": 1, "千元": 1000, "万元": 10000, "亿元": 100000000}.get(str(fact.get("unit") or "").strip())
    return value * factor if value is not None and factor else None


def _business_amount(value: Any, unit: Any) -> Decimal | None:
    number = _number(value)
    factor = {"元": 1, "千元": 1000, "万元": 10000, "亿元": 100000000}.get(str(unit or "").strip())
    return number * factor if number is not None and factor else None


def build_financial_brief(report: dict[str, Any]) -> dict[str, Any]:
    year_match = re.search(r"20\d{2}", str(report.get("report_year") or ""))
    year = int(year_match.group()) if year_match else None
    metrics: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}

    profit = _result(report, "profit")
    wanted_profit = {
        "operating_revenue": "营业收入", "operating_cost": "营业成本",
        "operating_profit": "营业利润", "net_profit": "净利润",
        "parent_profit": "归母净利润", "research_development_expense": "研发费用",
    }
    for role, name in wanted_profit.items():
        row = next((item for item in profit.get("profit_lines", [])
                    if item.get("profit_role") == role and item.get("value_status") == "可计算"
                    and _number(item.get("current_yuan")) is not None), None)
        if row:
            metric = _record(name, _number(row["current_yuan"]), _number(row.get("previous_yuan")),
                             "盈利分析", _pages(row.get("source_pages")))
            metrics.append(metric)
            by_name[name] = metric

    cashflow = _result(report, "cashflow")
    wanted_cash = {
        "operating_net_cash": "经营活动现金流量净额", "capex_assets": "购建长期资产支付的现金",
        "recon_oper_receivable": "经营性应收变动的现金影响",
        "recon_inventory": "存货变动的现金影响",
        "recon_oper_payable": "经营性应付变动的现金影响",
    }
    for key, name in wanted_cash.items():
        rows = [item for item in cashflow.get("facts", []) if item.get("metric_key") == key
                and item.get("calculable") is True and "合并" in str(item.get("scope") or "")
                and item.get("normalized_unit") == "CNY_yuan"]
        current = next((item for item in rows if year and str(item.get("period") or "").startswith(str(year))), None)
        previous = next((item for item in rows if year and str(item.get("period") or "").startswith(str(year - 1))), None)
        if current and _number(current.get("normalized_value")) is not None:
            note = "现金流模块可计算；原文行列语义仍需复核" if current.get("identity_status") not in ("confirmed", "verified") else ""
            metric = _record(name, _number(current["normalized_value"]),
                             _number(previous.get("normalized_value")) if previous else None,
                             "现金流", _evidence_pages(current.get("evidence")), note=note)
            metrics.append(metric)
            by_name[name] = metric

    assets = _result(report, "assets")
    wanted_assets = {
        "total_assets": ("资产总计", "资产总计"),
        "accounts_receivable": ("应收账款", "应收账款"),
        "inventory": ("存货", "存货"),
        "cash_and_equivalents": ("货币资金", "货币资金"),
    }
    for key, (label, name) in wanted_assets.items():
        rows = [item for item in assets.get("facts", []) if item.get("metric_key") == key
                and item.get("original_label") == label and _asset_amount(item) is not None]
        current = next((item for item in rows if year and str(item.get("period_end") or item.get("as_of_date") or "").startswith(str(year))), None)
        previous = next((item for item in rows if year and str(item.get("period_end") or item.get("as_of_date") or "").startswith(str(year - 1))), None)
        if current:
            metric = _record(name, _asset_amount(current), _asset_amount(previous) if previous else None,
                             "资产质量", _evidence_pages(current.get("evidence")))
            metrics.append(metric)
            by_name[name] = metric

    bridges: dict[str, Any] = {}
    raw_profit_bridge = profit.get("profit_bridge") or {}
    target = _number(raw_profit_bridge.get("target_change_yuan"))
    residual = _number(raw_profit_bridge.get("residual_yuan"))
    contributions = []
    for item in raw_profit_bridge.get("contributions", []):
        if item.get("included_in_total") is not True:
            continue
        effect = _number(item.get("profit_effect_yuan"))
        if effect is not None:
            contributions.append({"name": str(item.get("label") or ""), "effect_yi": _yi(effect),
                                  "source_pages": _pages(item.get("source_pages"))})
    if target is not None and residual is not None and contributions:
        bridges["profit"] = {
            "target_change_yi": _yi(target), "residual_yi": _yi(residual),
            "contributions": contributions,
            "note": "会计项目贡献，不等同经营驱动；未分类差额保留，不强行平账。",
        }

    cfo = by_name.get("经营活动现金流量净额")
    net_profit = by_name.get("净利润")
    if cfo and net_profit:
        gap = _number(cfo["current_yuan"]) - _number(net_profit["current_yuan"])
        bridges["cash"] = {
            "net_profit_yi": net_profit["current_yi"], "operating_cash_yi": cfo["current_yi"],
            "difference_yi": _yi(gap),
            "working_capital": [by_name[name] for name in (
                "经营性应收变动的现金影响", "存货变动的现金影响", "经营性应付变动的现金影响")
                if name in by_name],
            "note": "营运资金分项只解释部分差额，不能代替完整现金流补充资料勾稽。",
        }

    ar, inventory, total_assets = (by_name.get(name) for name in ("应收账款", "存货", "资产总计"))
    if ar and inventory and total_assets:
        occupied = _number(ar["current_yuan"]) + _number(inventory["current_yuan"])
        total = _number(total_assets["current_yuan"])
        bridges["asset_occupation"] = {
            "receivables_yi": ar["current_yi"], "inventory_yi": inventory["current_yi"],
            "combined_yi": _yi(occupied),
            "share_assets_pct": str((occupied / total * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if total and total > 0 else None,
            "source_pages": sorted(set(ar["source_pages"] + inventory["source_pages"] + total_assets["source_pages"])),
        }

    business = _result(report, "business")
    business_mix: list[dict[str, Any]] = []
    total = business.get("revenue_total") or {}
    total_amount = _business_amount(total.get("current_value"), total.get("current_unit"))
    total_previous = _business_amount(total.get("previous_value"), total.get("previous_unit"))
    for row in business.get("revenue_segments", []):
        if not isinstance(row, dict) or row.get("quote_verified") is not True:
            continue
        if year and not str(row.get("current_period") or "").startswith(str(year)):
            continue
        amount = _business_amount(row.get("current_value"), row.get("current_unit"))
        if amount is None or amount <= 0:
            continue
        same_denominator = (
            str(row.get("metric_name") or "").strip() == str(total.get("metric_name") or "").strip()
            and str(row.get("reporting_scope") or "").strip() == str(total.get("reporting_scope") or "").strip()
        )
        share = (amount / total_amount * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if same_denominator and total_amount and total_amount > 0 else None
        previous = _business_amount(row.get("previous_value"), row.get("previous_unit"))
        comparable = bool(
            previous is not None and row.get("cross_period_comparable") is True and year
            and str(row.get("previous_period") or "").startswith(str(year - 1))
        )
        change = amount - previous if comparable else None
        total_change = total_amount - total_previous if total_amount is not None and total_previous is not None else None
        contribution = (
            (change / total_change * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if change is not None and same_denominator and total_change is not None and total_change > 0 else None
        )
        business_mix.append({
            "basis": str(row.get("basis") or ""), "name": str(row.get("name") or ""),
            "amount_yi": _yi(amount), "share_pct": str(share) if share is not None else None,
            "previous_yi": _yi(previous) if comparable else "—", "change_yi": _yi(change),
            "growth_contribution_pct": str(contribution) if contribution is not None else None,
            "source_pages": _pages(row.get("source_pages")),
        })
    business_mix = sorted(business_mix, key=lambda item: Decimal(item["amount_yi"]), reverse=True)[:10]

    return {"year": year, "scope": "合并", "unit": "亿元人民币", "metrics": metrics,
            "bridges": bridges, "business_mix": business_mix,
            "method": "只使用模块标为可计算的同年合并数据；金额由原始元数确定性换算。不同收入拆分维度不可相加。"}


def render_financial_brief(brief: dict[str, Any]) -> str:
    metrics = brief.get("metrics") or []
    business_mix = brief.get("business_mix") or []
    if not metrics and not business_mix:
        return ""
    lines = ["## 财务主线计算底表", "", "下表使用六模块已标为可计算的合并口径数据；空格表示缺少可比数值。来源页可在年报中回查。"]
    if metrics:
        lines.extend(["", "| 指标（亿元） | 本期 | 上期 | 变化 | 来源页 |", "| --- | ---: | ---: | ---: | --- |"])
        for item in metrics:
            pages = "、".join(str(page) for page in item["source_pages"]) or "待补"
            lines.append(f"| {item['metric']} | {item['current_yi']} | {item['previous_yi']} | {item['change_yi']} | {pages} |")
        if any(item.get("note") for item in metrics):
            lines.extend(["", "现金流模块虽将部分数值标为可计算，其原文行列语义仍可能需要复核；相关判断以原文回查为准。"])
    if business_mix:
        lines.extend(["", "**已核对的收入构成。** 产品、地区等属于不同拆分维度，不能跨维度相加。", "",
                      "| 维度 | 分项 | 本期收入（亿元） | 上期 | 增量 | 同口径占比 | 来源页 |", "| --- | --- | ---: | ---: | ---: | ---: | --- |"])
        for item in business_mix:
            pages = "、".join(str(page) for page in item["source_pages"]) or "待补"
            share = item["share_pct"] + "%" if item["share_pct"] is not None else "—"
            lines.append(f"| {item['basis']} | {item['name']} | {item['amount_yi']} | {item['previous_yi']} | {item['change_yi']} | {share} | {pages} |")
    bridges = brief.get("bridges") or {}
    profit = bridges.get("profit")
    if profit:
        lines.extend(["", f"**利润变化桥。** 归母净利润变动 {profit['target_change_yi']} 亿元；"
                      f"已识别项目与目标之间仍有 {profit['residual_yi']} 亿元未分类差额。"
                      "各项目是会计贡献，不直接等于业务驱动。"])
    cash = bridges.get("cash")
    if cash:
        lines.extend(["", f"**利润到现金。** 净利润 {cash['net_profit_yi']} 亿元，"
                      f"经营活动现金流 {cash['operating_cash_yi']} 亿元，差额 {cash['difference_yi']} 亿元；"
                      "应收、存货、应付等调节项须结合现金流补充资料解释。"])
    occupation = bridges.get("asset_occupation")
    if occupation:
        lines.extend(["", f"**资产占用。** 应收账款 {occupation['receivables_yi']} 亿元与存货"
                      f" {occupation['inventory_yi']} 亿元合计 {occupation['combined_yi']} 亿元，"
                      f"占资产总计 {occupation['share_assets_pct']}%。"])
    return "\n".join(lines)


def audit_narrative(markdown: str, brief: dict[str, Any]) -> list[dict[str, str]]:
    """Catch high-impact contradictions against deterministic bridge values."""
    issues: list[dict[str, str]] = []
    occupation = (brief.get("bridges") or {}).get("asset_occupation") or {}
    expected_amount = _number(occupation.get("combined_yi"))
    expected_share = _number(occupation.get("share_assets_pct"))
    cash_known = any(item.get("metric") == "经营活动现金流量净额" for item in brief.get("metrics", []))
    for paragraph in re.split(r"\n\s*\n", markdown):
        if "亿元千元" in paragraph or "千元亿元" in paragraph:
            issues.append({"kind": "unit", "message": "同一金额混用亿元和千元", "paragraph": paragraph})
        if expected_amount is not None and "应收账款" in paragraph and "存货" in paragraph and "合计" in paragraph:
            amount_match = re.search(r"合计(?:占款)?(?:约)?\s*(\d+(?:\.\d+)?)\s*亿元", paragraph)
            if amount_match and abs(Decimal(amount_match.group(1)) - expected_amount) > Decimal("0.10"):
                issues.append({"kind": "asset_sum", "message": f"应收与存货合计应为 {expected_amount} 亿元", "paragraph": paragraph})
            share_match = re.search(r"占总资产(?:约)?\s*(\d+(?:\.\d+)?)\s*%", paragraph)
            if share_match and expected_share is not None and abs(Decimal(share_match.group(1)) - expected_share) > Decimal("0.25"):
                issues.append({"kind": "asset_share", "message": f"应收与存货占总资产应为 {expected_share}%", "paragraph": paragraph})
        if cash_known and re.search(r"经营活动现金流[^。；\n]{0,40}(?:未取得|缺失|无法获取)", paragraph):
            issues.append({"kind": "cash_missing", "message": "经营活动现金流已在统一底表取得", "paragraph": paragraph})
    return issues


def quarantine_inconsistent_paragraphs(markdown: str, issues: list[dict[str, str]],
                                        brief: dict[str, Any]) -> str:
    """Keep an unresolved arithmetic error out of reader-facing prose."""
    bad = {item["paragraph"] for item in issues}
    occupation = (brief.get("bridges") or {}).get("asset_occupation") or {}
    output: list[str] = []
    for paragraph in re.split(r"\n\s*\n", markdown):
        if paragraph not in bad:
            output.append(paragraph)
        elif occupation and "应收账款" in paragraph and "存货" in paragraph and any(
            item["paragraph"] == paragraph and item["kind"] in {"asset_sum", "asset_share", "unit"}
            for item in issues
        ):
            output.append(
                f"应收账款与存货合计 {occupation['combined_yi']} 亿元，"
                f"占总资产 {occupation['share_assets_pct']}%。"
                "原段落的其他数值存在冲突，相关经营解释需回到年报和模块底稿复核。"
            )
        else:
            output.append("本段出现与已取得数据冲突的表述，暂缓展示，需回到底稿复核。")
    return "\n\n".join(output)
