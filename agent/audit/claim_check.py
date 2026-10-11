"""主张门控、双轨核查和工具裁决转检查行。"""
from __future__ import annotations

import json
import re

from audit.grounding import resolve_company
from audit.prompt import FORECAST, FUZZY
from audit.schema import METRICS, OPERATORS, UNITS, LLMError, schema, validate_schema
from finance import check_claim, decimal, text


def check_one_claim(item: dict, facts: list[dict], sentence: str, draft: str, claim_id: str) -> dict:
    output = {"claim_id": claim_id, "original_sentence": sentence, "quote": item["quote"],
              "track": "review", "status": "口径冲突／需人工复核", "reason_code": None, "reason": None,
              "evidence_ids": [], "evidence": [], "calculation": None, "expected": None, "suggestion": None,
              "plain_claim": item.get("plain_claim"), "interpretation": {
                  "is_forecast": bool(item.get("is_forecast")),
                  "ambiguity": item.get("ambiguity") or "none",
                  "claim_type": item.get("claim_type") or item.get("kind"),
              }}

    def stop(code, reason, status="口径冲突／需人工复核", track="review"):
        output.update(reason_code=code, reason=reason, status=status, track=track)
        return output

    def model_note(code, reason):
        """模型判断轨：不给对错，只保留语义解释。"""
        return stop(code, reason, status="模型判断", track="model")

    quote, context = item["quote"], item.get("context_quote") or ""
    if not quote or quote not in sentence or (context and context not in draft):
        return stop("ungrounded_quote", "模型给出的原文或上下文无法在草稿中逐字定位")
    source = quote + "\n" + sentence + "\n" + context

    if item.get("is_forecast") or item.get("claim_type") == "forecast" or FORECAST.search(quote):
        return model_note("forecast_marked_only",
                          "预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实")

    if item.get("claim_type") == "comparison" or item.get("verification_action") == "mark_out_of_scope":
        company_b = item.get("company_b")
        comp_op = item.get("comparison_operator")
        if company_b and comp_op and item.get("metric") not in (None, "unknown", "unsupported"):
            from audit.tools.compare import compare_companies
            code_a = resolve_company(item.get("company_name") or "", source, draft, facts)
            code_b = resolve_company(company_b, source, draft, facts)
            if code_a and code_b and code_a != code_b and item.get("period_year"):
                result = compare_companies(
                    facts, company_a=code_a, company_b=code_b,
                    metric=item["metric"], period_year=int(item["period_year"]), draft=draft)
                if result.get("verdict") in ("evidence_supported", "confirmed_error"):
                    output.update(
                        status="证据支持" if result["verdict"] == "evidence_supported" else "确认错误",
                        track="deterministic", reason_code=result.get("reason_code"),
                        reason=result.get("reason"),
                        evidence_ids=result.get("evidence_ids") or [])
                    return output
                if result.get("verdict") == "needs_review":
                    return stop(result.get("reason_code") or "comparison_needs_review",
                                result.get("reason") or "跨公司比较缺少证据")
        return model_note("cross_company_comparison",
                          "跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留")

    if item.get("claim_type") == "qualitative":
        return model_note("qualitative_statement",
                          "定性陈述：本阶段不做数值裁决，解释见 plain_claim")

    code = resolve_company(item["company_name"], source, draft, facts)
    if not code:
        return stop("unresolved_company", "原文公司名称/代码无法对应到本次已加载材料，或无法在草稿中定位，不猜测公司", "证据不足")
    year = item["period_year"]
    if year is None or not re.search(rf"(?<!\d){year}(?!\d)", source):
        return stop("unresolved_period", "年度未明确或无法从原文定位，不猜测相对日期", "证据不足")
    metric_text = item["metric_text"]
    if not metric_text or (metric_text not in quote and metric_text not in context):
        return stop("ungrounded_metric", "模型给出的指标名称无法在原文中定位")
    metric = next((key for key, names in METRICS.items() if metric_text in names), None)
    if not metric:
        if metric_text in {"净利润", "净利", "利润", "净利润总额"}:
            return stop("ambiguous_profit_scope", "未明确归母归属，不能将普通净利润当成归母净利润")
        return stop("unsupported_metric",
                    "指标不在目录内（营收/营业总收入/归母/扣非/经营现金流/EPS/加权ROE/总资产）", "证据不足")
    if item["metric"] != metric and item["metric"] not in {"unknown", "unsupported"}:
        return stop("model_parse_conflict", "模型指标分类与原文指标名称不一致")
    period_fields = {}
    if item["period_kind"] != "annual" or re.search(r"季度|半年|前三季|Q[1-4]", source, re.I):
        from periods import end_date
        if re.search(r"半年|半年度|上半年", source):
            expected_kind, month, start_month = "half", 6, 1
        else:
            quarter = re.search(r"(?:第?([一二三四1234])季度|Q([1-4]))", source, re.I)
            cumulative = re.search(r"前三季|年初至", source)
            if not quarter and not cumulative:
                return stop("unsupported_period", "季度/半年期间没有明确起止，不能与全年值混比")
            q = int(quarter[2]) if quarter and quarter[2] else {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}.get(quarter[1], 3) if quarter else 3
            expected_kind, month, start_month = "quarter", q*3, 1 if cumulative else q*3-2
        if item["period_kind"] != expected_kind:
            return stop("model_parse_conflict", "模型期间分类与原文的半年/单季/累计表达不一致")
        period_fields = {"period_start": f"{year}-{start_month:02d}-01", "period_end": end_date(year, month)}

    operator = item.get("operator") or "eq"
    if operator not in OPERATORS:
        return stop("invalid_operator", f"未知运算符 {operator}")
    if operator == "none":
        return model_note("no_numeric_constraint", "无数值约束的陈述，不进数值裁决")
    if operator == "eq" and (item.get("qualifier") not in {"exact", None} or FUZZY.search(quote)):
        return stop("non_exact_claim",
                    "含约数或不等表达，但未给运算符；请标 operator=approx/exceed/… 或改写为精确数")

    raw_value, raw_unit = item["value"], item["unit"]
    if raw_value is None or raw_unit not in UNITS or raw_unit not in quote:
        return stop("unsupported_value_or_unit", "数值或单位未明确，或该表达暂不支持")
    normalize = lambda s: s.replace(",", "").replace("，", "").replace("−", "-").lstrip("+")
    numbers = re.findall(r"[-+−]?\d[\d,，]*(?:\.\d+)?", quote)
    if normalize(raw_value) not in [normalize(n) for n in numbers]:
        return stop("ungrounded_value", "模型数值未在原文出现；拒绝模型计算、换算或改写数字")
    pairs = re.findall(
        r"([-+−]?\d[\d,，]*(?:\.\d+)?)\s*(亿元|万元|千元|百万元|元/股|元／股|元|亿|万|百分点|%|％)", quote)
    unit_ok = {raw_unit, UNITS.get(raw_unit, raw_unit), {"％": "%", "元／股": "元/股"}.get(raw_unit, raw_unit)}
    if not any(normalize(number) == normalize(raw_value) and unit in unit_ok for number, unit in pairs):
        return stop("ungrounded_unit", "模型单位未与原文数值直接对应；不把亿元、万元误读为元")
    try:
        value = decimal(normalize(raw_value))
    except ValueError:
        return stop("invalid_value", "原文数值不能作为有限十进制数处理")
    if value is None or len(value.as_tuple().digits) > 25 or -value.as_tuple().exponent > 12:
        return stop("unsupported_precision", "原文数值为空、过长或精度超过现有计算规则")

    kind = item.get("kind") or ("yoy" if item.get("claim_type") == "yoy" else "amount")
    if kind not in {"amount", "yoy"}:
        return stop("unsupported_operation", "数值裁决仅支持金额与年度同比")
    if kind == "amount" and re.search(r"增加了?|减少了?|增长了?|下降了?", quote) and not re.search(r"增至|降至|达到", quote):
        return stop("unsupported_change_amount", "增减金额不等于期末/当期金额，需人工复核")
    if kind == "yoy" and re.search(r"下降|减少|降低|下滑", quote) and value > 0:
        value = -value
    if kind == "amount" and "亏损" in quote and value > 0:
        value = -value
    if re.search(r"美元|USD|US\$", source, re.I) and item["currency"] != "USD":
        return stop("model_parse_conflict", "原文明示美元，但模型币种字段不一致")
    if re.search(r"母公司(?!股东)", source) and item["scope"] != "parent_company":
        return stop("model_parse_conflict", "原文明示母公司口径，但模型统计口径字段不一致")

    claim = {"id": claim_id, "sentence": sentence, "company_code": code,
             "period_year": year, "source_report_year": year, "metric": metric,
             "kind": kind, "value": text(value), "unit": UNITS[raw_unit],
             "currency": item["currency"], "scope": item["scope"], "period_kind": item["period_kind"], **period_fields,
             "operator": operator, "tolerance_pct": item.get("tolerance_pct")}
    try:
        checked = check_claim(claim, facts)
    except (ValueError, ArithmeticError):
        return stop("calculation_unavailable", "现有计算规则无法安全处理该数值，需人工复核")
    output.update(checked, normalized_claim=claim, reason_code="deterministic_check",
                  track="deterministic")
    if checked["status"] == "确认错误":
        output["expected"] = checked["calculation"]["value"]
    index = {fact["evidence_id"]: fact for fact in facts}
    for ident in output["evidence_ids"]:
        fact = index[ident]
        output["evidence"].append({key: fact[key] for key in (
            "evidence_id", "source_file", "source_sha256", "page", "metric_name", "value", "unit",
            "period_year", "scope", "value_bbox")})
    return output


