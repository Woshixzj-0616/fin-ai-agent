"""DeepSeek tool loop and cashflow-specific source/analysis tools."""

from __future__ import annotations

import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from backend.core.context import ReportContext

from .calculations import calculate
from .facts import build_fact


MODULE_ID = "cashflow"
MODULE_VERSION = "cashflow-v1.1"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "现金流与利润兑现_v1.md"
MAX_SEARCH_HITS = 8
MAX_PAGE_CHARS = 3600
MAX_TOOL_ROUNDS = max(4, min(int(os.getenv("CASHFLOW_MAX_TOOL_ROUNDS", "14")), 24))
FINALIZATION_ROUNDS = 8
FINALIZATION_CALCULATION_ROUNDS = 2
FINALIZATION_REPAIR_CALCULATION_OFFSET = 4
FINALIZATION_CALCULATION_LIMIT = 8
FINALIZATION_REPAIR_CALCULATION_LIMIT = 4
MAX_FINAL_CONTEXT_FACTS = 320
MAX_FINAL_OUTPUT_TOKENS = 6000
MONEY_AMOUNT_PATTERN = re.compile(
    r"(?P<number>[+-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?)\s*"
    r"(?P<unit>万亿元|亿元|万元|千元|人民币元|元|万亿|亿)"
)
MONEY_UNIT_SCALE = {
    "万亿元": Decimal("1000000000000"),
    "万亿": Decimal("1000000000000"),
    "亿元": Decimal("100000000"),
    "亿": Decimal("100000000"),
    "万元": Decimal("10000"),
    "千元": Decimal("1000"),
    "人民币元": Decimal("1"),
    "元": Decimal("1"),
}

CASH_TERMS = {
    "现金流量表补充资料": 48,
    "将净利润调节为经营活动现金流量": 44,
    "经营活动产生的现金流量净额": 38,
    "投资活动产生的现金流量净额": 36,
    "筹资活动产生的现金流量净额": 36,
    "现金及现金等价物净增加额": 38,
    "现金及现金等价物": 18,
    "汇率变动对现金的影响": 28,
    "经营活动现金流量": 14,
    "投资活动现金流量": 14,
    "筹资活动现金流量": 14,
    "销售商品、提供劳务收到的现金": 16,
    "购买商品、接受劳务支付的现金": 16,
    "支付给职工": 12,
    "购建固定资产、无形资产和其他长期资产支付的现金": 20,
    "取得借款收到的现金": 12,
    "偿还债务支付的现金": 12,
    "货币资金": 4,
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_cashflow_pages",
            "description": "在年报全文中搜索现金流量表、补充资料、现金构成和现金相关附注。返回有限数量的原文页片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查找的准确项目名或现金问题关键词"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_cashflow_pages",
            "description": "读取指定 PDF 物理页，可选连续上下页以查看表格表头、续表和单位。",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "minimum": 1},
                    "radius": {"type": "integer", "minimum": 0, "maximum": 2},
                },
                "required": ["page"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_cashflow_facts",
            "description": "登记年报现金流数字、期间/范围/单位和一条或多条原文证据。只登记报告实际披露的原值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "facts": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 80,
                        "items": {
                            "type": "object",
                            "properties": {
                                "metric_key": {"type": "string"},
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "unit": {"type": "string"},
                                "currency": {"type": "string"},
                                "period": {"type": "string"},
                                "period_kind": {"type": "string", "enum": ["period_amount", "instant_amount", "ratio", "unknown"]},
                                "scope": {"type": "string"},
                                "dimensions": {"type": "string"},
                                "measurement_basis": {"type": "string"},
                                "source_table": {"type": "string"},
                                "evidence": {
                                    "type": "array",
                                    "maxItems": 6,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "page": {"type": "integer", "minimum": 1},
                                            "quote": {"type": "string"},
                                            "table_label": {"type": "string"},
                                            "row_label": {"type": "string"},
                                            "column_label": {"type": "string"},
                                        },
                                        "required": ["page", "quote"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["metric_key", "label", "value", "unit", "currency", "period", "period_kind", "scope", "source_table", "evidence"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["facts"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_cashflow",
            "description": "从已登记事实 ID 进行精确十进制运算。net_by_direction 必须用 inflow_fact_ids 和 outflow_fact_ids 分开传入，不能把两类合并到 fact_ids。profit_to_cash 的 fact_ids 按净利润、带符号调节项目、主表经营现金净额排列；yoy_bridge 按本期/上期成对提供净利润、每项调节行，最后一对为本期/上期经营现金净额，计算同比贡献并核对分项是否完整解释总变动；cash_bridge 按同一期间的经营、投资、筹资、汇率影响、披露净增加排列；cash_balance_bridge 按期末时点余额、期初时点余额、年度现金净增加排列；operating_after_capex 按经营现金净额、长期资产购建支出排列。角色键使用事实中的 metric_key；例如现金流主表常见键为 operating_net_cash、investing_net_cash、financing_net_cash、fx_effect、net_increase_cash，现金余额常见键为 ending_cash_equiv、beginning_cash_equiv，利润调节表净利润常见键为 recon_net_profit，长期资产购建现金支出常见键为 capex_assets、purchase_long_term_assets、invest_capex。期间、范围或单位不对应时会拒绝计算；缺失不能当零。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "operation": {
                        "type": "string",
                        "enum": ["sum", "difference", "ratio", "yoy", "yoy_bridge", "net_by_direction", "cash_bridge", "cash_balance_bridge", "profit_to_cash", "operating_after_capex"],
                    },
                    "fact_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 80},
                    "inflow_fact_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 80},
                    "outflow_fact_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 80},
                },
                "required": ["name", "operation"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_cashflow_analysis",
            "description": "分批保存或完成模块四结果。sections 可一次提交多个板块，也可只提交需要修正的板块；通过校验的板块会单独保存。五个板块都保存后，再用空 sections 提交总评完成汇总。每个关键金额必须关联本板块自己的事实或计算编号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "report_year": {"type": "string"},
                    "overall_view": {"type": "string"},
                    "overall_fact_ids": {"type": "array", "items": {"type": "string"}},
                    "overall_calculation_ids": {"type": "array", "items": {"type": "string"}},
                    "overall_source_pages": {"type": "array", "items": {"type": "integer"}},
                    "sections": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string", "enum": ["cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash"]},
                                "title": {"type": "string"},
                                "status": {"type": "string", "enum": ["analyzed", "insufficient_evidence", "not_applicable", "needs_review"]},
                                "summary": {"type": "string"},
                                "detail": {"type": "string"},
                                "fact_ids": {"type": "array", "items": {"type": "string"}},
                                "calculation_ids": {"type": "array", "items": {"type": "string"}},
                                "source_pages": {"type": "array", "items": {"type": "integer"}},
                                "tables": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                            },
                            "required": ["key", "title", "status", "summary", "detail", "fact_ids", "calculation_ids", "source_pages"],
                            "additionalProperties": True,
                        },
                    },
                    "topics": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                    "limitations": {"type": "array", "items": {"type": "string"}},
                    "follow_up_questions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["company", "report_year", "overall_view", "overall_fact_ids", "overall_calculation_ids", "overall_source_pages", "sections", "topics", "limitations", "follow_up_questions"],
                "additionalProperties": False,
            },
        },
    },
]


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def _numeric_values(value: Any) -> list[Decimal]:
    if isinstance(value, dict):
        output: list[Decimal] = []
        for key, item in value.items():
            if key in {"fact_ids", "label", "metric_key", "instruction", "formula", "name"}:
                continue
            output.extend(_numeric_values(item))
        return output
    if isinstance(value, list):
        return [number for item in value for number in _numeric_values(item)]
    if isinstance(value, bool) or value is None:
        return []
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return []
    return [number] if number.is_finite() else []


