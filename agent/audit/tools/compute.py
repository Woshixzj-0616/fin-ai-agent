"""只读数值计算工具。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext

from audit.schema import METRICS
from audit.tools.evidence import _err, _pick_fact, find_evidence
from finance import convert, decimal, evidence_yoy, select_previous, text


def compute_yoy(facts: list[dict], *, company_name_or_code: str, metric: str, year: int,
                draft: str = "") -> dict:
    located = find_evidence(facts, company_name_or_code=company_name_or_code, metric=metric,
                            period_year=int(year), draft=draft)
    if located.get("status") != "ok":
        return located
    fact = next(f for f in facts if f["evidence_id"] == located["items"][0]["evidence_id"])
    previous = select_previous(facts, fact)
    computation = evidence_yoy(fact, previous)
    return {"status": "ok" if computation.get("status") == "ok" else computation.get("status"),
            "yoy": computation, "current_evidence_id": fact["evidence_id"],
            "previous_evidence_id": (previous or {}).get("evidence_id")}


def _one_fact(facts: list[dict], code: str, metric: str, year: int) -> dict | None:
    """取该公司该指标该年的证据；同值多条择优，冲突则 None。"""
    matches = [f for f in facts
               if f.get("company_code") == code and f.get("metric") == metric
               and f.get("period_year") == int(year) and f.get("adjustment") != "before"]
    best, _ = _pick_fact(matches)
    return best


def _norm_value(fact: dict) -> Decimal | None:
    """归一化到元的数值；非金额单位直接取 value。"""
    from finance import NON_AMOUNT_UNITS
    v = decimal(fact.get("value"))
    if v is None:
        return None
    unit = fact.get("unit")
    if unit in NON_AMOUNT_UNITS or unit is None:
        return v
    try:
        return convert(v, unit, "元")
    except (ValueError, ArithmeticError):
        return v


def compute_trend(facts: list[dict], *, company_name_or_code: str, metric: str,
                  start_year: int, end_year: int, draft: str = "") -> dict:
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
    series = []
    for y in range(y0, y1 + 1):
        f = _one_fact(facts, code, metric, y)
        if not f:
            return {"status": "error", "error": f"缺少 {y} 年「{METRICS[metric][0]}」的唯一年报证据，无法算趋势"}
        v = _norm_value(f)
        if v is None:
            return {"status": "error", "error": f"{y} 年数值缺失"}
        series.append({"year": y, "value": text(v), "evidence_id": f["evidence_id"]})
    yoy_rows = []
    for i in range(1, len(series)):
        prev, cur = Decimal(series[i - 1]["value"]), Decimal(series[i]["value"])
        if prev == 0:
            yoy_rows.append({"year": series[i]["year"], "status": "zero_base"})
            continue
        with localcontext() as ctx:
            ctx.prec = 40
            pct = (cur - prev) / prev * 100
        yoy_rows.append({"year": series[i]["year"],
                         "yoy_pct": text(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                         "evidence_ids": [series[i - 1]["evidence_id"], series[i]["evidence_id"]]})
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
        "series": series,
        "yoy": yoy_rows,
        "cagr_pct": text(cagr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if cagr is not None else None,
        "total_change_pct": text(total_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if total_pct is not None else None,
        "monotonic_up": monotonic_up,
        "monotonic_down": monotonic_down,
        "years": [s["year"] for s in series],
        "evidence_ids": [s["evidence_id"] for s in series],
    }
