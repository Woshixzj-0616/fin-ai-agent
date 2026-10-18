"""主张门控、双轨核查和工具裁决转检查行。"""
from __future__ import annotations

import json
import re

from audit.grounding import resolve_period, resolve_subject, segments
from audit.prompt import FORECAST, FUZZY
from audit.resolution import (mark_conflict, resolution_for_subject, resolution_for_subjects,
                          source_scope, unit_candidates)
from audit.schema import METRICS, OPERATORS, UNITS, LLMError, schema, validate_schema
from finance import check_claim, decimal, evidence_yoy, scope_agnostic, select_previous, text

UP_WORDS = re.compile(r"增长|上升|提高|增加|增厚|上行|改善")
DOWN_WORDS = re.compile(r"下降|减少|降低|下滑|回落|收窄|恶化")
FLAT_WORDS = re.compile(r"持平|稳定|不变|基本相当")
DIRECTION_WORDS = {"up": "增长", "down": "下降", "flat": "持平"}
PERIOD_KINDS = {"annual", "half", "quarter"}


def claimed_direction(item: dict, quote: str) -> str | None:
    """只接受原文明示的单一方向；否定或复合方向保留为需复核。"""
    if re.search(r"未|并非|不是|没有|不(?:再|会|一定|能)?(?:增长|上升|下降|减少|提高|增加)", quote):
        return None
    flat, up, down = bool(FLAT_WORDS.search(quote)), bool(UP_WORDS.search(quote)), bool(DOWN_WORDS.search(quote))
    if sum((flat, up, down)) != 1:
        return None
    return "flat" if flat else ("up" if up else "down")


def _comparison_relation(quote: str):
    """最长关系词优先；否定/复合关系不交由模型补成确定运算符。"""
    pattern = (r"不低于|不少于|至少|不小于|不超过|不高于|不大于|至多|"
               r"高于|超过|大于|多于|低于|少于|小于|等于|相当于|一致")
    matches = list(re.finditer(pattern, quote))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.search(r"未|并非|不是|没有", quote[:match.start()]):
        return None
    word = match.group(0)
    groups = {"at_least": {"不低于", "不少于", "至少", "不小于"},
              "at_most": {"不超过", "不高于", "不大于", "至多"},
              "exceed": {"高于", "超过", "大于", "多于"},
              "below": {"低于", "少于", "小于"}, "eq": {"等于", "相当于", "一致"}}
    return next((key, match.start(), match.end()) for key, words in groups.items() if word in words)


def _direction_of(value) -> str | None:
    number = decimal(value)
    if number is None:
        return None
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "flat"


def _comparable_previous(facts: list[dict], fact: dict, baseline_year=None) -> dict | None:
    """方向核查的上期值：年报走同报告比较列，半年/单季走上年同一期间。"""
    if fact.get("period_kind") == "annual" and baseline_year in {None, fact.get("period_year", 0) - 1}:
        return select_previous(facts, fact)
    start, end = fact.get("period_start") or "", fact.get("period_end") or ""
    if len(start) < 10 or len(end) < 10:
        return None
    previous_year = int(baseline_year) if baseline_year is not None else int(fact.get("period_year") or 0) - 1
    candidates = [f for f in facts
                  if f.get("company_code") == fact.get("company_code")
                  and f.get("metric") == fact.get("metric")
                  and f.get("period_year") == previous_year
                  and f.get("adjustment") != "before"
                  and (f.get("period_start") or "")[4:] == start[4:]
                  and (f.get("period_end") or "")[4:] == end[4:]]
    return candidates[0] if len(candidates) == 1 else None


def _hydrate_evidence(output: dict, facts: list[dict]) -> None:
    index = {fact.get("evidence_id"): fact for fact in facts}
    for ident in output.get("evidence_ids") or []:
        fact = index.get(ident)
        if not fact:
            continue
        output.setdefault("evidence", []).append({key: fact.get(key) for key in (
            "evidence_id", "source_file", "source_sha256", "page", "metric_name", "value", "unit",
            "period_year", "period_kind", "period_start", "period_end", "document_id",
            "scope", "value_bbox")})