def _supported_money_amounts(
    fact_ids: set[str],
    calculation_ids: set[str],
    state: dict[str, Any],
) -> list[Decimal]:
    supported: list[Decimal] = []
    for fact_id in fact_ids:
        fact = state["facts"].get(fact_id, {})
        if fact.get("normalized_unit") != "CNY_yuan":
            continue
        if not any(
            ref.get("page_valid") and ref.get("quote_matches_page") and ref.get("number_appears_in_quote")
            for ref in fact.get("evidence", [])
        ):
            continue
        supported.extend(_numeric_values(fact.get("normalized_value")))
    for calculation_id in calculation_ids:
        calculation = state["calculations"].get(calculation_id, {})
        if calculation.get("unit") == "CNY_yuan":
            supported.extend(_numeric_values(calculation.get("value")))
            supported.extend(_numeric_values(calculation.get("details", {})))
            continue
        # A yoy calculation reports its rate as the primary value, while the
        # absolute currency change is explicitly recorded in its details.
        if calculation.get("operation") == "yoy" and calculation.get("unit") == "ratio":
            source_facts = [
                state["facts"].get(str(fact_id), {})
                for fact_id in calculation.get("fact_ids", [])
            ]
            if len(source_facts) >= 2 and all(
                fact.get("normalized_unit") == "CNY_yuan"
                and any(
                    ref.get("page_valid") and ref.get("quote_matches_page") and ref.get("number_appears_in_quote")
                    for ref in fact.get("evidence", [])
                )
                for fact in source_facts
            ):
                supported.extend(_numeric_values(calculation.get("details", {}).get("absolute_change")))
    return supported


def _unmatched_money_claims(
    text: str,
    fact_ids: set[str],
    calculation_ids: set[str],
    state: dict[str, Any],
) -> list[str]:
    supported = _supported_money_amounts(fact_ids, calculation_ids, state)
    unmatched: list[str] = []
    for match in MONEY_AMOUNT_PATTERN.finditer(text):
        amount_text = match.group("number")
        unit = match.group("unit")
        try:
            displayed = Decimal(amount_text.replace(",", "").replace("，", ""))
        except InvalidOperation:
            unmatched.append(match.group(0))
            continue
        scale = MONEY_UNIT_SCALE[unit]
        amount_in_yuan = displayed * scale
        decimal_places = len(amount_text.partition(".")[2])
        tolerance = Decimal("0.5") * (Decimal(10) ** -decimal_places) * scale
        candidates = list(supported)
        nearby_text = text[max(0, match.start() - 16):min(len(text), match.end() + 16)]
        describes_outflow = any(term in nearby_text for term in ("流出", "支付", "支出", "偿还", "付款", "减少", "下降"))
        if amount_in_yuan >= 0 and describes_outflow:
            candidates.extend(abs(candidate) for candidate in supported)
        if not any(abs(amount_in_yuan - candidate) <= tolerance for candidate in candidates):
            unmatched.append(match.group(0))
    return unmatched


def _reference_hints_for_amount(text: str, claim: str, state: dict[str, Any]) -> dict[str, list[str]]:
    """Suggest exact-amount source IDs after a failed citation check; never auto-accept them."""
    match = next((item for item in MONEY_AMOUNT_PATTERN.finditer(text) if item.group(0) == claim), None)
    if match is None:
        return {"candidate_fact_ids": [], "candidate_calculation_ids": []}
    try:
        target = Decimal(match.group("number").replace(",", "").replace("，", "")) * MONEY_UNIT_SCALE[match.group("unit")]
    except (InvalidOperation, KeyError):
        return {"candidate_fact_ids": [], "candidate_calculation_ids": []}
    decimal_places = len(match.group("number").partition(".")[2])
    tolerance = Decimal("0.5") * (Decimal(10) ** -decimal_places) * MONEY_UNIT_SCALE[match.group("unit")]
    nearby_text = text[max(0, match.start() - 16):min(len(text), match.end() + 16)]
    describes_outflow = any(term in nearby_text for term in ("流出", "支付", "支出", "偿还", "付款", "减少", "下降"))

    fact_candidates: list[str] = []
    for fact_id in state["facts"]:
        amounts = _supported_money_amounts({fact_id}, set(), state)
        if any(abs(target - amount) <= tolerance for amount in amounts):
            fact_candidates.append(fact_id)

    calculation_candidates: list[str] = []
    for calculation_id, calculation in state["calculations"].items():
        if calculation.get("status") != "calculated":
            continue
        amounts = _supported_money_amounts(set(), {calculation_id}, state)
        if any(abs(target - amount) <= tolerance for amount in amounts):
            calculation_candidates.append(calculation_id)

    if target >= 0 and describes_outflow:
        for fact_id in state["facts"]:
            if fact_id not in fact_candidates and any(
                abs(target - abs(amount)) <= tolerance
                for amount in _supported_money_amounts({fact_id}, set(), state)
            ):
                fact_candidates.append(fact_id)
        for calculation_id, calculation in state["calculations"].items():
            if calculation.get("status") != "calculated" or calculation_id in calculation_candidates:
                continue
            if any(
                abs(target - abs(amount)) <= tolerance
                for amount in _supported_money_amounts(set(), {calculation_id}, state)
            ):
                calculation_candidates.append(calculation_id)
    return {
        "candidate_fact_ids": fact_candidates[:8],
        "candidate_calculation_ids": calculation_candidates[:8],
    }


def _initial_pages(context: ReportContext, limit: int = 10) -> list[dict[str, Any]]:
    ranked: list[tuple[int, int, str]] = []
    for page in context.pages:
        text = str(page.get("text", ""))
        if not text:
            continue
        score = sum(min(text.count(term), 3) * weight for term, weight in CASH_TERMS.items())
        if "合并现金流量表" in text:
            score += 35
        if "母公司现金流量表" in text:
            score -= 28
        if "将净利润调节为经营活动现金流量" in text:
            score += 36
        ranked.append((score, int(page.get("page", 0)), text))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [item for item in ranked if item[0] > 0][:min(limit, 8)]
    return [{"page": page, "text": text[:2600]} for _, page, text in sorted(selected, key=lambda item: item[1])]


def _system_context(context: ReportContext, seeds: list[dict[str, Any]]) -> str:
    preview = [{"page": item["page"], "text": item["text"]} for item in seeds]
    return _json(
        {
            "report_id": context.report_id,
            "file_name": context.file_name,
            "file_sha256": context.file_sha256,
            "page_count": context.page_count,
            "seed_pages": preview,
            "optional_related_results": context.related_results,
        }
    )


def _page_map(context: ReportContext) -> dict[int, str]:
    return {int(item["page"]): str(item.get("text", "")) for item in context.pages if item.get("page") is not None}


def _save_state(context: ReportContext, state: dict[str, Any], label: str) -> None:
    state["checkpoint"] += 1
    context.save_artifact(f"cashflow_{state['checkpoint']:03d}_{label}", {
        "facts": list(state["facts"].values()),
        "calculations": list(state["calculations"].values()),
        "last_event": label,
    })


