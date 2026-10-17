"""只读数值计算工具。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext

from audit.schema import METRICS
from audit.tools.evidence import _err, _pick_fact, find_evidence
from finance import convert, decimal, evidence_yoy, select_previous, text


def compute_yoy(facts: list[dict], *, company_name_or_code: str, metric: str, year: int,
                draft: str = "", report_kind: str | None = None,
                period_start: str | None = None, period_end: str | None = None,
                document_id: str | None = None) -> dict:
    located = find_evidence(facts, company_name_or_code=company_name_or_code, metric=metric,
                            period_year=int(year), draft=draft, report_kind=report_kind,
                            period_start=period_start, period_end=period_end,
                            document_id=document_id)
    if located.get("status") != "ok":
        return located
    fact = next(f for f in facts if f["evidence_id"] == located["items"][0]["evidence_id"])
    previous = select_previous(facts, fact)
    computation = evidence_yoy(fact, previous)
    return {"status": "ok" if computation.get("status") == "ok" else computation.get("status"),
            "yoy": computation, "current_evidence_id": fact["evidence_id"],
            "previous_evidence_id": (previous or {}).get("evidence_id"),
            "period": {"period_kind": fact.get("period_kind"), "period_start": fact.get("period_start"),
                       "period_end": fact.get("period_end"), "document_id": fact.get("document_id")}}


def _one_fact(facts: list[dict], code: str, metric: str, year: int,
              report_kind: str | None = None, document_id: str | None = None) -> dict | None:
    """取该公司该指标该年的证据；同值多条择优，冲突则 None。

    report_kind/document_id 只收窄候选，不改变"多条冲突不任取"的规则。
    """
    matches = [f for f in facts
               if f.get("company_code") == code and f.get("metric") == metric
               and f.get("period_year") == int(year) and f.get("adjustment") != "before"]
    if report_kind:
        narrowed = [f for f in matches if f.get("period_kind") == report_kind]
        matches = narrowed
    if document_id:
        narrowed = [f for f in matches if f.get("document_id") == document_id]
        matches = narrowed
    best, _ = _pick_fact(matches)
    return best


def _norm_value(fact: dict) -> Decimal | None:
    """归一化到元的数值；非金额单位直接取 value。"""
    from finance import NON_AMOUNT_UNITS
    v = decimal(fact.get("value"))
    if v is None:
        return None
    unit = fact.get("unit")
    if unit in NON_AMOUNT_UNITS:
        return v
    try:
        return convert(v, unit, "元")
    except (ValueError, ArithmeticError):
        return None


def compute_trend(facts: list[dict], *, company_name_or_code: str, metric: str,
                  start_year: int, end_year: int, draft: str = "",
                  report_kind: str | None = None, document_id: str | None = None) -> dict:
    """多年趋势：逐年同比 + CAGR + 单调性。覆盖「连续 N 年增长」「累计增长」类主张。"""
    from audit.grounding import resolve_company

    if metric not in METRICS:
        return _err("指标不在目录", metric=metric)
    code = resolve_company(company_name_or_code, company_name_or_code, draft or company_name_or_code, facts)
    if not code:
        return {"status": "error", "error": "公司无法对应到本次已加载材料"}
    y0, y1 = int(start_year), int(end_year)
    if y1 - y0 < 2:
        return _err("趋势至少要 3 个年度（start_year 到 end_year 跨 2 年以上）")
    series, selected = [], []
    for y in range(y0, y1 + 1):
        f = _one_fact(facts, code, metric, y, report_kind, document_id)
        if not f:
            return {"status": "error", "error": f"缺少 {y} 年「{METRICS[metric][0]}」的唯一指定期间/材料证据，无法算趋势"}
        v = _norm_value(f)
        if v is None:
            return {"status": "error", "error": f"{y} 年数值缺失"}
        selected.append(f)
        series.append({"year": y, "value": text(v), "evidence_id": f["evidence_id"],
                       "period_kind": f.get("period_kind"), "period_start": f.get("period_start"),
                       "period_end": f.get("period_end"), "document_id": f.get("document_id")})
    from periods import comparability, growth, period_bounds
    normalized = []
    for fact in selected:
        bounds = period_bounds(fact, allow_legacy_annual=True)
        normalized.append({**fact, **({"period_start": bounds[0], "period_end": bounds[1]} if bounds else {})})
    blocked, yoy_rows = [], []
    for i in range(1, len(series)):
        calculation = growth(normalized[i], normalized[i - 1])
        # CAGR 和累计增长也必须有首尾可比依据，不能借相邻表标签绕过跨材料门控。
        overall = comparability(normalized[i], normalized[0])
        overall_reasons = list(overall["reasons"])
        if overall["status"] == "unknown":
            overall_reasons.extend(f"{key} 未声明" for key in overall["gaps"])
        if calculation.get("status") != "ok" or overall_reasons:
            blocked.append({"from_year": series[i - 1]["year"], "to_year": series[i]["year"],
                            "calculation": calculation, "overall_comparability": overall,
                            "reasons": [*(calculation.get("reasons") or []),
                                        *([calculation["reason"]] if calculation.get("reason") else []),
                                        *overall_reasons],
                            "operands": {"previous": series[i - 1]["value"], "current": series[i]["value"]},
                            "evidence_ids": [series[i - 1]["evidence_id"], series[i]["evidence_id"]]})
        else:
            yoy_rows.append({"year": series[i]["year"], "yoy_pct": calculation["value"],
                             "evidence_ids": calculation["evidence_ids"], "calculation": calculation})
    if blocked:
        return {"status": "not_comparable", "reason": "趋势计算被期间、质量或可比性门控阻断",
                "series": series, "blocked_comparisons": blocked, "yoy": [],
                "cagr_pct": None, "total_change_pct": None,
                "monotonic_up": None, "monotonic_down": None,
                "years": [row["year"] for row in series],
                "evidence_ids": [row["evidence_id"] for row in series]}
    first_v, last_v = Decimal(series[0]["value"]), Decimal(series[-1]["value"])
    n = len(series) - 1
    cagr = None
    if first_v > 0 and last_v > 0:
        with localcontext() as ctx:
            ctx.prec = 40
            cagr = ((last_v / first_v) ** (Decimal(1) / n) - 1) * 100
    total_pct = None
    if first_v != 0:
        with localcontext() as ctx:
            ctx.prec = 40
            total_pct = (last_v - first_v) / first_v * 100
    values = [Decimal(s["value"]) for s in series]
    monotonic_up = all(values[i] < values[i + 1] for i in range(len(values) - 1))
    monotonic_down = all(values[i] > values[i + 1] for i in range(len(values) - 1))
    return {
        "status": "ok",
        "report_kind": report_kind,
        "series": series,
        "yoy": yoy_rows,
        "cagr_pct": text(cagr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if cagr is not None else None,
        "total_change_pct": text(total_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if total_pct is not None else None,
        "monotonic_up": monotonic_up,
        "monotonic_down": monotonic_down,
        "years": [s["year"] for s in series],
        "evidence_ids": [s["evidence_id"] for s in series],
    }
