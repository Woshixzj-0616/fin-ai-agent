"""确定性财务计算与陈述核查：先确认可比性，再用Decimal计算。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

UNITS = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000),
         "百万元": Decimal(1000000), "亿元": Decimal(100000000)}
# 非金额单位：不参与金额换算，normalized_value 保持原值。
NON_AMOUNT_UNITS = {"元/股", "%", "％"}
UNKNOWN = {None, "", "unknown"}


def decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, (float, bool)):
        raise ValueError("金额请使用字符串或整数，避免浮点数与布尔值混入")
    text = str(value).strip().replace(",", "").replace("，", "").replace("−", "-")
    if text in {"-", "—", "–", "不适用", "N/A"}:
        return None
    if (text.startswith("(") and text.endswith(")")) or (
            text.startswith("（") and text.endswith("）")):
        text = "-" + text[1:-1]
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"不是有效数值：{text}") from exc
    if not number.is_finite():
        raise ValueError("拒绝 NaN 和 Infinity")
    return number


def text(number: Decimal | None) -> str | None:
    return None if number is None else format(number, "f")


def convert(value, source_unit: str, target_unit: str = "元") -> Decimal | None:
    if source_unit not in UNITS or target_unit not in UNITS:
        raise ValueError("单位未知；只支持元、万元、亿元，不作默认换算")
    number = decimal(value)
    if number is None:
        return None
    with localcontext() as context:
        context.prec = 40
        return number * UNITS[source_unit] / UNITS[target_unit]


def result(status: str, value=None, **details) -> dict:
    return {"status": status, "value": text(value) if isinstance(value, Decimal) else value,
            **details}


def yoy(current, previous, negative_policy: str = "review") -> dict:
    """正基期：(本期-上期)/上期×100；负基期默认人工复核。"""
    if negative_policy not in {"review", "absolute"}:
        raise ValueError("negative_policy 只能是 review 或 absolute")
    a, b = decimal(current), decimal(previous)
    operands = {"current": text(a), "previous": text(b)}
    if a is None or b is None:
        return result("missing", reason="本期或基期缺失", operands=operands)
    if b == 0:
        return result("zero_base", reason="基期为0，常规同比未定义", operands=operands)
    if b < 0 and negative_policy == "review":
        change = ("扭亏为盈" if a > 0 else "亏损归零" if a == 0
                  else "减亏" if a > b else "亏损扩大" if a < b else "亏损持平")
        return result("negative_base", reason="负基期不自动套用常规同比",
                      change=change, absolute_change=text(a - b), operands=operands)
    denominator = abs(b) if negative_policy == "absolute" else b
    with localcontext() as context:
        context.prec = 40
        value = (a - b) / denominator * 100
    return result("ok", value, unit="%", operands=operands,
                  formula="(current - previous) / abs(previous) * 100"
                  if negative_policy == "absolute" else "(current - previous) / previous * 100",
                  negative_policy=negative_policy)


def ratio(numerator, denominator, as_percent: bool = False) -> dict:
    a, b = decimal(numerator), decimal(denominator)
    if a is None or b is None:
        return result("missing", reason="分子或分母缺失")
    if b == 0:
        return result("zero_base", reason="分母为0")
    with localcontext() as context:
        context.prec = 40
        value = a / b * (100 if as_percent else 1)
    return result("ok", value, unit="%" if as_percent else "倍",
                  formula="numerator / denominator" + (" * 100" if as_percent else ""),
                  operands={"numerator": text(a), "denominator": text(b)})


def percentage_points(current_percent, previous_percent) -> dict:
    a, b = decimal(current_percent), decimal(previous_percent)
    if a is None or b is None:
        return result("missing", reason="百分比缺失")
    return result("ok", a - b, unit="百分点", formula="current_percent - previous_percent",
                  operands={"current_percent": text(a), "previous_percent": text(b)})


# 陈述运算符：eq=精确；approx=约；exceed=超过；at_least=不低于；at_most=不高于/不足；below=低于
OPERATORS = ("eq", "approx", "exceed", "at_least", "at_most", "below")
DEFAULT_TOLERANCE_PCT = Decimal("2")
MAX_TOLERANCE_PCT = Decimal("5")


def _tolerance(tolerance_pct) -> tuple[Decimal | None, str | None]:
    """容差百分比；超上限压到 5% 并给 warning，负数/非法返回错误说明。"""
    if tolerance_pct is None:
        return DEFAULT_TOLERANCE_PCT, None
    try:
        t = Decimal(str(tolerance_pct))
    except ArithmeticError:
        return None, "容差不是合法数值"
    if t < 0:
        return None, "容差不能为负"
    if t > MAX_TOLERANCE_PCT:
        return MAX_TOLERANCE_PCT, f"容差 {t}% 超上限，已压到 {MAX_TOLERANCE_PCT}%"
    return t, None


def compare_number(actual, claimed, decimals: int | None = None,
                   operator: str = "eq", tolerance_pct=None) -> dict:
    """同一单位下比较两个数。

    - eq：按陈述小数位 ROUND_HALF_UP
    - approx：相对误差 ≤ tolerance_pct（默认 2%）
    - exceed / at_least / at_most / below：证据值是否满足陈述的不等式
    """
    if operator not in OPERATORS:
        return result("needs_review", reason=f"不支持的运算符：{operator}")
    try:
        a, b = decimal(actual), decimal(claimed)
    except ValueError as exc:
        return result("needs_review", reason=str(exc))
    if a is None or b is None:
        return result("missing", reason="证据值或陈述值缺失")

    if operator == "eq":
        places = max(0, -b.as_tuple().exponent) if decimals is None else decimals
        if not isinstance(places, int) or not 0 <= places <= 12:
            raise ValueError("精度必须为0至12位小数")
        step = Decimal(1).scaleb(-places)
        with localcontext() as context:
            context.prec = 40
            rounded = a.quantize(step, rounding=ROUND_HALF_UP)
        return result("match" if rounded == b else "mismatch",
                      text(rounded), exact_value=text(a), claimed_value=text(b),
                      operator=operator, decimals=places,
                      rounding="ROUND_HALF_UP", difference=text(a - b),
                      formula="round_half_up(actual, decimals)")

    if operator == "approx":
        tol, warn = _tolerance(tolerance_pct)
        if tol is None:
            return result("needs_review", reason=warn)
        with localcontext() as context:
            context.prec = 40
            base = abs(a) if a != 0 else (abs(b) if b != 0 else Decimal(1))
            rel = abs(a - b) / base * 100
        ok = rel <= tol
        return result("match" if ok else "mismatch", text(a),
                      exact_value=text(a), claimed_value=text(b),
                      operator=operator, tolerance_pct=text(tol),
                      relative_error_pct=text(rel),
                      difference=text(a - b),
                      warning=warn,
                      formula=f"abs(actual-claimed)/abs(actual)*100 <= {tol}%")

    # 不等式：陈述的是对真实值的约束
    table = {
        "exceed": (a > b, "actual > claimed"),
        "at_least": (a >= b, "actual >= claimed"),
        "at_most": (a <= b, "actual <= claimed"),
        "below": (a < b, "actual < claimed"),
    }
    ok, formula = table[operator]
    return result("match" if ok else "mismatch", text(a),
                  exact_value=text(a), claimed_value=text(b),
                  operator=operator, difference=text(a - b),
                  formula=formula)


def compare_amount(actual, actual_unit: str, claimed, claimed_unit: str,
                   decimals: int | None = None,
                   operator: str = "eq", tolerance_pct=None) -> dict:
    """将证据换成陈述单位，再按运算符比较。"""
    try:
        converted = convert(actual, actual_unit, claimed_unit)
    except ValueError as exc:
        return result("needs_review", reason=str(exc))
    answer = compare_number(converted, claimed, decimals, operator=operator,
                            tolerance_pct=tolerance_pct)
    answer.update(claimed_unit=claimed_unit, actual_unit=actual_unit,
                  actual_value=text(decimal(actual)), operator=operator,
                  formula=(answer.get("formula") or "") +
                          " | unit=actual * source_factor / target_factor")
    return answer


def comparable(current: dict, previous: dict) -> list[str]:
    reasons = []
    for field in ("company_code", "metric", "currency", "scope", "period_kind",
                  "duration_months", "comparison_group"):
        a, b = current.get(field), previous.get(field)
        if a in UNKNOWN or b in UNKNOWN:
            reasons.append(f"{field} 未明确")
        elif a != b:
            reasons.append(f"{field} 不一致")
    if current.get("period_kind") != "annual":
        reasons.append("首版只计算完整年度同比")
    if current.get("period_year", 0) - previous.get("period_year", 0) != 1:
        reasons.append("比较期间不是相邻两年")
    if previous.get("adjustment") == "before":
        reasons.append("基期为调整前，尚未确认可比性")
    return reasons


def evidence_yoy(current: dict, previous: dict | None) -> dict:
    if previous is None:
        return result("missing", reason="同一报告中未找到唯一可比上年值")
    reasons = comparable(current, previous)
    if reasons:
        return result("not_comparable", reasons=reasons)
    try:
        a = convert(current.get("value"), current.get("unit"))
        b = convert(previous.get("value"), previous.get("unit"))
    except ValueError as exc:
        return result("not_comparable", reasons=[str(exc)])
    answer = yoy(a, b)
    answer["evidence_ids"] = [current["evidence_id"], previous["evidence_id"]]
    return answer


def select_previous(facts: list[dict], current: dict) -> dict | None:
    candidates = [f for f in facts if f["comparison_group"] == current["comparison_group"]
                  and f["metric"] == current["metric"]
                  and f["period_year"] == current["period_year"] - 1]
    after = [f for f in candidates if f["adjustment"] == "after"]
    preferred = after or [f for f in candidates if f["adjustment"] == "as_reported"]
    return preferred[0] if len(preferred) == 1 else None


def analyze(facts: list[dict], run) -> dict:
    current = [f for f in facts if f["period_year"] == f["report_year"]]
    rows, groups = [], {}
    for fact in current:
        previous = select_previous(facts, fact)
        computation = evidence_yoy(fact, previous)
        reported = fact.get("reported_yoy")
        disclosure_check = None
        if computation["status"] == "ok" and reported:
            places = max(0, -decimal(reported["value"]).as_tuple().exponent)
            with localcontext() as context:
                context.prec = 40
                rounded = decimal(computation["value"]).quantize(
                    Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
            disclosure_check = {
                "status": "match" if rounded == decimal(reported["value"]) else "mismatch",
                "calculated_rounded": text(rounded), "reported": reported["value"],
                "decimals": places, "rounding": "ROUND_HALF_UP",
                "source_page": fact["page"], "source_bbox": reported["bbox"],
            }
        row = {
            "company_code": fact["company_code"], "company_name": fact["company_name"],
            "report_year": fact["report_year"], "metric": fact["metric"],
            "metric_name": fact["metric_name"], "current": fact["normalized_value"],
            "previous": previous["normalized_value"] if previous else None,
            "previous_adjustment": previous["adjustment"] if previous else None,
            "scope": fact["scope"], "currency": fact["currency"], "unit": fact.get("unit"),
            "yoy": computation, "reported_yoy": reported, "reported_yoy_check": disclosure_check,
            "evidence_id": fact["evidence_id"],
            "previous_evidence_id": previous["evidence_id"] if previous else None,
            "source_file": fact["source_file"], "page": fact["page"],
            "issues": fact["issues"], "document_id": fact["document_id"],
        }
        rows.append(row)
        groups.setdefault(fact["document_id"], {})[fact["metric"]] = (fact, row)
        run.event("calculation", operation="annual_yoy", **row)
    signals = []
    for document_id, metrics in groups.items():
        profit, adjusted, cash = (metrics.get(k) for k in
                                  ("parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow"))
        if profit and adjusted and not profit[0]["issues"] and not adjusted[0]["issues"]:
            p, a = profit[0], adjusted[0]
            if (p["currency"], p["scope"], p["comparison_group"]) == (
                    a["currency"], a["scope"], a["comparison_group"]):
                signals.append({
                    "document_id": document_id, "type": "profit_minus_adjusted_profit",
                    "description": "归母净利润减扣非归母净利润（辅助观察非经常性项目影响）",
                    "value": text(decimal(p["normalized_value"]) - decimal(a["normalized_value"])),
                    "unit": "元", "formula": "parent_net_profit - adjusted_parent_net_profit",
                    "evidence_ids": [p["evidence_id"], a["evidence_id"]],
                    "interpretation": "这是差额计算，不构成错误或财务造假结论。",
                })
        if profit and cash:
            p_rate, c_rate = profit[1]["yoy"], cash[1]["yoy"]
            if p_rate["status"] == c_rate["status"] == "ok":
                if decimal(p_rate["value"]) > 0 and decimal(c_rate["value"]) < 0:
                    signals.append({
                        "document_id": document_id, "type": "profit_up_cash_down",
                        "description": "归母净利润增长，同时经营现金流净额下降，建议结合现金流明细复核。",
                        "evidence_ids": p_rate["evidence_ids"] + c_rate["evidence_ids"],
                        "interpretation": "归母利润与合并经营现金流的归属口径不同；这里只比较各自同比方向，不作等式核验。",
                    })
    for signal in signals:
        run.event("calculation", operation="auxiliary_signal", **signal)
    return {"rows": rows, "signals": signals,
            "basis": "使用同一份年度报告中的本年与上年比较值，调整后列优先。",
            "limits": "结论相对于所选原始文件；未自动认定其为截至今日最新有效披露版本。"}


def check_claim(claim: dict, facts: list[dict]) -> dict:
    output = {"claim_id": claim["id"], "original_sentence": claim["sentence"],
              "check_item": claim["kind"], "status": "证据不足", "evidence_ids": [],
              "calculation": None, "suggestion": None}
    matches = [f for f in facts if f["company_code"] == claim["company_code"]
               and f["metric"] == claim["metric"] and f["period_year"] == claim["period_year"]
               and f["report_year"] == claim["source_report_year"]
               and f["adjustment"] != "before"]
    if len(matches) != 1:
        output["reason"] = "未找到唯一匹配的公司、年度、指标与文件版本"
        return output
    fact = matches[0]
    output["evidence_ids"] = [fact["evidence_id"]]
    output["source_file"], output["page"] = fact["source_file"], fact["page"]
    for field in ("scope", "currency", "period_kind"):
        if not claim.get(field) or fact.get(field) in {None, "", "unknown"} or claim[field] != fact[field]:
            output.update(status="口径冲突／需人工复核", reason=f"{field} 不明确或不一致")
            return output
    if fact["issues"]:
        output["reason"] = "证据存在待复核字段：" + ",".join(fact["issues"])
        return output
    if claim["kind"] == "amount":
        operator = claim.get("operator") or "eq"
        calculation = compare_amount(fact["value"], fact["unit"], claim["value"], claim["unit"],
                                     operator=operator, tolerance_pct=claim.get("tolerance_pct"))
        expected = calculation.get("value")
    elif claim["kind"] == "yoy":
        if claim["unit"] != "%":
            output.update(status="口径冲突／需人工复核", reason="同比增长率使用百分比；百分点属于另一类计算")
            return output
        previous = select_previous(facts, fact)
        computation = evidence_yoy(fact, previous)
        if computation["status"] != "ok":
            output.update(status="口径冲突／需人工复核", calculation=computation,
                          reason="同比基期不可比或不能按常规公式计算")
            return output
        output["evidence_ids"] = computation["evidence_ids"]
        operator = claim.get("operator") or "eq"
        comparison = compare_number(computation["value"], claim["value"],
                                    operator=operator, tolerance_pct=claim.get("tolerance_pct"))
        comparison["claimed_unit"] = "%"
        if operator == "eq":
            comparison["formula"] = "round_half_up(yoy_percent, claimed_decimal_places)"
        calculation = {**comparison, "yoy_calculation": computation}
        expected = comparison.get("value")
    else:
        output["reason"] = "首版结构化样例仅支持金额与年度同比"
        return output
    output["calculation"] = calculation
    if calculation["status"] == "match":
        reason = ("单位、口径和陈述精度下与原文证据一致" if (claim.get("operator") or "eq") == "eq"
                  else "在陈述运算符与容差下与原文证据一致")
        output.update(status="证据支持", reason=reason)
    elif calculation["status"] == "mismatch":
        op = claim.get("operator") or "eq"
        reason = ("可比口径下陈述值与证据或计算结果不同" if op == "eq"
                  else f"陈述运算符「{op}」不成立，或超出约定容差")
        output.update(status="确认错误", reason=reason,
                      suggestion=f"将该核查项改为 {expected}{claim['unit']}，并引用所列年报页码。")
    else:
        output.update(status="口径冲突／需人工复核", reason=calculation.get("reason"))
    return output