def _submit(args: dict[str, Any], state: dict[str, Any], context: ReportContext) -> dict[str, Any]:
    section_order = ("cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash")
    required_keys = set(section_order)
    sections = args.get("sections") if isinstance(args.get("sections"), list) else []
    by_key: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for section in sections:
        key_value = section.get("key") if isinstance(section, dict) else None
        if isinstance(key_value, str) and key_value in required_keys:
            key = key_value
            if key in by_key:
                duplicates.add(key)
            by_key[key] = section
    unexpected = [
        section.get("key") if isinstance(section, dict) else "<invalid>"
        for section in sections
        if not isinstance(section, dict) or not isinstance(section.get("key"), str) or section.get("key") not in required_keys
    ]
    if duplicates or unexpected or len(sections) > len(section_order):
        return {
            "accepted": False,
            "error": "每次提交只能包含不同的五个核心板块；可只提交待修正板块",
            "duplicate_sections": sorted(duplicates),
            "unexpected_sections": unexpected,
        }

    state.setdefault("accepted_sections", {})
    state.setdefault("accepted_section_review_flags", {})
    if by_key:
        state.pop("accepted_overall", None)

    fact_ids = set(state["facts"])
    calculation_ids = set(state["calculations"])
    flags: list[str] = []
    invalid_fact_evidence: dict[str, dict[str, list[str]]] = {}

    def add_flag(message: str) -> None:
        if message not in flags:
            flags.append(message)

    def string_list(values: Any) -> list[str]:
        return [str(value) for value in values] if isinstance(values, list) else []

    def add_fact_references(
        values: list[str], label: str, pages: list[int], errors: list[str] | None = None
    ) -> list[str]:
        errors = errors if errors is not None else []
        valid: list[str] = []
        for fact_id in values:
            fact = state["facts"].get(fact_id)
            if fact is None:
                add_flag(f"{label}: 引用了未知事实编号")
                errors.append("存在无效或未知 fact_id")
                continue
            valid.append(fact_id)
            if fact.get("identity_status") != "verified":
                add_flag(f"{label}: 引用事实的表格行列/期间/范围身份尚未自动核实")
            valid_refs = [
                ref for ref in fact.get("evidence", [])
                if ref.get("page_valid") and ref.get("quote_matches_page") and ref.get("number_appears_in_quote")
            ]
            evidence_problems: list[str] = []
            if not valid_refs:
                add_flag(f"{label}: 有事实没有通过页码、摘录和数字三项来源核对")
                errors.append("有事实没有通过页码、摘录和数字三项来源核对")
                evidence_problems.append("未找到同时通过页码、摘录和数字核对的来源")
            pages.extend(int(ref["page"]) for ref in valid_refs)
            if any(ref.get("row_label_matches_quote") is False for ref in valid_refs):
                add_flag(f"{label}: 有事实的申报行名未出现在引用摘录中")
                errors.append("事实申报的行名未出现在引用摘录中")
                evidence_problems.append("申报行名与引用摘录不匹配")
            if evidence_problems:
                invalid_fact_evidence.setdefault(label, {})[fact_id] = evidence_problems
            if any(ref.get("column_label_matches_page") is False for ref in valid_refs):
                add_flag(f"{label}: 有事实的申报列名未出现在来源页中")
            if any(ref.get("table_label_matches_page") is False for ref in valid_refs):
                add_flag(f"{label}: 有事实的申报表名未出现在来源页中")
        return valid

    def add_calculation_references(
        values: list[str], label: str, pages: list[int], errors: list[str] | None = None
    ) -> list[str]:
        errors = errors if errors is not None else []
        valid: list[str] = []
        for calculation_id in values:
            calculation = state["calculations"].get(calculation_id)
            if calculation is None:
                add_flag(f"{label}: 引用了未知计算编号")
                errors.append("存在无效或未知 calculation_id")
                continue
            valid.append(calculation_id)
            add_fact_references([str(value) for value in calculation.get("fact_ids", [])], label, pages, errors)
        return valid

    def add_submitted_pages(
        values: Any, label: str, pages: list[int], errors: list[str] | None = None
    ) -> None:
        errors = errors if errors is not None else []
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, bool):
                add_flag(f"{label}: 提交了无效页码")
                errors.append("提交了无效页码")
                continue
            try:
                page_number = int(value)
            except (TypeError, ValueError):
                add_flag(f"{label}: 提交了无效页码")
                errors.append("提交了无效页码")
                continue
            if not 1 <= page_number <= context.page_count:
                add_flag(f"{label}: 页码超出报告范围")
                errors.append("页码超出报告范围")
            elif page_number not in state["observed_pages"]:
                add_flag(f"{label}: 第 {page_number} 页未由本次工具查阅，不能作为已阅读出处")
                errors.append("引用了本次分析没有读取的页码")
            else:
                pages.append(page_number)

    clean_sections: list[dict[str, Any]] = []
    section_errors: dict[str, list[str]] = {}
    unmatched_amount_claims: list[dict[str, str]] = []
    sections_missing_references: list[str] = []
    invalid_references: dict[str, Any] = {
        "sections": {}, "topics": [], "overall": {}, "fact_evidence": {},
    }
    checked_amount_claim_count = 0
    used_fact_ids: set[str] = set()
    used_calculation_ids: set[str] = set()
    for key, section in by_key.items():
        flag_start = len(flags)
        section_errors[key] = []
        cited_facts = string_list(section.get("fact_ids"))
        cited_calcs = string_list(section.get("calculation_ids"))
        pages: list[int] = []
        valid_facts = add_fact_references(cited_facts, str(section.get("key", "板块")), pages, section_errors[key])
        valid_calcs = add_calculation_references(cited_calcs, str(section.get("key", "板块")), pages, section_errors[key])
        if len(valid_facts) != len(cited_facts):
            section_errors[key].append("存在无效或未知 fact_id")
            invalid_references["sections"][key] = {
                **invalid_references["sections"].get(key, {}),
                "fact_ids": [value for value in cited_facts if value not in valid_facts],
            }
        if len(valid_calcs) != len(cited_calcs):
            section_errors[key].append("存在无效或未知 calculation_id")
            invalid_references["sections"][key] = {
                **invalid_references["sections"].get(key, {}),
                "calculation_ids": [value for value in cited_calcs if value not in valid_calcs],
            }
        section_source_fact_ids = set(valid_facts)
        for calculation_id in valid_calcs:
            section_source_fact_ids.update(state["calculations"][calculation_id].get("fact_ids", []))
        section_text = f"{section.get('summary', '')}\n{section.get('detail', '')}"
        amount_claims = list(MONEY_AMOUNT_PATTERN.finditer(section_text))
        checked_amount_claim_count += len(amount_claims)
        if amount_claims and not (valid_facts or valid_calcs):
            sections_missing_references.append(key)
            section_errors[key].append("板块写有金额，但没有关联本板块事实或计算编号")
        unmatched_in_section = _unmatched_money_claims(section_text, section_source_fact_ids, set(valid_calcs), state)
        for claim in unmatched_in_section:
            unmatched_amount_claims.append({
                "section": key,
                "amount": claim,
                **_reference_hints_for_amount(section_text, claim, state),
            })
        if unmatched_in_section:
            section_errors[key].append("部分金额无法与本板块引用来源核对")
        add_submitted_pages(section.get("source_pages", []), str(section.get("key", "板块")), pages, section_errors[key])
        section_errors[key] = list(dict.fromkeys(section_errors[key]))
        pages = sorted(set(pages))
        if section.get("status") == "analyzed" and not (valid_facts or valid_calcs or pages):
            message = f"{section.get('key', '板块')}: 标记为已分析，但没有关联事实、计算或已读取页码"
            add_flag(message)
            section_errors[key].append("已分析板块没有关联可追溯来源")
        used_fact_ids.update(valid_facts)
        used_calculation_ids.update(valid_calcs)
        for calculation_id in valid_calcs:
            used_fact_ids.update(
                value for value in state["calculations"][calculation_id].get("fact_ids", [])
                if value in fact_ids
            )
        clean_sections.append({
            **section,
            "fact_ids": valid_facts,
            "calculation_ids": valid_calcs,
            "source_pages": pages,
        })
        if not section_errors[key]:
            state["accepted_sections"][key] = clean_sections[-1]
            state["accepted_section_review_flags"][key] = flags[flag_start:]
        else:
            state["accepted_sections"].pop(key, None)
            state["accepted_section_review_flags"].pop(key, None)

    clean_topics: list[dict[str, Any]] = []
    topic_errors: list[str] = []
    for topic in args.get("topics", []) if isinstance(args.get("topics"), list) else []:
        if not isinstance(topic, dict):
            continue
        topic_facts = string_list(topic.get("fact_ids"))
        topic_calcs = string_list(topic.get("calculation_ids"))
        topic_label = f"额外专题“{str(topic.get('title', '未命名'))[:80]}”"
        topic_pages: list[int] = []
        topic_errors_before = len(topic_errors)
        valid_topic_facts = add_fact_references(topic_facts, topic_label, topic_pages, topic_errors)
        valid_topic_calcs = add_calculation_references(topic_calcs, topic_label, topic_pages, topic_errors)
        invalid_topic_refs = {
            "title": str(topic.get("title", "未命名"))[:80],
            "fact_ids": [value for value in topic_facts if value not in valid_topic_facts],
            "calculation_ids": [value for value in topic_calcs if value not in valid_topic_calcs],
        }
        if invalid_topic_refs["fact_ids"] or invalid_topic_refs["calculation_ids"]:
            invalid_references["topics"].append(invalid_topic_refs)
        topic_source_fact_ids = set(valid_topic_facts)
        for calculation_id in valid_topic_calcs:
            topic_source_fact_ids.update(state["calculations"][calculation_id].get("fact_ids", []))
        topic_text = " ".join(str(topic.get(field, "")) for field in ("title", "summary", "detail", "content"))
        topic_claims = list(MONEY_AMOUNT_PATTERN.finditer(topic_text))
        checked_amount_claim_count += len(topic_claims)
        unmatched_topic = _unmatched_money_claims(topic_text, topic_source_fact_ids, set(valid_topic_calcs), state)
        if unmatched_topic:
            topic_errors.append(f"{topic_label}: 有金额无法与引用来源核对")
        add_submitted_pages(topic.get("source_pages", []), topic_label, topic_pages, topic_errors)
        for claim in unmatched_topic:
            unmatched_amount_claims.append({
                "section": topic_label,
                "amount": claim,
                **_reference_hints_for_amount(topic_text, claim, state),
            })
        if len(valid_topic_facts) != len(topic_facts) and len(topic_errors) == topic_errors_before:
            topic_errors.append(f"{topic_label}: 存在无效或未知 fact_id")
        if len(valid_topic_calcs) != len(topic_calcs) and len(topic_errors) == topic_errors_before:
            topic_errors.append(f"{topic_label}: 存在无效或未知 calculation_id")
        used_fact_ids.update(valid_topic_facts)
        used_calculation_ids.update(valid_topic_calcs)
        for calculation_id in valid_topic_calcs:
            used_fact_ids.update(
                value for value in state["calculations"][calculation_id].get("fact_ids", [])
                if value in fact_ids
            )
        clean_topics.append({
            **topic,
            "fact_ids": valid_topic_facts,
            "calculation_ids": valid_topic_calcs,
            "source_pages": sorted(set(topic_pages)),
        })

    # The overview is composed from the accepted boards, so it inherits their
    # evidence when the model does not repeat the same long ID lists.
    overall_facts = string_list(args.get("overall_fact_ids"))
    overall_calculations = string_list(args.get("overall_calculation_ids"))
    for accepted_section in state["accepted_sections"].values():
        overall_facts.extend(string_list(accepted_section.get("fact_ids")))
        overall_calculations.extend(string_list(accepted_section.get("calculation_ids")))
    overall_facts = list(dict.fromkeys(overall_facts))
    overall_calculations = list(dict.fromkeys(overall_calculations))
    overall_pages: list[int] = []
    overall_errors: list[str] = []
    clean_overall_facts = add_fact_references(overall_facts, "总评", overall_pages, overall_errors)
    clean_overall_calculations = add_calculation_references(overall_calculations, "总评", overall_pages, overall_errors)
    overall_source_fact_ids = set(clean_overall_facts)
    for calculation_id in clean_overall_calculations:
        overall_source_fact_ids.update(state["calculations"][calculation_id].get("fact_ids", []))
    overall_text = str(args.get("overall_view", ""))
    invalid_references["overall"] = {
        "fact_ids": [value for value in overall_facts if value not in clean_overall_facts],
        "calculation_ids": [value for value in overall_calculations if value not in clean_overall_calculations],
    }
    overall_claims = list(MONEY_AMOUNT_PATTERN.finditer(overall_text))
    checked_amount_claim_count += len(overall_claims)
    unmatched_overall = _unmatched_money_claims(overall_text, overall_source_fact_ids, set(clean_overall_calculations), state)
    for claim in unmatched_overall:
        unmatched_amount_claims.append({
            "section": "总评",
            "amount": claim,
            **_reference_hints_for_amount(overall_text, claim, state),
        })
    add_submitted_pages(args.get("overall_source_pages", []), "总评", overall_pages, overall_errors)
    if unmatched_overall:
        overall_errors.append("总评中存在无法与已通过板块引用核对的金额")
    invalid_references["fact_evidence"] = invalid_fact_evidence
    used_fact_ids.update(clean_overall_facts)
    used_calculation_ids.update(clean_overall_calculations)
    for calculation_id in clean_overall_calculations:
        used_fact_ids.update(
            value for value in state["calculations"][calculation_id].get("fact_ids", [])
            if value in fact_ids
        )
    has_section_errors = any(section_errors.values())
    if unmatched_amount_claims or has_section_errors or topic_errors or overall_errors:
        bounded_claims = unmatched_amount_claims[:20]
        if unmatched_amount_claims:
            context.recorder.record(
                "cashflow_amount_claim_rejected",
                mismatch_count=len(unmatched_amount_claims),
                checked_count=checked_amount_claim_count,
            )
        if has_section_errors:
            context.recorder.record(
                "cashflow_sections_rejected",
                sections={key: values for key, values in section_errors.items() if values},
                accepted_sections=[key for key in section_order if key in state["accepted_sections"]],
            )
        state["last_rejected_submission"] = {
            "args": args,
            "unmatched_amount_claims": bounded_claims,
            "unmatched_amount_claim_count": len(unmatched_amount_claims),
            "sections_missing_references": sections_missing_references,
            "section_errors": section_errors,
            "topic_errors": topic_errors,
            "overall_errors": overall_errors,
            "invalid_references": invalid_references,
            "checked_amount_claim_count": checked_amount_claim_count,
        }
        context.save_artifact("cashflow_rejected_submission", state["last_rejected_submission"])
        return {
            "accepted": False,
            "error": "部分板块或金额未通过校验。已通过的板块已保存；只需修正返回错误的板块，再次提交这些板块即可。每个板块要引用自己的事实/计算；同比差额或小计应先调用 calculate_cashflow。",
            "unmatched_amount_claims": bounded_claims,
            "sections_missing_references": sections_missing_references,
            "section_errors": {key: values for key, values in section_errors.items() if values},
            "topic_errors": topic_errors,
            "overall_errors": overall_errors,
            "invalid_references": invalid_references,
            "accepted_sections": [key for key in section_order if key in state["accepted_sections"]],
            "pending_sections": [key for key in section_order if key not in state["accepted_sections"]],
            "checked_amount_claim_count": checked_amount_claim_count,
        }

    state.pop("last_rejected_submission", None)
    state.pop("finalization_feedback", None)

    pending_sections = [key for key in section_order if key not in state["accepted_sections"]]
    if pending_sections:
        return {
            "accepted": False,
            "message": "已保存通过校验的板块，请只提交其余板块；五个板块齐全后再提交总评。",
            "accepted_sections": [key for key in section_order if key in state["accepted_sections"]],
            "pending_sections": pending_sections,
        }
    if not overall_text.strip():
        return {
            "accepted": False,
            "message": "五个板块已保存。现在请提交总评；总评引用会自动继承各板块已通过校验的事实和计算。",
            "accepted_sections": list(section_order),
            "pending_sections": [],
        }

    state["accepted_overall"] = {
        "company": str(args.get("company", ""))[:240],
        "report_year": str(args.get("report_year", ""))[:40],
        "overall_view": overall_text,
        "overall_fact_ids": clean_overall_facts,
        "overall_calculation_ids": clean_overall_calculations,
        "overall_source_pages": sorted(set(overall_pages)),
        "topics": clean_topics,
        "limitations": args.get("limitations", []) if isinstance(args.get("limitations"), list) else [],
        "follow_up_questions": args.get("follow_up_questions", []) if isinstance(args.get("follow_up_questions"), list) else [],
        "review_flags": flags,
        "validated_amount_claim_count": checked_amount_claim_count,
    }
    if len(state["accepted_sections"]) != len(section_order):
        return {
            "accepted": False,
            "message": "总评已暂存。请先补齐待提交板块，之后重新提交总评以完成组装。",
            "accepted_sections": [key for key in section_order if key in state["accepted_sections"]],
            "pending_sections": [key for key in section_order if key not in state["accepted_sections"]],
        }

    clean_sections = [state["accepted_sections"][key] for key in section_order]
    flags = [
        message
        for key in section_order
        for message in state["accepted_section_review_flags"].get(key, [])
    ] + list(state["accepted_overall"].get("review_flags", []))
    clean_overall_facts = state["accepted_overall"]["overall_fact_ids"]
    clean_overall_calculations = state["accepted_overall"]["overall_calculation_ids"]
    overall_pages = state["accepted_overall"]["overall_source_pages"]
    used_fact_ids = {value for section in clean_sections for value in section["fact_ids"]}
    used_calculation_ids = {value for section in clean_sections for value in section["calculation_ids"]}
    used_fact_ids.update(clean_overall_facts)
    used_calculation_ids.update(clean_overall_calculations)
    for topic in state["accepted_overall"]["topics"]:
        used_fact_ids.update(value for value in topic["fact_ids"] if value in fact_ids)
        used_calculation_ids.update(value for value in topic["calculation_ids"] if value in calculation_ids)
    for calculation_id in used_calculation_ids:
        used_fact_ids.update(
            value for value in state["calculations"][calculation_id].get("fact_ids", [])
            if value in fact_ids
        )
    result = {
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "report_id": context.report_id,
        "company": state["accepted_overall"]["company"],
        "report_year": state["accepted_overall"]["report_year"],
        "overall_view": state["accepted_overall"]["overall_view"],
        "overall_fact_ids": clean_overall_facts,
        "overall_calculation_ids": clean_overall_calculations,
        "overall_source_pages": sorted(set(overall_pages)),
        "sections": clean_sections,
        "topics": state["accepted_overall"]["topics"],
        "facts": [state["facts"][value] for value in sorted(used_fact_ids)],
        "calculations": [state["calculations"][value] for value in sorted(used_calculation_ids)],
        "limitations": state["accepted_overall"]["limitations"],
        "follow_up_questions": state["accepted_overall"]["follow_up_questions"],
        "review_flags": flags,
        "validated_amount_claim_count": state["accepted_overall"]["validated_amount_claim_count"],
        "analysis_completeness": "complete" if not flags else "complete_with_review_flags",
    }
    context.save_artifact("cashflow_submitted_analysis", result)
    state["final_result"] = result
    return {"accepted": True, "section_count": len(clean_sections), "fact_count": len(result["facts"]), "review_flags": flags}