def check_payload(payload: dict, sentences: list[dict], draft: str, facts: list[dict]) -> list[dict]:
    validate_schema(payload, schema())
    sentence_map = {row["sentence_id"]: row["text"] for row in sentences}
    used = {item["sentence_id"] for item in payload["items"]}
    unused = payload["unclaimed_sentences"]
    if (used | set(unused) != set(sentence_map) or used & set(unused) or len(set(unused)) != len(unused)):
        raise LLMError("模型遗漏、重复标记或伪造句子ID；本次未判定，请分段重试")
    results = []
    for i, item in enumerate(payload["items"], 1):
        result = check_one_claim(item, facts, sentence_map[item["sentence_id"]], draft, f"C{i}")
        result["sentence_id"] = item["sentence_id"]
        results.append(result)
    for ident in unused:
        results.append({"claim_id": f"S{ident}", "sentence_id": ident,
                        "original_sentence": sentence_map[ident], "status": "口径冲突／需人工复核",
                        "reason_code": "no_claim_extracted", "reason": "该句未拆出核查项，请人工确认是否遗漏",
                        "evidence_ids": [], "evidence": [], "calculation": None, "expected": None, "suggestion": None})
    return sorted(results, key=lambda row: row["sentence_id"])


def _tool_verdict_rows(tool_results: list[dict], facts: list[dict] | None = None) -> list[dict]:
    """把循环里 compare_claim/compare_companies 的裁决转成检查行，避免 fallback 丢结果。"""
    evidence_index = {fact.get("evidence_id"): fact for fact in facts or []}
    rows = []
    for i, entry in enumerate(tool_results or []):
        name = entry.get("name")
        if name not in {"compare_claim", "compare_companies"}:
            continue
        result = entry.get("result") or {}
        verdict = result.get("verdict")
        if verdict not in {"evidence_supported", "confirmed_error"}:
            continue
        evidence_ids = result.get("evidence_ids") or []
        evidence = []
        for evidence_id in evidence_ids:
            fact = evidence_index.get(evidence_id)
            if fact:
                evidence.append({key: fact.get(key) for key in (
                    "evidence_id", "source_file", "source_sha256", "page", "metric_name",
                    "value", "unit", "period_year", "scope", "value_bbox")})
        rows.append({
            "claim_id": f"tool_{entry.get('round', 0)}_{i}",
            "original_sentence": "",
            "quote": json.dumps(entry.get("arguments") or {}, ensure_ascii=False),
            "track": "deterministic",
            "status": "证据支持" if verdict == "evidence_supported" else "确认错误",
            "reason_code": result.get("reason_code") or "deterministic_check",
            "reason": result.get("reason") or "工具循环内确定性裁决（submit 失败后仍保留）",
            "evidence_ids": evidence_ids,
            "evidence": evidence,
            "calculation": result.get("calculation"),
            "expected": result.get("expected"),
            "suggestion": None,
            "plain_claim": f"工具裁决：{name}",
            "interpretation": {"is_forecast": False, "ambiguity": "none",
                               "claim_type": "tool_verdict"},
            "source_tool": name,
        })
    return rows