def _atom_key(item: dict) -> str:
    return json.dumps({key: value for key, value in item.items()
                       if key not in {"claim_id", "context_quote", "plain_claim", "ambiguity", "verification_action"}},
                      ensure_ascii=False, sort_keys=True)

def check_one_claim(item: dict, facts: list[dict], sentence: str, draft: str, claim_id: str) -> dict:
    atom_key = _atom_key(item)
    output = {"claim_id": claim_id, "atom_key": atom_key,
              "original_sentence": sentence, "quote": item["quote"],
              "track": "review", "status": "口径冲突／需人工复核", "reason_code": None, "reason": None,
              "evidence_ids": [], "evidence": [], "calculation": None, "expected": None, "suggestion": None,
              "plain_claim": item.get("plain_claim"), "interpretation": {
                  "is_forecast": bool(item.get("is_forecast")),
                  "ambiguity": item.get("ambiguity") or "none",
                  "claim_type": item.get("claim_type") or item.get("kind"),
              },
              "grounding": {"company": None, "period": None}}
    output["resolution"] = {
        "status": "not_evaluated",
        "label": "未进入解析",
        "candidates": [],
        "conflicts": [],
        "next_action": None,
    }

    def stop(code, reason, status="口径冲突／需人工复核", track="review"):
        if output["resolution"].get("status") == "not_evaluated":
            output["resolution"].update(status="candidates", label="候选待定",
                                     next_action={"code": "review_input",
                                                  "message": "补全原文、公司、期间或单位后重试，或转人工复核"})
        output.update(reason_code=code, reason=reason, status=status, track=track)
        return output

    def model_note(code, reason):
        """模型判断轨：不给对错，只保留语义解释。"""
        return stop(code, reason, status="模型判断", track="model")

    quote, context = item["quote"], item.get("context_quote") or ""
    if not quote or quote not in sentence or (context and context not in draft):
        return stop("ungrounded_quote", "模型给出的原文或上下文无法在草稿中逐字定位")
    source = quote + "\n" + sentence + "\n" + context
    context_text = quote + "\n" + context

    paragraph = _paragraph_of(draft, sentence)
    preceding = _preceding_text(paragraph, sentence)

    def bind_company(name, *, prefer_field=True, priority_text=quote, allow_multi_field=False):
        return resolve_subject(name, sentence=sentence, context=context_text, draft=draft,
                               facts=facts, paragraph=paragraph, preceding=preceding,
                               prefer_field=prefer_field, priority_text=priority_text,
                               allow_multi_field=allow_multi_field)

    def bind_period(year):
        return resolve_period(year, sentence=sentence, context=context, quote=quote,
                              paragraph=paragraph, preceding=preceding)

    if item.get("is_forecast") or item.get("claim_type") == "forecast" or FORECAST.search(quote):
        return model_note("forecast_marked_only",
                          "预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实")

    if item.get("claim_type") == "comparison" or item.get("verification_action") == "mark_out_of_scope":
        company_b = item.get("company_b")
        comp_op = item.get("comparison_operator")
        if company_b and comp_op and item.get("metric") not in (None, "unknown", "unsupported"):
            from audit.tools.compare import compare_companies
            # 比较主语按逐字引文里最先出现的主体绑定；被比较方由模型字段在句内候选中选择。
            subject_a = bind_company(item.get("company_name") or "", prefer_field=False,
                                     priority_text=quote)
            subject_b = bind_company(company_b, allow_multi_field=True)
            output["grounding"] = {
                "company": {"a": subject_a, "b": subject_b},
                "period": bind_period(item.get("period_year")),
            }
            output["resolution"] = resolution_for_subjects(
                [subject_a, subject_b], source_text=source + "\n" + draft, facts=facts)
            if subject_a.get("conflict") or subject_b.get("conflict"):
                output["interpretation"]["model_company_conflict"] = True
            code_a, code_b = subject_a.get("code"), subject_b.get("code")
            if not code_a or not code_b:
                if any(subject.get("basis") == "ambiguous_sentence_companies"
                       for subject in (subject_a, subject_b)):
                    return stop("ambiguous_company_in_sentence",
                                "同一句出现多家公司，无法确定比较主体；请拆分句子或人工复核", "证据不足")
                missing = item.get("company_name") if not code_a else company_b
                return stop("comparison_company_unresolved",
                            f"比较一方「{missing or '未给出'}」无法对应到本次已加载材料，不能跨库比较",
                            "证据不足")
            if code_a == code_b:
                return stop("comparison_same_company", "比较双方解析为同一公司，不能构成跨公司比较", "证据不足")
            period_binding = output["grounding"]["period"]
            if period_binding.get("conflict"):
                mark_conflict(output["resolution"], status="period_conflict", field="period_year",
                              source_value=period_binding.get("year"), model_value=item.get("period_year"),
                              basis=period_binding.get("located_in") or "unknown",
                              source_text=period_binding.get("matched_text") or sentence,
                              action_code="confirm_period", action_message="按原文年度修正期间后重跑")
                return stop("model_parse_conflict", "模型年度与本句/引文明示年度不一致")
            year = period_binding.get("year")
            if year is None or not period_binding.get("located_in"):
                mark_conflict(output["resolution"], status="period_conflict", field="period_year",
                              source_value=None, model_value=item.get("period_year"), basis="missing",
                              source_text=sentence, action_code="confirm_period",
                              action_message="补全年份或选择明确期间后重跑")
                return stop("unresolved_period", "比较期间无法在原文唯一定位", "证据不足")
            if period_binding.get("baseline_year") is not None and period_binding["baseline_year"] != year:
                return stop("unsupported_comparison_period", "跨公司比较的两侧年份不同，现有工具不能按同一年份替代", "证据不足")
            relation = _comparison_relation(quote)
            if relation is None:
                return stop("ungrounded_comparison_operator", "比较方向没有在逐字引文中明确定位")
            if relation[0] != comp_op:
                return stop("model_parse_conflict", "模型比较运算符与原文关系词不一致")
            from audit.grounding import explicit_companies
            mentions = {m["code"]: m for m in explicit_companies(quote, facts)}
            if (code_a not in mentions or code_b not in mentions
                    or not (mentions[code_a]["position"] < relation[1] < mentions[code_b]["position"])):
                return stop("ambiguous_comparison_roles", "比较双方与关系词的主被动位置未明确，需人工复核")
            metric_text = item.get("metric_text") or ""
            raw_metric = next((key for key, names in METRICS.items() if metric_text in names), None)
            if metric_text not in quote or raw_metric != item.get("metric"):
                return stop("model_parse_conflict", "比较指标未在引文定位或分类不一致")
            explicit_scope = source_scope(source)
            if (explicit_scope and not scope_agnostic(raw_metric, item.get("unit"))
                    and item.get("scope") != explicit_scope["scope"]):
                mark_conflict(output["resolution"], status="scope_conflict", field="scope",
                              source_value=explicit_scope, model_value=item.get("scope"),
                              basis="source_text", source_text=explicit_scope["matched"],
                              action_code="confirm_scope",
                              action_message="按原文归母/合并/母公司口径修正后重跑，或转人工复核")
                return stop("model_parse_conflict", "比较口径与原文的归母/合并/母公司表达不一致")
            period_fields = _precise_period({**item, "period_year": year}, quote + "\n" + sentence)
            if period_fields.get("conflict"):
                mark_conflict(output["resolution"], status="period_conflict", field="period_kind",
                              source_value=quote + "\n" + sentence,
                              model_value=item.get("period_kind"), basis="source_text",
                              source_text=quote + "\n" + sentence,
                              action_code="confirm_period",
                              action_message="按原文半年/季度/年度分类修正后重跑")
                return stop("model_parse_conflict", "模型期间分类与原文半年/季度表达不一致")
            if period_fields.get("unsupported"):
                mark_conflict(output["resolution"], status="period_conflict", field="period_start",
                              source_value=None, model_value=item.get("period_kind"), basis="missing",
                              source_text=quote + "\n" + sentence,
                              action_code="confirm_period",
                              action_message="补全比较期间起止后重跑，不退回全年值")
                return stop("unsupported_period", "比较期间起止未明确，不能退回全年值", "证据不足")
            period_fields = _period_filters(period_fields)
            if year:
                try:
                    result = compare_companies(
                        facts, company_a=code_a, company_b=code_b,
                        metric=item["metric"], period_year=int(year),
                        operator=comp_op, draft=draft,
                        report_kind=item.get("period_kind") if item.get("period_kind") in PERIOD_KINDS else None,
                        **period_fields)
                except (ValueError, ArithmeticError, KeyError, TypeError) as exc:
                    output.update(execution_failed=True, error_type=type(exc).__name__)
                    return stop("tool_execution_failed",
                                f"跨公司比较工具未能完成：{type(exc).__name__}: {exc}", "证据不足")
                if result.get("verdict") in ("evidence_supported", "confirmed_error"):
                    output.update(
                        status="证据支持" if result["verdict"] == "evidence_supported" else "确认错误",
                        track="deterministic", reason_code=result.get("reason_code"),
                        reason=result.get("reason"),
                        calculation=result.get("calculation"),
                        expected=result.get("expected"),
                        evidence_ids=result.get("evidence_ids") or [])
                    if result.get("verdict") == "confirmed_error":
                        output["suggestion"] = "核对两侧公司与期间后修改比较方向或数值，并引用所列证据。"
                    _hydrate_evidence(output, facts)
                    return output
                if result.get("verdict") == "needs_review":
                    return stop(result.get("reason_code") or "comparison_needs_review",
                                result.get("reason") or "跨公司比较缺少证据", "证据不足")
        return model_note("cross_company_comparison",
                          "跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留")

    if item.get("claim_type") == "qualitative":
        return model_note("qualitative_statement",
                          "定性陈述：本阶段不做数值裁决，解释见 plain_claim")

    subject = bind_company(item.get("company_name") or "")
    year = item.get("period_year")
    output["grounding"] = {
        "company": subject,
        "period": bind_period(year),
    }
    output["resolution"] = resolution_for_subject(
        subject, source_text=source + "\n" + draft, facts=facts)
    if subject.get("conflict"):
        output["interpretation"]["model_company_conflict"] = True
    if not subject.get("code"):
        if subject.get("basis") == "ambiguous_sentence_companies":
            return stop("ambiguous_company_in_sentence",
                        "同一句出现多家公司，无法确定主张主体；请拆分句子或人工复核", "证据不足")
        return stop("unresolved_company", "原文公司名称/代码无法对应到本次已加载材料，或无法在草稿中定位，不猜测公司",
                    "证据不足")
    code = subject["code"]
    if output["grounding"]["period"].get("conflict"):
        binding = output["grounding"]["period"]
        mark_conflict(output["resolution"], status="period_conflict", field="period_year",
                      source_value=binding.get("year"), model_value=item.get("period_year"),
                      basis=binding.get("located_in") or "unknown",
                      source_text=binding.get("matched_text") or sentence,
                      action_code="confirm_period", action_message="按原文年度修正期间后重跑")
        return stop("model_parse_conflict", "模型年度与本句/引文明示年度不一致")
    year = output["grounding"]["period"].get("year")
    if year is None or not output["grounding"]["period"].get("located_in"):
        mark_conflict(output["resolution"], status="period_conflict", field="period_year",
                      source_value=None, model_value=item.get("period_year"), basis="missing",
                      source_text=sentence, action_code="confirm_period",
                      action_message="补全年份或选择明确期间后重跑")
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
    explicit_scope = source_scope(source)
    if (explicit_scope and not scope_agnostic(metric, item.get("unit"))
            and item.get("scope") != explicit_scope["scope"]):
        mark_conflict(output["resolution"], status="scope_conflict", field="scope",
                      source_value=explicit_scope,
                      model_value=item.get("scope"), basis="source_text",
                      source_text=explicit_scope["matched"], action_code="confirm_scope",
                      action_message="按原文归母/合并/母公司口径修正后重跑，或转人工复核")
        return stop("model_parse_conflict", "模型统计口径与原文的归母/合并/母公司表达不一致")
    period_binding = output["grounding"]["period"]
    period_fields = _precise_period({**item, "period_year": year},
                                    period_binding.get("current_text") or quote + "\n" + sentence)
    if period_fields.get("conflict"):
        mark_conflict(output["resolution"], status="period_conflict", field="period_kind",
                      source_value=period_binding.get("current_text") or sentence,
                      model_value=item.get("period_kind"), basis="source_text",
                      source_text=period_binding.get("current_text") or sentence,
                      action_code="confirm_period", action_message="按原文半年/季度/年度分类修正后重跑")
        return stop("model_parse_conflict", "模型期间分类与原文的半年/单季/累计表达不一致")
    if period_fields.get("unsupported"):
        mark_conflict(output["resolution"], status="period_conflict", field="period_start",
                      source_value=None, model_value=item.get("period_kind"), basis="missing",
                      source_text=quote + "\n" + sentence, action_code="confirm_period",
                      action_message="补全季度/半年的明确起止后重跑，不退回全年值")
        return stop("unsupported_period", "季度/半年期间没有明确起止，不能与全年值混比")
    if item.get("claim_type") == "direction" and (item.get("value") is None
                                                 or (item.get("operator") or "eq") == "none"):
        return _direction_row(output, item, facts, code=code, metric=metric, year=year,
                              sentence=sentence, quote=quote, period_fields=period_fields)

    operator = item.get("operator") or "eq"
    if operator not in OPERATORS:
        return stop("invalid_operator", f"未知运算符 {operator}")
    if operator == "none":
        return model_note("no_numeric_constraint", "无数值约束的陈述，不进数值裁决")
    if operator == "eq" and (item.get("qualifier") not in {"exact", None} or FUZZY.search(quote)):
        return stop("non_exact_claim",
                    "含约数或不等表达，但未给运算符；请标 operator=approx/exceed/… 或改写为精确数")

    raw_value, raw_unit = item["value"], item["unit"]
    source_units = unit_candidates(quote, raw_value)
    normalized_model_unit = UNITS.get(raw_unit, raw_unit)
    normalized_source_units = {UNITS.get(row["unit"], row["unit"]) for row in source_units}
    if source_units and normalized_model_unit not in normalized_source_units:
        mark_conflict(output["resolution"], status="unit_conflict", field="unit",
                      source_value=source_units, model_value=raw_unit, basis="source_number_unit",
                      source_text=quote, action_code="confirm_unit",
                      action_message="按原文数值直接对应的单位修正后重跑，不做默认换算")
        return stop("ungrounded_unit", "模型单位未与原文同一数值直接对应；拒绝按默认倍率猜测")
    if raw_value is None or raw_unit not in UNITS or raw_unit not in quote:
        if not source_units and output["resolution"].get("status") == "unique":
            output["resolution"].update(status="candidates", label="候选待定",
                                         next_action={"code": "confirm_unit",
                                                      "message": "补全并核对数值单位后重跑"})
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
    if kind == "yoy" and period_binding.get("baseline_year") not in {None, year - 1}:
        return stop("unsupported_comparison_period", "原文明示的比较基期不是上年，不能替换成同比基期", "证据不足")
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
             "currency": item["currency"], "scope": item["scope"], "period_kind": item["period_kind"],
             "report_kind": item["period_kind"] if item["period_kind"] in PERIOD_KINDS else None,
             **period_fields, "operator": operator, "tolerance_pct": item.get("tolerance_pct")}
    try:
        checked = check_claim(claim, facts)
    except (ValueError, ArithmeticError):
        return stop("calculation_unavailable", "现有计算规则无法安全处理该数值，需人工复核")
    output.update(checked, normalized_claim=claim, reason_code="deterministic_check",
                  track="deterministic")
    if checked["status"] == "确认错误":
        output["expected"] = checked["calculation"]["value"]
    _hydrate_evidence(output, facts)
    return output