def _dispatch(
    name: str,
    args: dict[str, Any],
    *,
    context: ReportContext,
    state: dict[str, Any],
    pages_by_number: dict[int, str],
) -> dict[str, Any]:
    if name == "search_cashflow_pages":
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("搜索词不能为空")
        limit = max(1, min(int(args.get("limit", 5)), MAX_SEARCH_HITS))
        hits = context.search_pages(query, limit=limit)[:limit]
        for hit in hits:
            try:
                page_number = int(hit.get("page"))
            except (TypeError, ValueError):
                continue
            page_text = str(hit.get("text", ""))
            if page_text:
                pages_by_number[page_number] = page_text
                state["observed_pages"][page_number] = page_text[:2600]
        return {"query": query, "results": [{**hit, "text": str(hit.get("text", ""))[:2600]} for hit in hits]}

    if name == "read_cashflow_pages":
        center = int(args.get("page", 0))
        radius = max(0, min(int(args.get("radius", 0)), 2))
        if not 1 <= center <= context.page_count:
            raise ValueError(f"物理页码必须在 1 到 {context.page_count} 之间")
        result = []
        for page_number in range(max(1, center - radius), min(context.page_count, center + radius) + 1):
            text = context.read_page(page_number)
            if text is None:
                continue
            pages_by_number[page_number] = text
            state["observed_pages"][page_number] = text[:2600]
            result.append({"page": page_number, "text": text[:MAX_PAGE_CHARS]})
        return {"pages": result}

    if name == "register_cashflow_facts":
        output = []
        errors = []
        for index, item in enumerate(args.get("facts", [])):
            if not isinstance(item, dict):
                continue
            try:
                source_refs = item.get("evidence", [])
                if isinstance(source_refs, list):
                    for ref in source_refs:
                        try:
                            source_page = int(ref.get("page")) if isinstance(ref, dict) else 0
                        except (TypeError, ValueError):
                            continue
                        if 1 <= source_page <= context.page_count:
                            source_text = context.read_page(source_page)
                            if source_text is not None:
                                pages_by_number[source_page] = source_text
                                state["observed_pages"][source_page] = source_text[:2600]
                fact = build_fact(item, report_id=context.report_id, pages_by_number=pages_by_number)
            except (ValueError, TypeError) as exc:
                errors.append({"index": index, "error": str(exc)})
                continue
            state["facts"][fact["fact_id"]] = fact
            output.append({
                "fact_id": fact["fact_id"],
                "label": fact["original_label"],
                "period": fact["period"],
                "scope": fact["scope"],
                "value": fact["value"],
                "unit": fact["unit"],
                "evidence_status": fact["evidence_status"],
                "calculable": fact["calculable"],
            })
        _save_state(context, state, "facts")
        return {"registered": output, "errors": errors, "note": "页内数字匹配并不自动证明行列、期间和合并范围正确。"}

    if name == "calculate_cashflow":
        calculated = calculate(args, state["facts"])
        if calculated.get("status") == "calculated":
            calculation_id = "calc_" + uuid.uuid4().hex[:16]
            calculated["calculation_id"] = calculation_id
            state["calculations"][calculation_id] = calculated
            context.record_calculation(
                calculated.get("name", "现金流计算"),
                inputs={
                    "fact_ids": calculated.get("fact_ids", []),
                    "facts": [state["facts"][value] for value in calculated.get("fact_ids", []) if value in state["facts"]],
                },
                formula=calculated.get("formula", ""),
                output=calculated,
                rule_version=calculated.get("rule_version", MODULE_VERSION),
            )
            _save_state(context, state, "calculations")
        return calculated

    if name == "submit_cashflow_analysis":
        return _submit(args, state, context)

    raise ValueError(f"现金流模块不支持工具：{name}")


