"""确定性主张比较和跨公司比较。"""
from __future__ import annotations

from decimal import Decimal

from audit.grounding import resolve_company
from audit.schema import METRICS
from audit.tools.compute import _norm_value
from audit.tools.evidence import _err, find_evidence
from finance import NON_AMOUNT_UNITS, check_claim, decimal, evidence_issues, text
from periods import period_bounds

COMPANY_OPS = {"exceed": (">", lambda a, b: a > b),
               "at_least": (">=", lambda a, b: a >= b),
               "at_most": ("<=", lambda a, b: a <= b),
               "below": ("<", lambda a, b: a < b),
               "eq": ("≈", lambda a, b: abs(a - b) <= abs(b) * Decimal("0.02") if b else a == b)}


def _period_label(period_kind, period_year, period_end=None) -> str:
    kinds = {"annual": "年度", "half": "半年报", "quarter": "季度"}
    label = f"{period_year}年{kinds.get(period_kind or '', '')}"
    return f"{label}（截至 {period_end}）" if period_end else label


def compare_claim(facts: list[dict], *, company_name_or_code: str, metric: str,
                  period_year: int, kind: str, claimed_value: str, claimed_unit: str,
                  operator: str, tolerance_pct=None, direction: str = "unknown",
                  scope: str = "consolidated", source_report_year: int | None = None,
                  draft: str = "", report_kind: str | None = None,
                  period_start: str | None = None, period_end: str | None = None,
                  document_id: str | None = None) -> dict:
    """唯一裁决入口：内部走 finance.check_claim，verdict 不由模型改写。

    可选 report_kind/period_start/period_end/document_id 用于精确取证；旧调用不带这些
    参数时按公司+指标+期间年份取证，候选多义仍返回 ambiguous。
    """
    located = find_evidence(facts, company_name_or_code=company_name_or_code, metric=metric,
                            period_year=int(period_year),
                            source_report_year=source_report_year, draft=draft,
                            report_kind=report_kind, period_start=period_start,
                            period_end=period_end, document_id=document_id)
    if located.get("status") != "ok":
        return {"verdict": "needs_review", "reason_code": "missing_evidence",
                "reason": located.get("hint") or located.get("error"),
                "evidence_ids": located.get("evidence_ids") or [], "detail": located}
    located_fact = located["items"][0]
    index = {fact.get("evidence_id"): fact for fact in facts}
    fact = index.get(located_fact["evidence_id"], located_fact)
    code = located_fact["company_code"]
    chosen_report_year = located_fact.get("report_year") or int(source_report_year or period_year)
    value = claimed_value
    if kind == "yoy" and direction == "down" and decimal(claimed_value) and decimal(claimed_value) > 0:
        value = "-" + str(decimal(claimed_value))
    period_kind = report_kind or located_fact.get("period_kind") or "annual"
    claim = {
        "id": "tool", "sentence": "", "company_code": code,
        "period_year": int(period_year),
        "source_report_year": int(source_report_year or chosen_report_year),
        "metric": metric, "kind": kind,
        "value": value, "unit": claimed_unit,
        "currency": "CNY", "scope": scope, "period_kind": period_kind,
        "report_kind": period_kind,
        "period_start": period_start or located_fact.get("period_start"),
        "period_end": period_end or located_fact.get("period_end"),
        "document_id": document_id or located_fact.get("document_id"),
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
        "period": {"period_kind": period_kind, "period_start": claim["period_start"],
                   "period_end": claim["period_end"], "document_id": claim["document_id"],
                   "source_file": fact.get("source_file"), "page": fact.get("page")},
        "expected": (checked.get("calculation") or {}).get("value")
        if status == "确认错误" else None,
    }


