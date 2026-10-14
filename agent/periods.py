"""报告身份、期间可比性和累计拆季；输入与计算值均可追溯。"""
from __future__ import annotations

import calendar
import re
from datetime import date

from finance import (BASIS_KEYS, NON_AMOUNT_UNITS, convert, decimal, declared_basis_conflicts,
                     declared_basis_states, evidence_issues, result, text, yoy)

STOCK_METRICS = frozenset({"total_assets", "parent_equity", "book_value_per_share",
                           "accounts_receivable", "receivables", "inventory", "total_liabilities",
                           "cash_and_equivalents", "accounts_payable", "contract_assets"})
ADDITIVE_METRICS = frozenset({"revenue", "total_revenue", "parent_net_profit",
                             "adjusted_parent_net_profit", "operating_cash_flow", "revenue_after_deduction"})
NON_ADDITIVE = STOCK_METRICS | frozenset({
    "basic_eps", "diluted_eps", "deducted_basic_eps", "weighted_roe", "deducted_weighted_roe"})


def report_period(content: str, year: int | None = None) -> dict:
    compact = re.sub(r"\s+", "", content)
    specs = [("half", 6, r"半年度报告|半年度報告|半年报"),
             ("quarter", 3, r"第一季度报告|一季度报告|第1季度报告"),
             ("quarter", 6, r"第二季度报告|二季度报告|第2季度报告"),
             ("quarter", 9, r"第三季度报告|三季度报告|第3季度报告"),
             ("quarter", 12, r"第四季度报告|四季度报告|第4季度报告"),
             ("annual", 12, r"年度报告|年度報告")]
    for kind, month, pattern in specs:
        match = re.search(r"((?:19|20)\d{2})年?(?:" + pattern + ")", compact)
        if match and (year is None or int(match[1]) == year):
            y = int(match[1])
            return {"report_kind": kind, "report_year": y, "report_month": month,
                    "report_period_end": end_date(y, month), "identity_basis": match[0]}
    return {"report_kind": "unknown", "report_year": year, "report_month": None,
            "report_period_end": None, "identity_basis": None}


def end_date(year: int, month: int) -> str:
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()



def period_bounds(fact: dict, *, allow_legacy_annual: bool = False):
    """读取明确起止；兼容旧年度表中明确的年度 + 12个月，不推断半年/单季。"""
    start, end = fact.get("period_start"), fact.get("period_end")
    if (allow_legacy_annual and not start and not end and fact.get("period_kind") == "annual"
            and fact.get("duration_months") == 12 and fact.get("period_year") is not None):
        year = int(fact["period_year"])
        start, end = f"{year}-01-01", f"{year}-12-31"
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            return None
        return first.isoformat(), last.isoformat()
    except (ValueError, TypeError):
        return None

def dimension_reasons(a: dict, b: dict) -> list[str]:
    reasons = []
    for key in ("company_code", "metric", "currency", "scope"):
        if key == "scope" and a.get("unit") in NON_AMOUNT_UNITS and b.get("unit") in NON_AMOUNT_UNITS:
            continue
        if a.get(key) in {None, "", "unknown"} or b.get(key) in {None, "", "unknown"}:
            reasons.append(f"{key} 未明确")
        elif a[key] != b[key]:
            reasons.append(f"{key} 不一致")
    if evidence_issues(a) or evidence_issues(b):
        reasons.append("存在待复核字段")
    if a.get("adjustment") == "before" or b.get("adjustment") == "before":
        reasons.append("调整前数据不能直接参与比较")
    return reasons


def dimensions(a: dict, b: dict) -> list[str]:
    """兼容旧接口：维度问题 + 已声明的依据冲突。"""
    return [*dimension_reasons(a, b), *declared_basis_conflicts(a, b)]


def same_disclosure(a: dict, b: dict) -> bool:
    """同一份材料的同一个比较表：属于披露表内部算术，不要求跨报告依据。"""
    group = a.get("comparison_group")
    if not group or group != b.get("comparison_group"):
        return False
    # 分组标签不是材料身份；两侧声明身份时还必须属于同一份材料。
    for key in ("document_id", "source_sha256", "source_file"):
        if a.get(key) and b.get(key) and a[key] != b[key]:
            return False
    return True


def comparability(a: dict, b: dict) -> dict:
    """跨报告可比性三态：satisfied / conflict / unknown。

    - satisfied：同一披露比较表内部算术，或政策、合并边界、重述版本都已声明且一致；
    - conflict：已声明的依据明确冲突；
    - unknown：跨报告且缺少依据声明——未知不等于一致，派生结果不得当成功。
    """
    reasons = dimension_reasons(a, b)
    internal = same_disclosure(a, b)
    states = declared_basis_states(a, b)
    if internal:
        # 同一披露比较表的内部算术不要求跨报告依据；已声明的明确冲突仍然生效。
        states = {key: ("conflict" if state == "conflict" else "satisfied")
                  for key, state in states.items()}
    conflicts = [key for key, state in states.items() if state == "conflict"]
    gaps = [key for key, state in states.items() if state == "unknown"]
    status = "conflict" if conflicts else ("unknown" if gaps else "satisfied")
    reasons.extend(f"{key} 不一致" for key in conflicts)
    return {"status": status, "internal": internal, "dimensions": states,
            "conflicts": conflicts, "gaps": gaps, "reasons": reasons}


def amount(fact: dict):
    return (decimal(fact.get("value")) if fact.get("unit") in NON_AMOUNT_UNITS
            else convert(fact.get("value"), fact.get("unit")))