def _partial_result(state: dict[str, Any], context: ReportContext) -> dict[str, Any]:
    sections = []
    labels = [
        ("cash_overview", "现金全貌"),
        ("profit_to_cash", "利润到经营现金"),
        ("operating_cash", "经营现金收付"),
        ("investment_cash", "投资现金"),
        ("financing_cash", "筹资与现金余额"),
    ]
    rejected = state.get("last_rejected_submission")
    accepted_overall = state.get("accepted_overall", {})
    rejected_args = rejected.get("args", {}) if isinstance(rejected, dict) else {}
    rejected_sections = {
        section.get("key"): section
        for section in rejected_args.get("sections", [])
        if isinstance(section, dict) and isinstance(section.get("key"), str)
    } if isinstance(rejected_args, dict) and isinstance(rejected_args.get("sections"), list) else {}
    accepted_sections = state.get("accepted_sections", {})
    def claim_matches_amount(claim: re.Match[str], amount: Any) -> bool:
        try:
            displayed = Decimal(claim.group("number").replace(",", "").replace("，", ""))
            candidate = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            return False
        scale = MONEY_UNIT_SCALE[claim.group("unit")]
        tolerance = Decimal("0.5") * (Decimal(10) ** -len(claim.group("number").partition(".")[2])) * scale
        return abs(displayed * scale - candidate) <= tolerance

    for key, title in labels:
        candidate = accepted_sections.get(key) or rejected_sections.get(key)
        if candidate:
            was_accepted = key in accepted_sections
            valid_fact_ids = [
                str(value) for value in candidate.get("fact_ids", [])
                if str(value) in state["facts"]
            ] if isinstance(candidate.get("fact_ids"), list) else []
            valid_calculation_ids = [
                str(value) for value in candidate.get("calculation_ids", [])
                if str(value) in state["calculations"]
            ] if isinstance(candidate.get("calculation_ids"), list) else []
            section_text = f"{candidate.get('summary', '')}\n{candidate.get('detail', '')}"
            for calculation_id in re.findall(r"\bcalc_[0-9a-f]{16}\b", section_text):
                if calculation_id in state["calculations"] and calculation_id not in valid_calculation_ids:
                    valid_calculation_ids.append(calculation_id)
            for claim in MONEY_AMOUNT_PATTERN.finditer(section_text):
                for fact_id, fact in state["facts"].items():
                    if fact.get("normalized_unit") == "CNY_yuan" and claim_matches_amount(claim, fact.get("normalized_value")):
                        if fact_id not in valid_fact_ids:
                            valid_fact_ids.append(fact_id)
                for calculation_id, calculation in state["calculations"].items():
                    values: list[Decimal] = []
                    if calculation.get("unit") == "CNY_yuan":
                        values.extend(_numeric_values(calculation.get("value")))
                        values.extend(_numeric_values(calculation.get("details", {})))
                    elif calculation.get("operation") == "yoy" and calculation.get("unit") == "ratio":
                        values.extend(_numeric_values(calculation.get("details", {}).get("absolute_change")))
                    if any(claim_matches_amount(claim, value) for value in values):
                        if calculation_id not in valid_calculation_ids:
                            valid_calculation_ids.append(calculation_id)
                        for fact_id in calculation.get("fact_ids", []):
                            if str(fact_id) in state["facts"] and str(fact_id) not in valid_fact_ids:
                                valid_fact_ids.append(str(fact_id))
            source_pages = {
                int(ref["page"])
                for fact_id in valid_fact_ids
                for ref in state["facts"][fact_id].get("evidence", [])
                if ref.get("page_valid") and ref.get("quote_matches_page") and ref.get("number_appears_in_quote")
            }
            for calculation_id in valid_calculation_ids:
                for fact_id in state["calculations"][calculation_id].get("fact_ids", []):
                    fact = state["facts"].get(str(fact_id), {})
                    source_pages.update(
                        int(ref["page"])
                        for ref in fact.get("evidence", [])
                        if ref.get("page_valid") and ref.get("quote_matches_page") and ref.get("number_appears_in_quote")
                    )
            submitted_pages = candidate.get("source_pages", [])
            if isinstance(submitted_pages, list):
                source_pages.update(
                    int(page) for page in submitted_pages
                    if str(page).isdigit() and int(page) in state["observed_pages"]
                )
            sections.append({
                **candidate,
                "key": key,
                "title": str(candidate.get("title") or title),
                "status": candidate.get("status") if was_accepted else "needs_review",
                "fact_ids": valid_fact_ids,
                "calculation_ids": valid_calculation_ids,
                "source_pages": sorted(source_pages),
            })
        else:
            sections.append({
                "key": key,
                "title": title,
                "status": "insufficient_evidence",
                "summary": "本次分析未能完成此板块。",
                "detail": "模型调用或分析轮次结束前没有提交经审阅的板块结论。请结合本次已保存原文、事实和错误记录继续分析。",
                "fact_ids": [],
                "calculation_ids": [],
                "source_pages": [],
            })
    used_fact_ids = {
        fact_id for section in sections for fact_id in section.get("fact_ids", [])
    }
    used_calculation_ids = {
        calculation_id for section in sections for calculation_id in section.get("calculation_ids", [])
    }
    overall_fact_ids = list(accepted_overall.get("overall_fact_ids", [])) or [
        str(value) for value in rejected_args.get("overall_fact_ids", [])
        if isinstance(rejected_args, dict)
        and isinstance(rejected_args.get("overall_fact_ids"), list)
        and str(value) in state["facts"]
    ]
    overall_calculation_ids = list(accepted_overall.get("overall_calculation_ids", [])) or [
        str(value) for value in rejected_args.get("overall_calculation_ids", [])
        if isinstance(rejected_args, dict)
        and isinstance(rejected_args.get("overall_calculation_ids"), list)
        and str(value) in state["calculations"]
    ]
    used_fact_ids.update(overall_fact_ids)
    used_calculation_ids.update(overall_calculation_ids)
    topics = accepted_overall.get("topics", []) if isinstance(accepted_overall.get("topics", []), list) else []
    for topic in topics:
        if isinstance(topic, dict):
            used_fact_ids.update(str(value) for value in topic.get("fact_ids", []) if str(value) in state["facts"])
            used_calculation_ids.update(str(value) for value in topic.get("calculation_ids", []) if str(value) in state["calculations"])
    for calculation_id in used_calculation_ids:
        used_fact_ids.update(
            str(fact_id) for fact_id in state["calculations"][calculation_id].get("fact_ids", [])
            if str(fact_id) in state["facts"]
        )
    limitations = ["五个板块或总评尚未全部通过提交校验。"]
    if isinstance(rejected, dict):
        limitations.append("以下保留的是校验未通过的候选分析，金额问题需复核，不能视为已验收结论。")
    return {
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "report_id": context.report_id,
        "company": str(accepted_overall.get("company") or (rejected_args.get("company", "") if isinstance(rejected_args, dict) else ""))[:240],
        "report_year": str(accepted_overall.get("report_year") or (rejected_args.get("report_year", "") if isinstance(rejected_args, dict) else ""))[:40],
        "overall_view": str(accepted_overall.get("overall_view") or (rejected_args.get("overall_view", "分析尚未完整提交；以下为已保存的模块过程和事实候选。") if isinstance(rejected_args, dict) else "分析尚未完整提交；以下为已保存的模块过程和事实候选。")),
        "overall_fact_ids": overall_fact_ids,
        "overall_calculation_ids": overall_calculation_ids,
        "overall_source_pages": accepted_overall.get("overall_source_pages") or (
            sorted(
                int(page) for page in rejected_args.get("overall_source_pages", [])
                if str(page).isdigit() and int(page) in state["observed_pages"]
            )
            if isinstance(rejected_args, dict) and isinstance(rejected_args.get("overall_source_pages"), list)
            else []
        ),
        "sections": sections,
        "topics": topics,
        "facts": [state["facts"][fact_id] for fact_id in sorted(used_fact_ids)],
        "calculations": [state["calculations"][calculation_id] for calculation_id in sorted(used_calculation_ids)],
        "limitations": limitations + (["因提交缺少板块引用编号，待复核结果按正文金额匹配已登记事实/计算以展示可能来源；这些自动关联不等于模型提交通过。"] if isinstance(rejected, dict) else []),
        "follow_up_questions": accepted_overall.get("follow_up_questions", rejected_args.get("follow_up_questions", []) if isinstance(rejected_args, dict) else []),
        "rejected_amount_claims": rejected.get("unmatched_amount_claims", []) if isinstance(rejected, dict) else [],
        "rejected_amount_claim_count": rejected.get("unmatched_amount_claim_count", len(rejected.get("unmatched_amount_claims", []))) if isinstance(rejected, dict) else 0,
        "missing_section_references": rejected.get("sections_missing_references", []) if isinstance(rejected, dict) else [],
        "validated_amount_claim_count": rejected.get("checked_amount_claim_count", 0) if isinstance(rejected, dict) else 0,
        "review_flags": [
            message
            for key in ("cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash")
            for message in state.get("accepted_section_review_flags", {}).get(key, [])
        ] + [
            f"{claim.get('section', '未知板块')} 中的金额 {claim.get('amount', '')} 未通过引用金额核对"
            for claim in rejected.get("unmatched_amount_claims", [])
        ] if isinstance(rejected, dict) else [
            message
            for key in ("cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash")
            for message in state.get("accepted_section_review_flags", {}).get(key, [])
        ],
        "accepted_sections": [key for key in ("cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash") if key in accepted_sections],
        "analysis_completeness": "partial",
    }