def _paragraph_of(draft: str, sentence: str) -> str:
    """本句所在的自然段；用于段落主体绑定，不跨段借用其他公司或年度。"""
    start = draft.find(sentence) if sentence else -1
    if start < 0:
        return sentence
    left = draft.rfind("\n", 0, start)
    right = draft.find("\n", start + len(sentence))
    return draft[left + 1: right if right >= 0 else len(draft)]


def _preceding_text(paragraph: str, sentence: str) -> str:
    """同段里本句之前的文字；只在“唯一可定位”时才用于继承。"""
    position = paragraph.find(sentence) if sentence else -1
    return paragraph[:position] if position > 0 else ""


def _precise_period(item: dict, source: str) -> dict:
    """把半年/单季/累计表达转成明确起止。

    返回 {"unsupported": True} 表示原文没有可定位的起止；返回 {"conflict": True}
    表示模型期间分类与原文表达冲突；年度精确陈述返回空字典。
    """
    if item["period_kind"] == "annual" and not re.search(r"季度|半年|前三季|Q[1-4]", source, re.I):
        return {}
    from periods import end_date

    half = re.search(r"半年|半年度", source)
    first_half = re.search(r"上半年", source)
    second_half = re.search(r"下半年", source)
    quarter = re.search(r"(?:第?([一二三四1234])季度|Q([1-4]))", source, re.I)
    cumulative = re.search(r"前三季|年初至", source)
    if half or first_half:
        expected_kind, month, start_month = ("half", 12, 7) if second_half else ("half", 6, 1)
        basis = half.group(0) if half else first_half.group(0)
    elif quarter or cumulative:
        if quarter and quarter.group(2):
            number = int(quarter.group(2))
        elif quarter:
            digit = quarter.group(1)
            number = int(digit) if digit.isdigit() else {"一": 1, "二": 2, "三": 3, "四": 4}[digit]
        else:
            number = 3
        expected_kind, month = "quarter", number * 3
        start_month = 1 if cumulative else number * 3 - 2
        basis = quarter.group(0) if quarter else cumulative.group(0)
    else:
        return {"unsupported": True}
    if item["period_kind"] != expected_kind:
        return {"conflict": True}
    year = int(item["period_year"])
    return {"period_start": f"{year}-{start_month:02d}-01", "period_end": end_date(year, month),
            "period_basis": basis}


