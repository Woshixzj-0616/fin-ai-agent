"""确定性主张比较和跨公司比较。"""
from __future__ import annotations

from decimal import Decimal

from audit.grounding import resolve_company
from audit.schema import METRICS
from audit.tools.compute import _norm_value, _one_fact
from audit.tools.evidence import _err, find_evidence
from finance import check_claim, decimal, text


def compare_claim(facts: list[dict], *, company_name_or_code: str, metric: str,
                  period_year: int, kind: str, claimed_value: str, claimed_unit: str,
                  operator: str, tolerance_pct=None, direction: str = "unknown",
                  scope: str = "consolidated", source_report_year: int | None = None,
                  draft: str = "") -> dict:
    """唯一裁决入口：内部走 finance.check_claim，verdict 不由模型改写。"""
    located = find_evidence(facts, company_name_or_code=company_name_or_code, metric=metric,
                            period_year=int(period_year),
                            source_report_year=source_report_year, draft=draft)
    if located.get("status") != "ok":
        return {"verdict": "needs_review", "reason_code": "missing_evidence",
                "evidence_ids": [], "detail": located}
    code = located["items"][0]["company_code"]
    chosen_report_year = located["items"][0].get("report_year") or int(source_report_year or period_year)
    value = claimed_value
    if kind == "yoy" and direction == "down" and decimal(claimed_value) and decimal(claimed_value) > 0:
        value = "-" + str(decimal(claimed_value))
    claim = {
        "id": "tool", "sentence": "", "company_code": code,
        "period_year": int(period_year),
        "source_report_year": int(source_report_year or chosen_report_year),
        "metric": metric, "kind": kind,
        "value": value, "unit": claimed_unit,
        "currency": "CNY", "scope": scope, "period_kind": "annual",
        "operator": operator, "tolerance_pct": tolerance_pct,
    }
    checked = check_claim(claim, facts)
    status = checked.get("status")
    verdict = {
        "证据支持": "evidence_supported",
        "确认错误": "confirmed_error",
    }.get(status, "needs_review")
    return {
        "verdict": verdict, "status": status,
        "reason_code": "deterministic_check" if verdict != "needs_review" else checked.get("reason"),
        "reason": checked.get("reason"),
        "evidence_ids": checked.get("evidence_ids") or [],
        "calculation": checked.get("calculation"),
        "expected": (checked.get("calculation") or {}).get("value")
        if status == "确认错误" else None,
    }


def compare_companies(facts: list[dict], *, company_a: str, company_b: str,
                      metric: str, period_year: int, operator: str,
                      draft: str = "") -> dict:
    """跨公司同指标比较：A operator B，返回确定性裁决 + 双侧 evidence_id。"""
    ops = {"exceed": (">", lambda a, b: a > b),
           "at_least": (">=", lambda a, b: a >= b),
           "at_most": ("<=", lambda a, b: a <= b),
           "below": ("<", lambda a, b: a < b),
           "eq": ("≈", lambda a, b: abs(a - b) <= abs(b) * Decimal("0.02") if b else a == b)}
    if operator not in ops:
        return _err(f"不支持的比较运算符：{operator}")
    if metric not in METRICS:
        return _err("指标不在目录", metric=metric)
    code_a = resolve_company(company_a, company_a, draft or company_a, facts)
    code_b = resolve_company(company_b, company_b, draft or company_b, facts)
    if not code_a or not code_b:
        return {"verdict": "needs_review", "reason_code": "unresolved_company",
                "reason": "公司无法对应到本次已加载材料", "evidence_ids": []}
    if code_a == code_b:
        return _err("比较双方是同一公司")
    fact_a = _one_fact(facts, code_a, metric, int(period_year))
    fact_b = _one_fact(facts, code_b, metric, int(period_year))
    if not fact_a or not fact_b:
        missing = code_a if not fact_a else code_b
        return {"verdict": "needs_review", "reason_code": "missing_evidence",
                "reason": f"缺少 {missing} {period_year} 年「{METRICS[metric][0]}」的唯一年报证据",
                "evidence_ids": [f["evidence_id"] for f in (fact_a, fact_b) if f]}
    va, vb = _norm_value(fact_a), _norm_value(fact_b)
    if va is None or vb is None:
        return {"verdict": "needs_review", "reason_code": "value_missing",
                "reason": "证据数值缺失", "evidence_ids": [fact_a["evidence_id"], fact_b["evidence_id"]]}
    sym, fn = ops[operator]
    held = fn(va, vb)
    verdict = "evidence_supported" if held else "confirmed_error"
    name_a = fact_a.get("company_name") or code_a
    name_b = fact_b.get("company_name") or code_b
    name_m = METRICS[metric][0]
    return {
        "verdict": verdict, "operator": operator,
        "reason_code": "cross_company_comparison",
        "reason": f"{name_a} {period_year}年{name_m}={text(va)}，{name_b}={text(vb)}，"
                  f"判断 {name_a}{sym}{name_b} {'成立' if held else '不成立'}",
        "evidence_ids": [fact_a["evidence_id"], fact_b["evidence_id"]],
        "values": [{"company_code": code_a, "value": text(va), "evidence_id": fact_a["evidence_id"]},
                   {"company_code": code_b, "value": text(vb), "evidence_id": fact_b["evidence_id"]}],
    }