def _precompute_operating_after_capex(
    context: ReportContext,
    state: dict[str, Any],
    pages_by_number: dict[int, str],
) -> int:
    """Precompute the common operating-cash-less-capex metric when inputs are unambiguous."""
    if state.get("standard_cashflow_calculations_precomputed"):
        return 0
    state["standard_cashflow_calculations_precomputed"] = True

    operating_keys = {"op_net_cash_cfs", "cfs_op_net", "operating_net_cash", "ocf_net"}
    capex_keys = {
        "capex_paid", "capex_construction_payments", "long_term_asset_purchase_cash",
        "invest_capex", "capex_assets", "purchase_long_term_assets",
    }
    buckets: dict[tuple[str, str], dict[str, dict[tuple[str, str], str]]] = {}
    for fact_id, fact in state.get("facts", {}).items():
        if (
            fact.get("normalized_unit") != "CNY_yuan"
            or fact.get("period_kind") != "period_amount"
            or not fact.get("calculable")
        ):
            continue
        role = (
            "operating" if fact.get("metric_key") in operating_keys else
            "capex" if fact.get("metric_key") in capex_keys else
            ""
        )
        if not role:
            continue
        period = str(fact.get("period", "")).strip()
        scope = str(fact.get("scope", "")).strip()
        if not period or not scope:
            continue
        bucket = buckets.setdefault((period, scope), {"operating": {}, "capex": {}})
        identity = (str(fact.get("normalized_value", "")), str(fact.get("original_label", "")))
        bucket[role].setdefault(identity, fact_id)

    generated = 0
    for (period, _scope), roles in buckets.items():
        if len(roles["operating"]) != 1 or len(roles["capex"]) != 1:
            continue
        fact_ids = [next(iter(roles["operating"].values())), next(iter(roles["capex"].values()))]
        already_calculated = any(
            calculation.get("operation") == "operating_after_capex"
            and calculation.get("fact_ids") == fact_ids
            for calculation in state.get("calculations", {}).values()
        )
        if already_calculated:
            continue
        try:
            _dispatch(
                "calculate_cashflow",
                {
                    "operation": "operating_after_capex",
                    "name": f"经营现金减长期资产购建支出_{period}",
                    "fact_ids": fact_ids,
                },
                context=context,
                state=state,
                pages_by_number=pages_by_number,
            )
        except (ValueError, TypeError):
            continue
        if any(
            calculation.get("operation") == "operating_after_capex"
            and calculation.get("fact_ids") == fact_ids
            and calculation.get("status") == "calculated"
            for calculation in state.get("calculations", {}).values()
        ):
            generated += 1
    context.recorder.record("cashflow_standard_calculations_precomputed", count=generated)
    return generated