def _period_filters(period_fields: dict | None) -> dict:
    """已通过门控的精确期间参数；冲突必须由调用方先拦截。"""
    if not period_fields or period_fields.get("unsupported") or period_fields.get("conflict"):
        return {}
    return {key: period_fields[key] for key in ("period_start", "period_end") if period_fields.get(key)}


def _direction_row(output: dict, item: dict, facts: list[dict], *, code: str, metric: str,
                   year, sentence: str, quote: str, period_fields: dict) -> dict:
    """无数字的增长/下降主张：复用已有证据与增长计算判方向，拿不到证据就明确弃答。"""
    from audit.tools.evidence import find_evidence

    def stop(code_, reason, status="证据不足", track="review"):
        output.update(reason_code=code_, reason=reason, status=status, track=track)
        return output

    report_kind = item.get("period_kind") if item.get("period_kind") in PERIOD_KINDS else None
    located = find_evidence(facts, company_name_or_code=code, metric=metric, period_year=int(year),
                            report_kind=report_kind,
                            period_start=period_fields.get("period_start"),
                            period_end=period_fields.get("period_end"))
    if located.get("status") != "ok":
        return stop("direction_evidence_unavailable",
                    "方向核查缺少唯一的公司/指标/期间证据（" + str(located.get("status")) + "），不凭语气判断")
    index = {fact.get("evidence_id"): fact for fact in facts}
    fact = index.get(located["items"][0]["evidence_id"])
    binding = output.get("grounding", {}).get("period", {})
    baseline_year = binding.get("baseline_year")
    if baseline_year is not None:
        baseline_fields = _precise_period({**item, "period_year": baseline_year}, binding["baseline_text"])
        if baseline_fields.get("unsupported") or baseline_fields.get("conflict"):
            return stop("direction_not_comparable", "比较基期的期间类型与本期不一致或无法定位")
        if bool(baseline_fields) != bool(period_fields) or any(
                baseline_fields.get(k, "")[4:] != period_fields.get(k, "")[4:]
                for k in ("period_start", "period_end")):
            return stop("direction_not_comparable", "原文明示的本期与比较基期不是相同期间")
    previous = _comparable_previous(facts, fact, baseline_year)
    if previous is None:
        return stop("direction_base_missing", "方向核查缺少唯一可比的上期数值，不能凭语气判断")
    stated_base = (output.get("grounding", {}).get("period") or {}).get("comparison_year")
    if stated_base is not None and stated_base != previous.get("period_year"):
        return stop("direction_base_mismatch", "原文明示比较年度与可用上年基期不同，不能替换比较对象")
    computation = evidence_yoy(fact, previous)
    if computation.get("status") != "ok":
        reasons = computation.get("reasons") or [computation.get("reason") or "期间不可比"]
        return stop("direction_not_comparable", "方向核查的期间不可比：" + "、".join(str(r) for r in reasons))
    actual = _direction_of(computation["value"])
    claimed = claimed_direction(item, quote)
    output.update(calculation=computation, evidence_ids=computation.get("evidence_ids") or [])
    if claimed is None:
        output["interpretation"]["computed_direction"] = actual
        return stop("direction_not_stated",
                    "原文没有明确方向词，仅保留可计算的方向：" + DIRECTION_WORDS.get(actual, actual),
                    "模型判断", "model")
    if claimed == actual:
        output.update(status="证据支持", track="deterministic", reason_code="direction_supported",
                      reason=f"可比期间计算方向为「{DIRECTION_WORDS[actual]}」，与陈述一致")
        _hydrate_evidence(output, facts)
        return output
    output.update(status="确认错误", track="deterministic", reason_code="direction_mismatch",
                  reason=f"可比期间计算方向为「{DIRECTION_WORDS[actual]}」，与陈述的「{DIRECTION_WORDS[claimed]}」不一致",
                  expected=DIRECTION_WORDS[actual],
                  suggestion=f"将方向由「{DIRECTION_WORDS[claimed]}」改为「{DIRECTION_WORDS[actual]}」，并引用所列证据。")
    before, after = DIRECTION_WORDS[claimed], DIRECTION_WORDS[actual]
    revised, count = re.subn(re.escape(before), after, sentence, count=1)
    output["correction"] = {"field": metric, "before": before, "after": after,
                            "original_sentence": sentence,
                            "revised_sentence": revised if count else None,
                            "evidence_ids": output["evidence_ids"], "page": fact.get("page"),
                            "apply_automatically": bool(count),
                            "condition": "只替换已核实的方向词；其他表述需人工编辑"}
    _hydrate_evidence(output, facts)
    return output