def derive_quarter(current: dict, previous_cumulative: dict | None = None) -> dict:
    """仅流量指标可拆差；EPS/ROE/资产余额不可相减后冒充单季。"""
    if (current.get("metric") in NON_ADDITIVE or current.get("period_attribute") == "instant"
            or current.get("period_kind") == "instant"):
        return result("not_comparable", reason="该指标为存量或非可加指标，不能累计拆季")
    if current.get("metric") not in ADDITIVE_METRICS:
        return result("not_comparable", reason="该指标没有已登记的累计可加定义，不能推导单季")
    try:
        start, end = date.fromisoformat(current["period_start"]), date.fromisoformat(current["period_end"])
    except (KeyError, ValueError, TypeError):
        return result("not_comparable", reason="期间起止未明确")
    if start.month != 1 or start.day != 1 or end.month not in {3, 6, 9, 12} or end.isoformat() != end_date(end.year, end.month):
        return result("not_comparable", reason="需要年初至季度末累计期间")
    if end.month == 3:
        reasons = dimensions(current, current)
        if reasons:
            return result("not_comparable", reasons=reasons, comparability=comparability(current, current))
        if amount(current) is None:
            return result("missing", reason="一季度累计值缺失")
        return result("ok", amount(current), unit="元", period_start=start.isoformat(),
                      period_end=end.isoformat(), evidence_ids=[current["evidence_id"]],
                      formula="Q1 = year_to_date_Q1")
    if previous_cumulative is None:
        return result("missing", reason="缺少上一季度累计数，无法得到单季值")
    a, b = amount(current), amount(previous_cumulative)
    if a is None or b is None:
        return result("missing", reason="累计数缺失")
    compare = comparability(current, previous_cumulative)
    reasons = list(compare["reasons"])
    if (previous_cumulative.get("period_start") != start.isoformat() or
            previous_cumulative.get("period_end") != end_date(end.year, end.month - 3)):
        reasons.append("累计期间不相邻或跨年度")
    operands = {"current_ytd": text(a), "previous_ytd": text(b)}
    evidence_ids = [current["evidence_id"], previous_cumulative["evidence_id"]]
    if reasons:
        return result("not_comparable", reasons=reasons, comparability=compare,
                      operands=operands, evidence_ids=evidence_ids,
                      period_start=date(end.year, end.month - 2, 1).isoformat(),
                      period_end=end.isoformat(),
                      formula="quarter = current_ytd - previous_ytd")
    if compare["status"] == "unknown":
        # 跨报告派生缺少政策、合并边界或调整依据：保留操作数和被阻断的尝试，但不给出单季值。
        return result("not_comparable", reason="跨报告可比性依据未知，未按同一口径处理",
                      reasons=[f"{key} 未声明" for key in compare["gaps"]],
                      comparability=compare, operands=operands, evidence_ids=evidence_ids,
                      period_start=date(end.year, end.month - 2, 1).isoformat(),
                      period_end=end.isoformat(),
                      attempted_value=text(a - b), formula="quarter = current_ytd - previous_ytd")
    return result("ok", a - b, unit="元", formula="quarter = current_ytd - previous_ytd",
                  period_start=date(end.year, end.month - 2, 1).isoformat(), period_end=end.isoformat(),
                  operands=operands, evidence_ids=evidence_ids, comparability=compare)


def growth(current: dict, previous: dict | None, kind: str = "yoy") -> dict:
    if kind not in {"yoy", "qoq", "year_end"}:
        return result("not_comparable", reason="增长类型必须为 yoy、qoq 或 year_end")
    if previous is None:
        return result("missing", reason="缺少唯一可比期间数据")
    compare = comparability(current, previous)
    reasons = list(compare["reasons"])
    try:
        a0, a1 = (date.fromisoformat(current[k]) for k in ("period_start", "period_end"))
        b0, b1 = (date.fromisoformat(previous[k]) for k in ("period_start", "period_end"))
        if current.get("metric") in STOCK_METRICS:
            if kind == "qoq":
                reasons.append("时点余额不按单季流量环比计算")
            elif kind == "year_end":
                if a0 != a1 or b0 != b1 or b1 != date(a1.year - 1, 12, 31):
                    reasons.append("较上年末变化必须使用上年12月31日余额")
            elif (a1.year - b1.year, a1.month, a1.day) != (1, b1.month, b1.day):
                reasons.append("余额同比需为上年相同日期")
        elif kind == "year_end":
            reasons.append("较上年末变化只适用于时点余额")
        elif kind == "yoy":
            if (a0.year - b0.year, a1.year - b1.year, a0.month, a0.day, a1.month, a1.day) != (
                    1, 1, b0.month, b0.day, b1.month, b1.day):
                reasons.append("同比要求上年相同期间")
        else:
            if (a0.toordinal() != b1.toordinal() + 1 or
                    current.get("duration_months") != 3 or previous.get("duration_months") != 3):
                reasons.append("环比需要相邻的完整单季，不能比较累计数")
    except (KeyError, ValueError, TypeError):
        reasons.append("期间起止未明确")
    if reasons:
        return result("not_comparable", reasons=reasons, comparability=compare)
    if compare["status"] == "unknown":
        return result("not_comparable", reason="跨报告可比性依据未知，未按同一口径处理",
                      reasons=[f"{key} 未声明" for key in compare["gaps"]],
                      comparability=compare,
                      evidence_ids=[current.get("evidence_id"), previous.get("evidence_id")])
    try:
        out = yoy(amount(current), amount(previous))
    except ValueError as exc:
        return result("not_comparable", reasons=[str(exc)])
    out.update(growth_kind=kind, evidence_ids=[current["evidence_id"], previous["evidence_id"]],
               comparability=compare)
    return out