def _compact_context(context: ReportContext, state: dict[str, Any], *, include_all_facts: bool = False) -> str:
    terms = CASH_TERMS
    ranked_pages: list[tuple[int, int, str]] = []
    for page_number, text in state["observed_pages"].items():
        score = sum(min(text.count(term), 3) * weight for term, weight in terms.items())
        ranked_pages.append((score, int(page_number), text))
    ranked_pages.sort(key=lambda item: (-item[0], item[1]))
    compact_pages = [
        {"page": page, "text": text[:1800]}
        for _, page, text in ranked_pages[:7]
    ]
    all_facts = list(state["facts"].values())
    omitted_fact_count = 0
    if include_all_facts and len(all_facts) > MAX_FINAL_CONTEXT_FACTS:
        core_keys = {
            "cfs_op_net", "net_cash_investing", "net_cash_financing", "fx_effect_on_cash",
            "net_increase_in_cash", "beginning_cash_balance", "ending_cash_balance",
            "net_profit", "revenue", "capex_construction_payments",
            "operating_net_cash", "investing_net_cash", "financing_net_cash", "fx_effect",
            "fx_effect_cash", "net_increase_cash", "beginning_cash_equiv", "ending_cash_equiv",
            "cash_begin", "cash_end", "cash_equiv_begin", "cash_equiv_end", "recon_net_profit", "invest_capex",
        }
        prefixes = (
            "adj_", "other_", "cfs_", "net_cash_", "net_increase_", "beginning_cash_",
            "ending_cash_", "cash_composition_", "monetary_funds_", "capex_", "borrowings_",
            "debt_", "investment_", "investing_", "financing_",
        )
        prioritized = [
            fact for fact in all_facts
            if fact.get("metric_key") in core_keys
            or str(fact.get("metric_key", "")).startswith(prefixes)
        ]
        selected_ids = {fact["fact_id"] for fact in prioritized[:MAX_FINAL_CONTEXT_FACTS]}
        if len(selected_ids) < MAX_FINAL_CONTEXT_FACTS:
            for fact in reversed(all_facts):
                if fact["fact_id"] not in selected_ids:
                    selected_ids.add(fact["fact_id"])
                    if len(selected_ids) >= MAX_FINAL_CONTEXT_FACTS:
                        break
        all_facts = [fact for fact in all_facts if fact["fact_id"] in selected_ids]
        omitted_fact_count = len(state["facts"]) - len(all_facts)
    elif not include_all_facts:
        all_facts = all_facts[-80:]

    compact_facts = []
    for fact in all_facts:
        compact_facts.append({
            "fact_id": fact["fact_id"],
            "label": fact["original_label"],
            "value": fact["value"],
            "unit": fact["unit"],
            "period": fact["period"],
            "scope": fact["scope"],
            "table": fact["source_table"],
            "evidence_status": fact["evidence_status"],
            "evidence": [{"page": ref["page"], "row": ref["row_label"], "column": ref["column_label"], "quote": ref["quote"][:180]}
                         for ref in fact["evidence"][:2]],
        })
    compact_calculations = [
        {key: calc.get(key) for key in ("calculation_id", "name", "operation", "formula", "value", "unit", "fact_ids", "status", "details")}
        for calc in list(state["calculations"].values())[-80:]
    ]
    return _json({
        "report_id": context.report_id,
        "file_name": context.file_name,
        "page_count": context.page_count,
        "related_results": context.related_results,
        "recently_observed_cash_pages": compact_pages,
        "registered_facts": compact_facts,
        "verified_calculations": compact_calculations,
        "omitted_fact_count": omitted_fact_count,
        "instruction": (
            "这是最终提交上下文；只引用这里列出的事实和计算编号，资料不够时在板块中说明限制。"
            if include_all_facts else
            "此上下文浓缩了先前工具往返；原始查询、页面、模型请求与响应均保存在本次运行记录中。可继续搜索和读取页面。"
        ),
    })