def check_payload(payload: dict, sentences: list[dict], draft: str, facts: list[dict], *,
                  completed_atom_keys: set[str] | None = None) -> list[dict]:
    validate_schema(payload, schema())
    sentence_map = {row["sentence_id"]: row["text"] for row in sentences}
    used = {item["sentence_id"] for item in payload["items"]}
    unused = payload["unclaimed_sentences"]
    if (used | set(unused) != set(sentence_map) or used & set(unused) or len(set(unused)) != len(unused)):
        raise LLMError("模型遗漏、重复标记或伪造句子ID；本次未判定，请分段重试")
    results = []
    for i, item in enumerate(payload["items"], 1):
        if _atom_key(item) in (completed_atom_keys or set()):
            continue
        try:
            result = check_one_claim(item, facts, sentence_map[item["sentence_id"]], draft, f"C{i}")
        except Exception as exc:  # 单项执行失败保留相邻已经完成的原子结果，不伪装成正常缺证。
            result = {"claim_id": f"C{i}", "atom_key": _atom_key(item),
                      "original_sentence": sentence_map[item["sentence_id"]],
                      "quote": item["quote"], "status": "证据不足", "track": "review",
                      "reason_code": "tool_execution_failed", "execution_failed": True,
                      "error_type": type(exc).__name__, "reason": f"{type(exc).__name__}: {exc}",
                      "evidence_ids": [], "evidence": [], "calculation": None,
                      "expected": None, "suggestion": None, "plain_claim": item.get("plain_claim"),
                      "interpretation": {"claim_type": item.get("claim_type")}}
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
                    "value", "unit", "period_year", "period_kind", "document_id",
                    "scope", "value_bbox")})
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