def compare_companies(facts: list[dict], *, company_a: str, company_b: str,
                      metric: str, period_year: int, operator: str,
                      draft: str = "", report_kind: str | None = None,
                      period_start: str | None = None, period_end: str | None = None,
                      document_id_a: str | None = None,
                      document_id_b: str | None = None) -> dict:
    """跨公司同指标同期间比较：A operator B，返回确定性裁决 + 双侧 evidence_id。"""
    if operator not in COMPANY_OPS:
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
    index = {fact.get("evidence_id"): fact for fact in facts}
    sides = {}
    for code, document_id in ((code_a, document_id_a), (code_b, document_id_b)):
        sides[code] = find_evidence(facts, company_name_or_code=code, metric=metric,
                                    period_year=int(period_year), draft=draft,
                                    report_kind=report_kind, period_start=period_start,
                                    period_end=period_end, document_id=document_id)
    fact_a = index.get((sides[code_a].get("items") or [{}])[0].get("evidence_id")) if sides[code_a].get("status") == "ok" else None
    fact_b = index.get((sides[code_b].get("items") or [{}])[0].get("evidence_id")) if sides[code_b].get("status") == "ok" else None
    if not fact_a or not fact_b:
        missing_code = code_a if not fact_a else code_b
        missing = sides[missing_code]
        name = next((f.get("company_name") for f in facts if f.get("company_code") == missing_code),
                    missing_code)
        return {"verdict": "needs_review", "reason_code": "missing_evidence",
                "reason": f"缺少 {name} {_period_label(report_kind, period_year, period_end)}"
                          f"「{METRICS[metric][0]}」的唯一证据（{missing.get('status')}）",
                "evidence_ids": [f["evidence_id"] for f in (fact_a, fact_b) if f],
                "period": {"period_kind": report_kind, "period_start": period_start,
                           "period_end": period_end},
                "detail": {code: {k: v for k, v in sides[code].items() if k != "items"}
                           for code in (code_a, code_b)}}
    evidence_ids = [fact_a["evidence_id"], fact_b["evidence_id"]]
    bounds_a, bounds_b = (period_bounds(f, allow_legacy_annual=True) for f in (fact_a, fact_b))
    if (bounds_a is None or bounds_b is None or bounds_a != bounds_b
            or fact_a.get("period_kind") != fact_b.get("period_kind")
            or fact_a.get("duration_months") != fact_b.get("duration_months")
            or (fact_a.get("period_attribute") and fact_b.get("period_attribute")
                and fact_a["period_attribute"] != fact_b["period_attribute"])):
        return {"verdict": "needs_review", "reason_code": "period_mismatch",
                "reason": "两侧起止、期间长度或时点/流量属性未明确一致，不能直接比较",
                "evidence_ids": evidence_ids}
    reasons = []
    if evidence_issues(fact_a) or evidence_issues(fact_b):
        reasons.append("证据存在待复核字段")
    for key in ("currency", "scope"):
        if key == "scope" and fact_a.get("unit") in NON_AMOUNT_UNITS and fact_b.get("unit") in NON_AMOUNT_UNITS:
            continue
        if (fact_a.get(key) in {None, "", "unknown"} or fact_b.get(key) in {None, "", "unknown"}
                or fact_a[key] != fact_b[key]):
            reasons.append(f"{key} 未明确一致")
    non_amount_a, non_amount_b = fact_a.get("unit") in NON_AMOUNT_UNITS, fact_b.get("unit") in NON_AMOUNT_UNITS
    if non_amount_a != non_amount_b or (non_amount_a and fact_a.get("unit") != fact_b.get("unit")):
        reasons.append("单位维度不一致")
    if reasons:
        return {"verdict": "needs_review", "reason_code": "comparison_needs_review",
                "reason": "、".join(reasons), "evidence_ids": evidence_ids}
    va, vb = _norm_value(fact_a), _norm_value(fact_b)
    if va is None or vb is None:
        return {"verdict": "needs_review", "reason_code": "value_missing",
                "reason": "证据数值/单位不能安全归一化", "evidence_ids": evidence_ids}
    sym, fn = COMPANY_OPS[operator]
    held = fn(va, vb)
    verdict = "evidence_supported" if held else "confirmed_error"
    name_a = fact_a.get("company_name") or code_a
    name_b = fact_b.get("company_name") or code_b
    name_m = METRICS[metric][0]
    return {
        "verdict": verdict, "operator": operator,
        "reason_code": "cross_company_comparison",
        "reason": f"{name_a} {_period_label(fact_a.get('period_kind'), period_year, fact_a.get('period_end'))}"
                  f"{name_m}={text(va)}，{name_b}={text(vb)}，"
                  f"判断 {name_a}{sym}{name_b} {'成立' if held else '不成立'}",
        "evidence_ids": [fact_a["evidence_id"], fact_b["evidence_id"]],
        "period": {"period_kind": fact_a.get("period_kind"), "period_start": fact_a.get("period_start"),
                   "period_end": fact_a.get("period_end")},
        "values": [{"company_code": code_a, "value": text(va), "evidence_id": fact_a["evidence_id"],
                    "period_kind": fact_a.get("period_kind"), "period_start": fact_a.get("period_start"),
                    "period_end": fact_a.get("period_end"), "document_id": fact_a.get("document_id"), "source_file": fact_a.get("source_file"),
                    "page": fact_a.get("page")},
                   {"company_code": code_b, "value": text(vb), "evidence_id": fact_b["evidence_id"],
                    "period_kind": fact_b.get("period_kind"), "period_start": fact_b.get("period_start"),
                    "period_end": fact_b.get("period_end"), "document_id": fact_b.get("document_id"), "source_file": fact_b.get("source_file"),
                    "page": fact_b.get("page")}],
    }