def analyze_cashflow(context: ReportContext) -> dict[str, Any]:
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    seeds = _initial_pages(context)
    state: dict[str, Any] = {"facts": {}, "calculations": {}, "checkpoint": 0, "observed_pages": {}}
    context.progress("现金流分析", "进行中", "已准备年报候选页")
    context.save_artifact("cashflow_initial_pages", seeds)
    context.save_artifact("cashflow_analysis_instructions", {"version": MODULE_VERSION, "prompt": system_prompt})
    for page in seeds:
        state["observed_pages"][int(page["page"])] = str(page["text"])
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": "请分析下面这一份报告。先从候选页查找现金流主表和补充资料，再按需要检索及读取续页。候选页只是入口，不代表已经覆盖全文。报告运行上下文：\n" + _system_context(context, seeds),
        },
    ]
    pages_by_number = _page_map(context)

    total_rounds = MAX_TOOL_ROUNDS + FINALIZATION_ROUNDS
    for round_number in range(1, total_rounds + 1):
        finalizing = round_number > MAX_TOOL_ROUNDS
        rejected_submission = state.get("last_rejected_submission", {})
        unmatched_claims = rejected_submission.get("unmatched_amount_claims", [])
        repair_calculation_needed = any(
            isinstance(claim, dict) and not claim.get("candidate_calculation_ids")
            for claim in unmatched_claims
        )
        repair_calculation_round = (
            finalizing
            and round_number == MAX_TOOL_ROUNDS + FINALIZATION_REPAIR_CALCULATION_OFFSET
            and repair_calculation_needed
        )
        finalizing_calculations = finalizing and (
            round_number <= MAX_TOOL_ROUNDS + FINALIZATION_CALCULATION_ROUNDS
            or repair_calculation_round
        )
        final_tool = next(tool for tool in TOOLS if tool["function"]["name"] == "submit_cashflow_analysis")
        calculation_tool = next(tool for tool in TOOLS if tool["function"]["name"] == "calculate_cashflow")
        if finalizing:
            _precompute_operating_after_capex(context, state, pages_by_number)
            rejected = state.get("last_rejected_submission", {})
            final_state = {
                "accepted_sections": [
                    state["accepted_sections"][key]
                    for key in ("cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash")
                    if key in state.get("accepted_sections", {})
                ],
                "submission_errors": {
                    "section_errors": rejected.get("section_errors", {}),
                    "topic_errors": rejected.get("topic_errors", []),
                    "overall_errors": rejected.get("overall_errors", []),
                    "unmatched_amount_claims": rejected.get("unmatched_amount_claims", []),
                    "sections_missing_references": rejected.get("sections_missing_references", []),
                    "invalid_references": rejected.get("invalid_references", {}),
                },
                "previous_finalization_feedback": state.get("finalization_feedback", ""),
            }
            if repair_calculation_round:
                finalization_instructions = (
                    "这是本次最后一次金额修正计算机会。只能调用 calculate_cashflow，不要提交板块。只处理上一轮 unmatched_amount_claims 中没有候选 calculation_id、且能由已登记事实明确计算出来的差额/小计；没有足够事实时不要试算，后续删掉该金额并说明限制。候选事实或计算 ID 必须先核对科目、期间、方向。"
                )
            elif finalizing_calculations:
                finalization_instructions = (
                    "现在进入计算核验阶段。只能调用 calculate_cashflow，不要提交板块。检查事实/计算上下文，补齐缺少的必要勾稽：现金流主表活动净额与披露净增加的核对、期初期末现金余额核对、净利润调节到经营净现金的核对；"
                    "对正文将使用的同比差额、小计以及经营净现金减长期资产购建支出，也先计算。已有同一组事实的 calculation_id 不要重复计算；角色键以事实 metric_key 为准；最多调用到工具返回有效结果，不得试探公式或变换无关科目。"
                )
            else:
                finalization_instructions = (
                    "现在进入最终提交阶段。只能调用 submit_cashflow_analysis，不要再调用计算工具。先查看 accepted_sections 和 submission_errors：已通过的板块不要重复改写，只提交缺失或被退回的板块。"
                    "五个板块全部通过后，再用空 sections 提交总评和 topics 完成组装。金额错误反馈中的 candidate_fact_ids/candidate_calculation_ids 只是按数值匹配的候选；仅在科目、期间和方向都相符时才加入引用，否则删除金额或使用相应公式重算。"
                    "无效编号会在 invalid_references 中逐项列出；不要原样重用这些编号，只能从当前事实/计算清单中复制真实编号。金额候选列表为空时，已登记证据无法支持该数值，必须删除该数值并改写为明确限制；候选非空时也须确认科目、期间和方向一致。"
                    "invalid_references.fact_evidence 列出的事实来源存在摘录或行名问题；不要继续用它支撑数字。若没有另一条通过来源核对的事实，删去相关金额和细节，改为 needs_review 并写明证据限制。"
                )
            messages = [
                messages[0],
                {
                    "role": "user",
                    "content": (
                        finalization_instructions +
                        "只能引用下列上下文中存在的事实和计算编号；证据不足就说明具体限制，不要补造数字。"
                        "status=analyzed 必须关联本板块自己的事实或计算；无证据时可用 insufficient_evidence 并说明缺口。"
                        "每个 summary 不超过 80 个汉字，每个 detail 不超过 400 个汉字；不输出 tables，不使用未转义的半角双引号。\n"
                        "当前已保存结果和校验反馈：\n" + _json(final_state) + "\n"
                        "当前事实、计算与报告信息：\n" +
                        _compact_context(context, state, include_all_facts=True)
                    ),
                },
            ]
        elif round_number > 1 and round_number <= MAX_TOOL_ROUNDS and round_number % 3 == 1:
            messages = [
                messages[0],
                {"role": "user", "content": "继续完成本报告分析。以下为此前工具过程的工作记忆浓缩；原文页、事实和计算仍可继续读取与修订：\n" + _compact_context(context, state)},
            ]
        if finalizing:
            offered_tools = [calculation_tool] if finalizing_calculations else [final_tool]
            tool_choice: Any = (
                "auto" if finalizing_calculations else
                {"type": "function", "function": {"name": "submit_cashflow_analysis"}}
            )
            progress = f"最终提交轮次 {round_number - MAX_TOOL_ROUNDS}/{FINALIZATION_ROUNDS}"
        else:
            offered_tools = TOOLS
            tool_choice = "auto"
            progress = f"DeepSeek 分析轮次 {round_number}/{MAX_TOOL_ROUNDS}"
        context.progress("现金流分析", "进行中", progress)
        model_options: dict[str, Any] = {}
        if finalizing:
            model_options["max_tokens"] = MAX_FINAL_OUTPUT_TOKENS
        if finalizing:
            # DeepSeek rejects named tool_choice in thinking mode; finalization only assembles prior work.
            model_options["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            response = context.call_model(
                messages=messages,
                tools=offered_tools,
                tool_choice=tool_choice,
                temperature=0.15,
                **model_options,
            )
        except Exception as exc:
            # Preserve accepted sections, facts, calculations, and rejected
            # submission feedback if the provider stops responding mid-run.
            partial = _partial_result(state, context)
            has_work = bool(state.get("facts") or state.get("calculations") or state.get("accepted_sections"))
            partial["analysis_completeness"] = "partial" if has_work else "insufficient_data"
            name = type(exc).__name__
            if name == "AuthenticationError":
                reason = "DeepSeek 拒绝了当前 API Key，请检查密钥是否有效。"
            elif name == "RateLimitError":
                reason = "DeepSeek 请求受限或账户余额不足；已保留本轮中间结果。"
            elif name == "APITimeoutError":
                reason = "DeepSeek 响应超时；已保留本轮中间结果。"
            elif name == "APIConnectionError":
                reason = "后台无法连接 DeepSeek；已保留本轮中间结果。"
            elif name == "ModelCallError":
                message_text = str(exc)
                reason = message_text[:500] if not any(token in message_text.lower() for token in ("api_key", "authorization", "bearer", "sk-")) else "模型配置或调用失败，敏感凭据已省略。"
            else:
                reason = f"模型请求出现 {name}；已保留本轮中间结果。"
            partial["failure_reason"] = reason
            partial.setdefault("limitations", []).append(
                "模型调用在分析过程中中断；已保存的事实、计算和板块仍可查看，未完成部分不能视为已分析。"
            )
            context.recorder.record("cashflow_analysis_interrupted", error_type=name)
            context.save_artifact("cashflow_interrupted_result", partial)
            context.progress("现金流分析", "部分完成" if has_work else "失败", reason)
            return {"result": partial, "read_pages": sorted(state["observed_pages"]), "trace": []}
        choice = response.choices[0]
        message = choice.message
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            content = getattr(message, "content", None) or ""
            if finalizing:
                if finalizing_calculations:
                    state["finalization_feedback"] = "本轮没有生成新的计算结果；后续请提交已登记事实和现有计算，不要重复尝试。"
                else:
                    state["finalization_feedback"] = "上一轮没有调用 submit_cashflow_analysis。请直接提交当前整理结果；可将证据缺口写为限制，不要再检索或计算。"
            else:
                messages.append({"role": "assistant", "content": str(content)})
                messages.append({
                    "role": "user",
                    "content": "还没有通过 submit_cashflow_analysis 提交结果。请核对已有事实和计算，然后调用该工具提交五个板块；如资料有缺口，标出状态和具体限制。",
                })
            continue

        expected_final_tool = "calculate_cashflow" if finalizing_calculations else "submit_cashflow_analysis"
        if finalizing and any(call.function.name != expected_final_tool for call in tool_calls):
            state["finalization_feedback"] = f"上一轮试图调用当前阶段不可用的工具。当前只能调用 {expected_final_tool}；请按当前阶段继续。"
            context.recorder.record(
                "cashflow_finalization_unexpected_tool_rejected",
                expected_tool=expected_final_tool,
                unexpected_tools=sorted({call.function.name for call in tool_calls if call.function.name != expected_final_tool}),
            )
            continue

        messages.append(message.model_dump(exclude_none=True))
        submitted: dict[str, Any] | None = None
        for tool_call in tool_calls:
            function = tool_call.function
            arguments: dict[str, Any] = {}
            try:
                arguments = json.loads(function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须是 JSON 对象")
                calc_counter_key = (
                    "finalization_repair_calculation_count"
                    if repair_calculation_round else "finalization_calculation_count"
                )
                calc_counter_limit = (
                    FINALIZATION_REPAIR_CALCULATION_LIMIT
                    if repair_calculation_round else FINALIZATION_CALCULATION_LIMIT
                )
                if finalizing_calculations and state.get(calc_counter_key, 0) >= calc_counter_limit:
                    result = {"error": "最终计算核验调用次数已达上限；请直接提交已核对结果并列出仍无法计算的限制。", "retryable": False}
                else:
                    if finalizing_calculations and function.name == "calculate_cashflow":
                        state[calc_counter_key] = state.get(calc_counter_key, 0) + 1
                    result = _dispatch(
                        function.name,
                        arguments,
                        context=context,
                        state=state,
                        pages_by_number=pages_by_number,
                    )
                if function.name == "submit_cashflow_analysis" and result.get("accepted"):
                    submitted = state.get("final_result")
                elif finalizing and function.name == "submit_cashflow_analysis":
                    state["finalization_feedback"] = result
                elif finalizing_calculations and function.name == "calculate_cashflow":
                    state["finalization_feedback"] = "已登记所需的计算结果，下一阶段根据这些结果提交五个板块。"
            except json.JSONDecodeError as exc:
                result = {
                    "error": "提交参数不是有效 JSON。请缩短内容：五个板块的 detail 各不超过 400 个汉字，不输出表格，不使用未转义的半角双引号；只引用上下文已有编号后重新提交。",
                    "retryable": True,
                }
                context.recorder.record("cashflow_tool_rejected", tool=function.name, error_type=type(exc).__name__)
            except (ValueError, TypeError) as exc:
                result = {"error": str(exc), "retryable": True}
                context.recorder.record("cashflow_tool_rejected", tool=function.name, error_type=type(exc).__name__)
            except Exception as exc:
                result = {"error": "模块工具执行失败", "error_type": type(exc).__name__, "retryable": False}
                context.recorder.record("cashflow_tool_failed", tool=function.name, error_type=type(exc).__name__)
            context.record_tool(function.name, arguments, result)
            context.tool_activity(function.name, f"现金流模块正在执行：{function.name}")
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": function.name,
                "content": _json(result),
            })
            if submitted is not None:
                context.progress("现金流分析", "已完成", f"已提交 {len(submitted.get('sections', []))} 个板块")
                return {"result": submitted, "read_pages": sorted(state["observed_pages"]), "trace": []}

    partial = _partial_result(state, context)
    context.save_artifact("cashflow_partial_result", partial)
    context.progress("现金流分析", "部分完成", "已保存中间结果；未在分析轮次内提交完整分析")
    return {"result": partial, "read_pages": sorted(state["observed_pages"]), "trace": []}
