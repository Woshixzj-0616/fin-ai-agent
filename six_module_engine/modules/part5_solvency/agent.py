"""DeepSeek tool loop for evidence-based debt and funding-pressure analysis."""

from __future__ import annotations

import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from backend.core.context import ReportContext
from backend.core.legacy_runtime import _safe_model_error
from backend.deepseek_client import ModelCallError
from modules.part5_solvency.calculations import calculate_metric
from modules.part5_solvency.facts import add_facts
from modules.part5_solvency.retrieval import SEARCH_STARTERS, select_initial_pages


MODULE_DIR = Path(__file__).resolve().parent
MODULE_VERSION = "模块五_债务与资金压力_v2.1.1"
MAX_ROUNDS = 16
MAX_TOTAL_TOOL_CALLS = 40
MAX_PAGE_READS = 18
MAX_TOTAL_RETRIEVAL_TOOL_CALLS = 24
MAX_CONTEXT_GROUPS = 1
MAX_PAGE_CHARS = 4800
MAX_TABLE_LAYOUT_CHARS = 7000
MAX_TOOL_TEXT_CHARS = 2200
MAX_PERSISTED_PAGE_CHARS = 1600
MAX_PERSISTED_PAGE_CONTEXT_CHARS = 7500

CORE_FACT_GROUPS = {
    "total_assets": ("total_assets", "资产总计", "资产合计"),
    "total_liabilities": ("total_liabilities", "负债合计", "负债总计"),
    "current_assets": ("current_assets", "流动资产合计"),
    "current_liabilities": ("current_liab", "流动负债合计"),
    "equity": ("bs_equity", "equity_total", "所有者权益合计", "股东权益合计", "所有者权益（股东权益）合计"),
    "cash": ("cash", "货币资金"),
    "inventory": ("inventory", "存货"),
    "short_borrowings": ("st_borrow", "短期借款"),
    "current_maturities": ("nt_", "一年内到期的非流动负债"),
    "long_borrowings": ("lt_borrow", "长期借款"),
    "bonds": ("bond", "应付债券"),
    "lease_liabilities": ("lease", "租赁负债"),
    "operating_cash_flow": ("ocf", "经营活动产生的现金流量净额"),
    "pretax_profit": ("pretax_profit", "利润总额"),
    "interest_expense": ("interest_expense", "利息费用", "利息支出"),
}


def _cny_value_in_100m(item: dict[str, Any]) -> str | None:
    """Return a deterministic two-decimal CNY display value in 亿元."""
    unit = str(item.get("normalized_unit") or item.get("unit") or "").strip().upper()
    if unit not in {"CNY", "元"}:
        return None
    raw = item.get("normalized_value")
    if raw is None:
        raw = item.get("value")
    try:
        value = Decimal(str(raw)) / Decimal("100000000")
    except (InvalidOperation, ValueError, TypeError):
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def _load_prompt(name: str) -> str:
    return (MODULE_DIR / "prompts" / name).read_text(encoding="utf-8")


def _compact_related(results: dict[str, Any]) -> str:
    lines: list[str] = []
    for module_id, value in list(results.items())[:6]:
        if not isinstance(value, dict):
            continue
        parts = [f"线索来自{module_id}，必须回到本报告核实："]
        for key in ("headline", "summary", "business_overview"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                parts.append(item.strip()[:360])
                break
        for item in value.get("findings", [])[:3] if isinstance(value.get("findings"), list) else []:
            if isinstance(item, dict):
                detail = item.get("claim") or item.get("title") or ""
                if detail:
                    parts.append(str(detail)[:220])
        lines.append("\n".join(parts))
    return "\n\n".join(lines)[:1800]


def _compact_source_page(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    anchors = (
        "应付债券", "短期借款", "长期借款", "一年内到期的非流动负债", "租赁负债",
        "负债合计", "所有者权益合计", "资产总计", "流动资产合计", "货币资金", "存货",
        "经营活动产生的现金流量净额", "经营活动现金流量净额", "利润总额", "利息费用", "利息支出",
        "流动性风险", "剩余期限", "未折现合同现金流",
    )
    windows: list[tuple[int, int]] = [(0, min(260, len(text)))]
    for anchor in anchors:
        position = text.find(anchor)
        if position >= 0:
            windows.append((max(0, position - 180), min(len(text), position + 420)))
    # Select windows in domain priority order, then restore source order.
    selected = [windows[0]]
    used = min(limit, windows[0][1])
    for start, end in windows[1:]:
        if used >= limit:
            break
        if any(start < old_end and end > old_start for old_start, old_end in selected):
            continue
        take = min(end - start, limit - used)
        selected.append((start, start + take))
        used += take
    selected.sort()
    return "\n…\n".join(text[start:end] for start, end in selected)


def _page_table_layout(context: ReportContext, page_number: int) -> str:
    """Return layout rows retained during PDF indexing, when available."""
    for page in context.pages:
        if int(page.get("page", 0)) == page_number:
            return str(page.get("table_layout") or "")
    return ""


def _core_coverage(facts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    coverage = {}
    for coverage_key, aliases in CORE_FACT_GROUPS.items():
        matched = [
            item for item in facts.values()
            if any(alias in str(item.get("fact_key") or "").lower() or alias in str(item.get("label") or "") for alias in aliases)
        ]
        if not matched:
            status = "not_yet_captured"
        elif all(item.get("validation", {}).get("calculation_eligible") for item in matched):
            status = "captured_semantics_confirmed"
        else:
            status = "captured_needs_review"
        coverage[coverage_key] = {
            "status": status,
            "fact_ids": [item.get("fact_id") for item in matched],
            "validation": [item.get("validation", {}).get("semantic_context_status", "unconfirmed") for item in matched],
        }
    return coverage


def _messages_for_model(
    messages: list[dict[str, Any]],
    facts: dict[str, dict[str, Any]],
    calculations: dict[str, dict[str, Any]],
    read_pages: set[int],
    page_texts: dict[int, str],
    *,
    include_initial_pages: bool,
    retain_history_groups: int = MAX_CONTEXT_GROUPS,
    retrieval_budget_exhausted: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Keep the audit transcript locally while sending a bounded working context."""
    if include_initial_pages or len(messages) <= 2:
        return messages, 0

    try:
        task = json.loads(str(messages[1].get("content") or "{}"))
    except json.JSONDecodeError:
        task = {}
    if isinstance(task, dict):
        task.pop("initial_pages", None)
        task.pop("first_search_suggestions", None)
    compact_user = {"role": "user", "content": json.dumps(task, ensure_ascii=False)}

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in messages[2:]:
        role = item.get("role")
        if role in {"assistant", "user"} and current:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)
    retained_groups = groups[-max(0, retain_history_groups):] if retain_history_groups else []
    trailing_user = messages[-1] if len(messages) > 2 and messages[-1].get("role") == "user" else None

    fact_state = [
        {
            "fact_id": item.get("fact_id"),
            "fact_key": item.get("fact_key"),
            "label": item.get("label"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "currency": item.get("currency"),
            "normalized_value": item.get("normalized_value"),
            "normalized_unit": item.get("normalized_unit"),
            "display_value_100m_cny": _cny_value_in_100m(item),
            "scope": item.get("scope"),
            "period_type": item.get("period_type"),
            "period_start": item.get("period_start"),
            "period_end": item.get("period_end"),
            "as_of_date": item.get("as_of_date"),
            "measurement_basis": item.get("measurement_basis"),
            "liability_type": item.get("liability_type"),
            "included_fact_ids": item.get("included_fact_ids", []),
            "evidence": [
                {"page": source.get("page"), "quote": str(source.get("quote") or "")[:360], "role": source.get("role", "")}
                for source in item.get("evidence", [])[:2]
            ],
            "source_context": item.get("source_context", {}),
            "validation_status": item.get("validation", {}).get("status"),
            "validation_problems": item.get("validation", {}).get("problems", []),
            "semantic_context_status": item.get("validation", {}).get("semantic_context_status"),
            "semantic_context_problems": item.get("validation", {}).get("semantic_context_problems", []),
            "calculation_eligible": item.get("validation", {}).get("calculation_eligible", False),
            "revision": item.get("revision", 1),
        }
        for item in facts.values()
    ]
    calculation_state = [
        {
            "calculation_id": calculation_id,
            "name": item.get("name"),
            "metric": item.get("metric"),
            "status": item.get("status"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "display_value": item.get("display_value"),
            "display_unit": item.get("display_unit"),
            "display_value_100m_cny": _cny_value_in_100m(item),
            "formula": item.get("formula"),
            "input_fact_ids": item.get("input_fact_ids", []),
            "reason": item.get("reason"),
            "note": item.get("note"),
        }
        for calculation_id, item in calculations.items()
    ]
    coverage = _core_coverage(facts)

    # Keep useful, explicitly read source pages across compacted model calls.
    # Initial excerpts are deliberately excluded: they are leads, not full-page review.
    relevant_pages = []
    fact_pages = {
        int(source["page"])
        for item in facts.values()
        for source in item.get("evidence", [])
        if source.get("page") is not None and int(source["page"]) in read_pages
    }
    for page in reversed(list(page_texts)):
        if page in read_pages and page not in relevant_pages:
            relevant_pages.append(page)
        if len(relevant_pages) >= 3:
            break
    for page in sorted(fact_pages, reverse=True):
        if page not in relevant_pages:
            relevant_pages.append(page)
    page_context = []
    remaining_page_chars = MAX_PERSISTED_PAGE_CONTEXT_CHARS
    for page in relevant_pages:
        text = _compact_source_page(str(page_texts.get(page) or ""), min(MAX_PERSISTED_PAGE_CHARS, remaining_page_chars))
        if text:
            page_context.append({"page": page, "text": text})
            remaining_page_chars -= len(text)
        if remaining_page_chars <= 0:
            break

    state = {
        "current_state": {
            "instruction": "这是持续工作账本。initial_excerpt_pages 是初筛摘录，不等于完整读页。facts 保存已登记摘录和来源语境；read_page_context 保存已完整读取页的文本。优先补 not_yet_captured 或语义未确认的项目，不要重复读取已在上下文中提供的页面。自动核对不证明会计含义正确。",
            "retrieval_budget_exhausted": retrieval_budget_exhausted,
            "full_pages_read": sorted(read_pages),
            "core_data_coverage": coverage,
            "read_page_context": page_context,
            "facts": fact_state,
            "calculations": calculation_state,
        }
    }
    state_message = {"role": "user", "content": json.dumps(state, ensure_ascii=False)}
    compacted = [messages[0], compact_user]
    compacted.extend(message for group in retained_groups for message in group)
    if trailing_user is not None and not any(trailing_user is item for group in retained_groups for item in group):
        compacted.append(trailing_user)
    compacted.append(state_message)
    return compacted, len(retained_groups)


def _finalizer_messages(
    messages: list[dict[str, Any]],
    facts: dict[str, dict[str, Any]],
    calculations: dict[str, dict[str, Any]],
    read_pages: set[int],
    page_texts: dict[int, str],
) -> list[dict[str, Any]]:
    try:
        task = json.loads(str(messages[1].get("content") or "{}"))
    except (IndexError, json.JSONDecodeError):
        task = {}
    compacted, _ = _messages_for_model(
        messages,
        facts,
        calculations,
        read_pages,
        page_texts,
        include_initial_pages=False,
        retain_history_groups=0,
        retrieval_budget_exhausted=True,
    )
    try:
        state = json.loads(str(compacted[-1].get("content") or "{}"))
    except json.JSONDecodeError:
        state = {}
    final_system = (
        "你是模块五的最终结果整理器。现在只根据用户消息中的报告身份、已登记事实和计算尝试，"
        "提交本模块的结构化分析。当前唯一允许的工具是 submit_final_analysis；只能调用该工具，"
        "不要请求其他工具或新资料。不得在工具失败或缺少计算时手算、估算或编造数值。"
        "在 analysis 中保留有依据的公司专题，区分年报披露、程序计算和推断；每项结论把现有事实 ID 放入 evidence_fact_ids 数组，把计算 ID 放入 calculation_ids 数组，禁止把 ID 只写在 statement 或 claim 文本里。金额优先使用工作账本给出的 display_value_100m_cny；该字段以亿元表示。比率优先使用程序提供的 display_value 和 display_unit；资产负债率、净负债率、短债占比应按百分数展示。不要自行换算元、手算差额、合计或比率。任何推导金额或百分比必须有对应 calculation_id；没有计算记录时只定性描述或说明缺口。"
        "对未确认、未匹配或未计算的事项明确说明原因及影响。引用事实前检查 calculation_eligible；语义未确认的事实只作为待核线索。"
    )
    final_user = {
        "report": task.get("report", {}) if isinstance(task, dict) else {},
        "working_ledger": state.get("current_state", {}) if isinstance(state, dict) else {},
        "task": "现在保存当前分析，不再查阅新页面。使用 submit_final_analysis，参数必须为 {analysis: {...}}。"
        "至少提交 analysis_status、headline、findings、limitations、handoff_questions、data_coverage。对未登记的核心项逐一使用 not_yet_captured、not_found_after_search、not_disclosed、ambiguous 或 not_applicable；非 not_yet_captured 状态必须给 note 和 source_pages，页码只能取 full_pages_read。不要把没查到写成未披露。已登记事实的状态由程序根据事实底稿生成。有资料时保留 debt_structure、maturity_profile、funding_sources、cash_availability、support_metrics、obligations 和 additional_sections。报告按最重要发现组织，不要求每家公司套相同结论；但应分别覆盖债务结构、期限压力、资金来源与可动用性、偿债支撑和关键不确定项。先给结论，再给事实与计算依据，不要把所有指标塞进一句话。同一指标的年报披露值与程序计算值不同时，说明口径；无法解释时并列保留，不得声称一致。",
    }
    return [
        {"role": "system", "content": final_system},
        {"role": "user", "content": json.dumps(final_user, ensure_ascii=False)},
    ]


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "search_pdf_pages",
                "description": "搜索当前年报全文，找债务、资金、期限、利息和承诺的原文页。可以分批查短语。",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "maxLength": 160}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_pdf_page",
                "description": "读取当前年报的 PDF 物理页码原文；如果索引时保留了表格布局，也会给出按行列还原的表格数据并保留空白单元格。若续表页没有重复表头，读取相邻表头页并在事实证据中同时引用两页。期限表候选结果会优先提示本报告年度页面；不要用比较期页面替代本期。债务重分类、偿还或发行变化需回查相邻附注。",
                "parameters": {
                    "type": "object",
                    "properties": {"page_number": {"type": "integer", "minimum": 1}},
                    "required": ["page_number"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_financial_facts",
                "description": "提交或修订带原文摘录和来源语境的事实。表格事实在 source_context 中填写 kind=table、准确 row_label、column_header、unit_label、semantic_status、review_note；如有 PDF_TABLE_LAYOUT，按同一行、同一列确认数值与列标题。续表行和相邻页表头必须都作为 evidence 提交。布局缺失或矩阵仍无法定位时填 ambiguous。检查相关附注是否说明债务重分类、偿还或其他变化。同一事实修订时沿用 fact_key；未确认的事实不能用于计算。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "facts": {"type": "array", "items": {"type": "object"}, "maxItems": 100}
                    },
                    "required": ["facts"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "calculate_solvency_metric",
                "description": "用数字和摘录匹配、且来源语境已确认的事实进行精确计算。表格事实需提交并核对 row_label、column_header、unit_label；提供 PDF_TABLE_LAYOUT 时，程序还会核对金额是否位于声明行列。若口径不匹配，返回具体原因。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "fact_ids": {"type": "array", "items": {"type": "string"}},
                        "cash_fact_id": {"type": "string"},
                        "restriction_fact_ids": {"type": "array", "items": {"type": "string"}},
                        "debt_fact_ids": {"type": "array", "items": {"type": "string"}},
                        "cash_fact_ids": {"type": "array", "items": {"type": "string"}},
                        "equity_fact_id": {"type": "string"},
                        "short_debt_fact_ids": {"type": "array", "items": {"type": "string"}},
                        "total_debt_fact_ids": {"type": "array", "items": {"type": "string"}},
                        "note": {"type": "string", "maxLength": 400},
                    },
                    "required": ["metric"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_final_analysis",
                "description": "提交本模块结构化专业分析。保留有证据的新专题；结论引用已返回的 fact_id 和 calculation_id，并明确自动证据匹配不等于人工审阅。",
                "parameters": {
                    "type": "object",
                    "properties": {"analysis": {"type": "object"}},
                    "required": ["analysis"],
                    "additionalProperties": False,
                },
            },
        },
    ]


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("模型工具参数不是有效 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError("模型工具参数必须是 JSON 对象。")
    return value


def _parse_json_text(raw: str) -> dict[str, Any] | None:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.I)
    try:
        result = json.loads(text)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(text[start : end + 1])
                return result if isinstance(result, dict) else None
            except json.JSONDecodeError:
                return None
    return None


_PERCENT_METRICS = {
    "asset_liability_ratio",
    "net_gearing",
    "short_debt_share",
}
_PERCENT_NAMES = {
    "资产负债率": "asset_liability_ratio",
    "净负债率": "net_gearing",
    "短期融资债务占所列融资债务小计比例": "short_debt_share",
}


def _prepare_calculation_display(calculations: dict[str, dict[str, Any]]) -> None:
    """Add deterministic, human-readable values while preserving raw results."""
    for item in calculations.values():
        metric = str(item.get("metric") or _PERCENT_NAMES.get(str(item.get("name") or ""), ""))
        item["metric"] = metric or item.get("metric")
        item["display_value"] = None
        item["display_unit"] = item.get("unit")
        if item.get("status") != "calculated" or item.get("value") is None:
            continue
        try:
            value = Decimal(str(item["value"]))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if metric in _PERCENT_METRICS:
            item["display_value"] = format((value * Decimal(100)).quantize(Decimal("0.01")), "f")
            item["display_unit"] = "%"
        elif str(item.get("unit") or "") in {"元", "CNY"}:
            item["display_value"] = _cny_value_in_100m(item)
            item["display_unit"] = "亿元" if item["display_value"] is not None else item.get("unit")
        else:
            item["display_value"] = format(value.quantize(Decimal("0.01")), "f")


def _attach_section_evidence(
    result: dict[str, Any],
    facts: dict[str, dict[str, Any]],
    calculations: dict[str, dict[str, Any]],
    read_pages: set[int],
) -> list[str]:
    """Validate citations throughout nested result sections and expose calc results."""
    section_keys = (
        "debt_structure", "maturity_profile", "funding_sources", "cash_availability",
        "support_metrics", "obligations", "additional_sections",
    )
    problems: list[str] = []
    for key in section_keys:
        if key not in result:
            result[key] = [] if key == "additional_sections" else {
                "section_status": "not_assessed",
                "note": "本轮未形成该板块的可核实结论。",
            }
        section = result[key]
        nodes = section if isinstance(section, list) else [section]

        def visit(node: Any, path: str) -> tuple[set[str], set[str], list[str]]:
            if isinstance(node, list):
                all_facts: set[str] = set()
                all_calcs: set[str] = set()
                all_problems: list[str] = []
                for index, child in enumerate(node):
                    child_facts, child_calcs, child_problems = visit(child, f"{path}[{index}]")
                    all_facts.update(child_facts)
                    all_calcs.update(child_calcs)
                    all_problems.extend(child_problems)
                return all_facts, all_calcs, all_problems
            if not isinstance(node, dict):
                return set(), set(), []

            own_facts: set[str] = set()
            own_calcs: set[str] = set()
            for field in ("evidence_fact_ids", "fact_ids", "fact_id"):
                value = node.get(field)
                values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                own_facts.update(str(item) for item in values if item)
            for field in ("calculation_ids", "calculation_id", "calc_id"):
                value = node.get(field)
                values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                own_calcs.update(str(item) for item in values if item)
            for field, value in node.items():
                if field.endswith("_calculation_id") and isinstance(value, str):
                    own_calcs.add(value)
                if isinstance(value, str):
                    for match in re.finditer(r"(?:solv|calc)_[0-9a-f]+", value, flags=re.I):
                        token = match.group(0)
                        if token in facts:
                            own_facts.add(token)
                        elif token in calculations:
                            own_calcs.add(token)

            child_facts: set[str] = set()
            child_calcs: set[str] = set()
            child_problems: list[str] = []
            for field, value in node.items():
                if field in {"program_result", "program_results", "section_evidence_notes"}:
                    continue
                if isinstance(value, (dict, list)):
                    found_facts, found_calcs, found_problems = visit(value, f"{path}.{field}")
                    child_facts.update(found_facts)
                    child_calcs.update(found_calcs)
                    child_problems.extend(found_problems)

            fact_ids = set(own_facts)
            calc_ids = set(own_calcs)
            narrative = " ".join(
                str(node.get(field) or "")
                for field in ("summary", "statement", "claim", "text", "basis", "conclusion")
            )
            quantified_fields = " ".join(
                str(value) for field, value in node.items()
                if field.startswith(("amount", "display", "value", "ratio", "percent"))
                and isinstance(value, (str, int, float))
            )
            quantified = bool(
                re.search(r"\d[\d,.]*(?:亿元|万元|元|%|倍|年|期)", narrative)
                or re.search(r"(?<![\w.])[+-]?\d+(?:\.\d+)?", quantified_fields)
            )
            # Section summaries often restate the item figures but omit their
            # IDs at the root. Promote the nested IDs so the summary stays traceable.
            if quantified and not (fact_ids or calc_ids) and (child_facts or child_calcs):
                fact_ids.update(child_facts)
                calc_ids.update(child_calcs)

            missing_facts = [value for value in fact_ids if value not in facts]
            unverified_facts = [
                value for value in fact_ids
                if value in facts and (
                    facts[value].get("validation", {}).get("status") not in {"quote_matched", "quote_and_number_matched"}
                    or not facts[value].get("validation", {}).get("calculation_eligible")
                )
            ]
            missing_calcs = [value for value in calc_ids if value not in calculations]
            unsuccessful_calcs = [
                value for value in calc_ids
                if value in calculations and calculations[value].get("status") != "calculated"
            ]
            invalid_pages: list[int] = []
            page_values = node.get("source_pages", [])
            if isinstance(page_values, (str, int)):
                page_values = [page_values]
            if isinstance(page_values, list):
                for page in page_values:
                    try:
                        number = int(page)
                    except (TypeError, ValueError):
                        continue
                    if number not in read_pages:
                        invalid_pages.append(number)

            if fact_ids:
                node["evidence_fact_ids"] = sorted(fact_ids)
            if calc_ids:
                node["calculation_ids"] = sorted(calc_ids)
            program_results = []
            for calc_id in sorted(calc_ids):
                calculation = calculations.get(calc_id)
                if not calculation:
                    continue
                program_results.append({
                    "calculation_id": calc_id,
                    "status": calculation.get("status"),
                    "value": calculation.get("display_value"),
                    "unit": calculation.get("display_unit"),
                    "formula": calculation.get("formula"),
                    "note": calculation.get("note") or calculation.get("reason"),
                })
            if program_results:
                node["program_results"] = program_results
                if len(program_results) == 1:
                    node["program_result"] = program_results[0]

            reasons: list[str] = []
            if missing_facts:
                reasons.append("引用了不存在的事实 ID")
            if unverified_facts:
                reasons.append("部分事实的来源或语义仍待复核")
            if missing_calcs:
                reasons.append("引用了不存在的计算 ID")
            if unsuccessful_calcs:
                reasons.append("引用的计算未成功完成")
            if invalid_pages:
                reasons.append("来源页不在本轮完整读取页中")
            if quantified and not (fact_ids or calc_ids):
                reasons.append("包含定量结论但没有结构化事实或计算引用")
            reasons.extend(child_problems)
            node["section_status"] = "needs_review" if reasons else "available"
            if reasons:
                node["section_evidence_notes"] = list(dict.fromkeys(reasons))
                return fact_ids | child_facts, calc_ids | child_calcs, reasons
            node.pop("section_evidence_notes", None)
            return fact_ids | child_facts, calc_ids | child_calcs, []

        for index, node in enumerate(nodes):
            _, _, node_problems = visit(node, f"{key}[{index}]" if isinstance(section, list) else key)
            if node_problems:
                problems.append(f"{key}：" + "；".join(dict.fromkeys(node_problems)))
    return problems


def _clean_final(
    raw: Any,
    facts: dict[str, dict[str, Any]],
    calculations: dict[str, dict[str, Any]],
    report_id: str,
    read_pages: set[int] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    result = dict(raw)
    result.update(
        {
            "module_id": "solvency",
            "module_version": MODULE_VERSION,
            "report_id": report_id,
        }
    )
    result["analysis_status"] = str(result.get("analysis_status") or "partial")
    if result["analysis_status"] not in {"complete", "partial", "insufficient_data"}:
        result["analysis_status"] = "partial"
    if not isinstance(result.get("findings"), list):
        result["findings"] = []

    for index, finding in enumerate(result["findings"]):
        if not isinstance(finding, dict):
            result["findings"][index] = {"claim": str(finding), "evidence_fact_ids": [], "evidence_status": "needs_review"}
            continue
        if not str(finding.get("title") or "").strip():
            title_source = next(
                (
                    str(finding.get(field) or "").strip()
                    for field in ("topic", "issue", "claim", "statement", "text")
                    if str(finding.get(field) or "").strip()
                ),
                "未命名偿债专题",
            )
            title_source = re.split(r"[。；\n]", title_source, maxsplit=1)[0].strip()
            finding["title"] = title_source[:56]
        if not str(finding.get("claim") or "").strip():
            finding["claim"] = str(finding.get("statement") or finding.get("topic") or finding["title"]).strip()
        # Accept the model's common shorthand as well as the documented field.
        # Otherwise real citations in `fact_ids` are silently discarded and
        # every finding is mislabeled as having no evidence.
        fact_ids = finding.get("evidence_fact_ids") or finding.get("fact_ids", [])
        calc_ids = finding.get("calculation_ids", [])
        if not isinstance(fact_ids, list):
            fact_ids = []
        if not isinstance(calc_ids, list):
            calc_ids = []
        narrative_fields = ("claim", "statement", "title", "text", "basis", "caveats", "limitations")
        narrative = " ".join(str(finding.get(key) or "") for key in narrative_fields)
        # Recover citations some model responses place inline (for example
        # `[solv_...]`) instead of in the structured citation arrays.
        for match in re.finditer(r"(?:solv|calc)_[0-9a-f]+", narrative, flags=re.I):
            token = match.group(0)
            if token in facts and token not in fact_ids:
                fact_ids.append(token)
            elif token in calculations and token not in calc_ids:
                calc_ids.append(token)
        # A calculation is also a traceable source; carry its registered input
        # fact IDs into the finding when the model cites only the calculation.
        for calculation_id in calc_ids:
            calculation = calculations.get(str(calculation_id), {})
            for input_fact_id in calculation.get("input_fact_ids", []):
                if str(input_fact_id) in facts and str(input_fact_id) not in fact_ids:
                    fact_ids.append(str(input_fact_id))
        missing_facts = [str(item) for item in fact_ids if str(item) not in facts]
        unmatched_facts = [
            str(item)
            for item in fact_ids
            if str(item) in facts
            and facts[str(item)].get("validation", {}).get("status") not in {"quote_matched", "quote_and_number_matched"}
        ]
        semantically_unconfirmed = [
            str(item) for item in fact_ids
            if str(item) in facts and not facts[str(item)].get("validation", {}).get("calculation_eligible")
        ]
        missing_calculations = [str(item) for item in calc_ids if str(item) not in calculations]
        unsuccessful_calculations = [
            str(item) for item in calc_ids
            if str(item) in calculations and calculations[str(item)].get("status") != "calculated"
        ]
        finding["evidence_fact_ids"] = [str(item) for item in fact_ids]
        finding["calculation_ids"] = [str(item) for item in calc_ids]
        for field in narrative_fields:
            value = finding.get(field)
            if isinstance(value, str):
                for source_id in [*fact_ids, *calc_ids]:
                    value = value.replace(f"[{source_id}]", "")
                finding[field] = value
        finding["evidence_status"] = (
            "needs_review" if missing_facts or unmatched_facts or missing_calculations or unsuccessful_calculations or semantically_unconfirmed or not (fact_ids or calc_ids) else "source_text_matched_semantics_unreviewed"
        )
        if missing_facts or unmatched_facts or missing_calculations or unsuccessful_calculations or semantically_unconfirmed or not (fact_ids or calc_ids):
            finding["evidence_review_notes"] = {
                "missing_fact_ids": missing_facts,
                "unmatched_fact_ids": unmatched_facts,
                "missing_calculation_ids": missing_calculations,
                "unsuccessful_calculation_ids": unsuccessful_calculations,
                "semantically_unconfirmed_fact_ids": semantically_unconfirmed,
                "no_fact_citation": not bool(fact_ids or calc_ids),
            }

    result["facts"] = list(facts.values())
    data_coverage = _core_coverage(facts)
    declared_coverage = raw.get("data_coverage") if isinstance(raw.get("data_coverage"), dict) else {}
    allowed_pages = read_pages or set()
    coverage_statuses = {"not_yet_captured", "not_found_after_search", "not_disclosed", "ambiguous", "not_applicable"}
    for key, derived in data_coverage.items():
        if derived["status"] != "not_yet_captured":
            continue
        declared = declared_coverage.get(key)
        if not isinstance(declared, dict) or declared.get("status") not in coverage_statuses:
            continue
        source_pages = []
        raw_pages = declared.get("source_pages", [])
        if isinstance(raw_pages, list):
            for value in raw_pages:
                try:
                    page = int(value)
                except (TypeError, ValueError):
                    continue
                if page in allowed_pages and page not in source_pages:
                    source_pages.append(page)
        note = str(declared.get("note") or "").strip()[:500]
        status = str(declared["status"])
        if status != "not_yet_captured" and (not note or not source_pages):
            status = "not_yet_captured"
        derived.update({"status": status, "source_pages": sorted(source_pages), "note": note, "assessed_by": "model"})
    result["data_coverage"] = data_coverage
    _prepare_calculation_display(calculations)
    result["calculations"] = [
        {"calculation_id": calc_id, **value} for calc_id, value in calculations.items()
    ]
    result.setdefault("limitations", [])
    if not isinstance(result["limitations"], list):
        result["limitations"] = [str(result["limitations"])]
    unmatched = [item["fact_id"] for item in facts.values() if item.get("validation", {}).get("status") == "unmatched"]
    if unmatched:
        result["limitations"].append(f"有 {len(unmatched)} 条事实的来源摘录或金额未能自动匹配；引用它们的结论需要回看原文。")
    if facts:
        limitation = "自动证据状态说明来源摘录、数字及声明语境锚点已匹配；仅对成功提取布局的表格自动核对行列归属，未提取或无法识别的表格、主体范围、期间及会计含义仍需人工复核。"
        if limitation not in result["limitations"]:
            result["limitations"].append(limitation)
        result["evidence_review"] = {
            "status": "automatic_source_text_match_only",
            "human_reviewed": False,
            "checked": ["source_quote_match", "number_occurrence_when_numeric", "declared_context_anchor_presence", "retained_table_row_column_value_alignment"],
            "not_checked": ["tables_without_extracted_layout", "reporting_scope_semantics", "period_semantics", "accounting_meaning", "human_review"],
        }
    else:
        result["evidence_review"] = {"status": "no_structured_facts", "human_reviewed": False}
        result["analysis_status"] = "insufficient_data"
    resolved_statuses = {"captured_semantics_confirmed", "not_disclosed", "not_applicable"}
    unresolved_core = [key for key, item in result["data_coverage"].items() if item["status"] not in resolved_statuses]
    if unresolved_core and result["analysis_status"] == "complete":
        result["analysis_status"] = "partial"
    if unresolved_core:
        result["limitations"].append(
            "核心数据清单仍有未登记或语义待复核项（not_yet_captured 不等于年报未披露）：" + ", ".join(unresolved_core)
        )
    model_not_disclosed = [key for key, item in result["data_coverage"].items() if item["status"] == "not_disclosed"]
    if model_not_disclosed:
        result["limitations"].append(
            "模型将以下核心项标为年报未披露；程序只核对所列页码已完整读取，未独立证明全文检索穷尽：" + ", ".join(model_not_disclosed)
        )
    completeness_gaps: list[str] = []
    if facts and not result["findings"]:
        completeness_gaps.append("已登记事实，但没有提交带证据引用的偿债专题")
    if facts and not calculations:
        completeness_gaps.append("未保存任何指标计算尝试")
    if any(item.get("status") == "not_calculated" for item in calculations.values()):
        completeness_gaps.append("至少一项计算因事实或口径问题未能完成")
    if any(item.get("evidence_status") == "needs_review" for item in result["findings"] if isinstance(item, dict)):
        completeness_gaps.append("至少一项专题的事实或计算引用需要复核")
    if completeness_gaps and result["analysis_status"] == "complete":
        result["analysis_status"] = "partial"
    if completeness_gaps:
        for gap in completeness_gaps:
            if gap not in result["limitations"]:
                result["limitations"].append(gap)
    section_problems = _attach_section_evidence(result, facts, calculations, allowed_pages)
    if section_problems:
        result["limitations"].extend(
            item for item in section_problems
            if item not in result["limitations"]
        )
        if result["analysis_status"] == "complete":
            result["analysis_status"] = "partial"
    return result


def _snapshot(context: ReportContext, facts: dict[str, dict[str, Any]], calculations: dict[str, dict[str, Any]], messages: list[dict[str, Any]]) -> None:
    context.save_artifact(
        "solvency_working_state",
        {
            "module_version": MODULE_VERSION,
            "report_id": context.file_sha256,
            "facts": list(facts.values()),
            "calculations": calculations,
            "message_count": len(messages),
        },
    )


def _call_model(context: ReportContext, **request: Any) -> Any:
    try:
        return context.call_model(**request)
    except ModelCallError:
        raise
    except Exception as exc:
        raise _safe_model_error(exc) from exc


def _execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    context: ReportContext,
    facts: dict[str, dict[str, Any]],
    calculations: dict[str, dict[str, Any]],
    read_pages: set[int],
    page_texts: dict[int, str],
    page_reads: int,
) -> tuple[dict[str, Any], int, bool, dict[str, Any] | None]:
    if name == "search_pdf_pages":
        query = str(arguments.get("query") or "").strip()[:160]
        if not query:
            raise ValueError("搜索词不能为空。")
        hits = context.search_pages(query, limit=5)
        if any(term in query for term in ("到期", "流动性", "剩余期限", "未折现", "合同现金流")):
            year_match = re.search(r"20\d{2}", str(context.file_name or ""))
            report_year = year_match.group(0) if year_match else ""
            maturity_pages = []
            for page_data in context.pages:
                text = str(page_data.get("text") or "")
                compact = re.sub(r"\s+", "", text)
                if not ("未折现合同金额" in compact or "剩余到期期限" in compact):
                    continue
                if report_year and f"{report_year}年12月31日" not in compact:
                    continue
                maturity_pages.append({
                    "page": int(page_data.get("page", 0)),
                    "text": text[:MAX_TOOL_TEXT_CHARS],
                    "priority_reason": f"本报告年度 {report_year} 的期限表候选页；请完整读取并与相邻比较期页区分。" if report_year else "期限表候选页；请检查报告年度和表头。",
                })
            hit_by_page = {int(item.get("page", 0)): item for item in hits}
            priority_hits = []
            for candidate in maturity_pages:
                if candidate["page"] in hit_by_page:
                    hit = hit_by_page[candidate["page"]]
                    hit["priority_reason"] = candidate["priority_reason"]
                    priority_hits.append(hit)
                else:
                    priority_hits.append(candidate)
            prioritized_pages = {int(item.get("page", 0)) for item in priority_hits}
            remaining_hits = [item for item in hits if int(item.get("page", 0)) not in prioritized_pages]
            hits = priority_hits[:2] + remaining_hits
        return (
            {"query": query, "results": [{"page": item.get("page"), "text": str(item.get("text") or "")[:MAX_TOOL_TEXT_CHARS], **({"priority_reason": item["priority_reason"]} if item.get("priority_reason") else {})} for item in hits[:5]]},
            page_reads,
            False,
            None,
        )

    if name == "read_pdf_page":
        page = int(arguments.get("page_number", 0))
        if page < 1 or page > context.page_count:
            raise ValueError(f"页码必须在 1 至 {context.page_count} 之间。")
        is_new_read = page not in read_pages
        if is_new_read and page_reads >= MAX_PAGE_READS:
            raise ValueError("本次定向读取页数已达到模块预算；请使用已读资料提交有效的部分分析和限制。")
        text = context.read_page(page)
        if text is None:
            raise ValueError("该 PDF 页没有可提取的文字。")
        table_layout = _page_table_layout(context, page)
        evidence_text = f"{text}\n\n{table_layout}" if table_layout else text
        # The evidence validator receives this mutable cache. Keep explicitly
        # read pages available for the next submit_financial_facts call;
        # search hits alone are not treated as having read the source page.
        page_texts[page] = evidence_text
        read_pages.add(page)
        if is_new_read:
            page_reads += 1
        output = {"page": page, "text": text[:MAX_PAGE_CHARS]}
        if table_layout:
            output["table_layout"] = table_layout[:MAX_TABLE_LAYOUT_CHARS]
        return (output, page_reads, False, None)

    if name == "submit_financial_facts":
        added = add_facts(
            arguments.get("facts"),
            facts=facts,
            report_id=context.file_sha256,
            page_count=context.page_count,
            get_page=lambda page: page_texts.get(page),
        )
        revised_ids = {
            item["fact_id"] for item in added["facts"] if int(item.get("revision", 1)) > 1
        }
        invalidated_calculations = [
            calculation_id
            for calculation_id, calculation in calculations.items()
            if revised_ids.intersection(str(value) for value in calculation.get("input_fact_ids", []))
        ]
        for calculation_id in invalidated_calculations:
            calculations.pop(calculation_id, None)
        if invalidated_calculations:
            context.recorder.record(
                "solvency_calculations_invalidated",
                revised_fact_ids=sorted(revised_ids),
                calculation_ids=invalidated_calculations,
            )
        read_pages.update(
            int(item["page"])
            for fact in added["facts"]
            for item in fact.get("evidence", [])
            if item.get("matched")
        )
        public_facts = [
            {
                "fact_id": item["fact_id"],
                "fact_key": item["fact_key"],
                "label": item["label"],
                "revision": item.get("revision", 1),
                "validation": item["validation"],
            }
            for item in added["facts"]
        ]
        context.record_tool(name, {"count": added["accepted_count"]}, public_facts, pages=sorted(read_pages))
        return (
            {
                "accepted_count": added["accepted_count"],
                "evidence_match_count": added["evidence_match_count"],
                "unmatched_count": added["unmatched_count"],
                "invalidated_calculation_ids": invalidated_calculations,
                "facts": public_facts,
            },
            page_reads,
            False,
            None,
        )

    if name == "calculate_solvency_metric":
        try:
            result = calculate_metric(facts, arguments)
        except (ValueError, ArithmeticError) as exc:
            calculation_id = f"calc_{uuid.uuid4().hex[:14]}"
            input_fact_ids: list[str] = []
            for key in (
                "fact_ids",
                "debt_fact_ids",
                "cash_fact_ids",
                "restriction_fact_ids",
                "short_debt_fact_ids",
                "total_debt_fact_ids",
            ):
                values = arguments.get(key, [])
                if isinstance(values, list):
                    input_fact_ids.extend(str(value) for value in values)
            for key in ("cash_fact_id", "equity_fact_id"):
                value = arguments.get(key)
                if value:
                    input_fact_ids.append(str(value))
            result = {
                "calculation_id": calculation_id,
                "status": "not_calculated",
                "reason": str(exc),
                "metric": str(arguments.get("metric") or ""),
                "input_fact_ids": list(dict.fromkeys(input_fact_ids)),
                "formula": "未执行：输入事实或适用口径未通过检查",
                "reporting_instruction": "本项没有计算结果。最终分析只说明失败原因和缺失条件，不得自行手算、估算或报告该项的数值结果。",
            }
            calculations[calculation_id] = result
            context.record_calculation(
                f"未计算：{result['metric'] or '未知指标'}",
                inputs={"arguments": arguments},
                formula=result["formula"],
                output=result,
                rule_version=MODULE_VERSION,
            )
            context.record_tool(name, arguments, result, error=str(exc))
            return (result, page_reads, False, None)
        calculation_id = f"calc_{uuid.uuid4().hex[:14]}"
        result["metric"] = str(arguments.get("metric") or "")
        result["calculation_id"] = calculation_id
        calculations[calculation_id] = result
        context.record_calculation(
            result["name"],
            inputs={"fact_ids": result["input_fact_ids"], "input_labels": result["input_labels"]},
            formula=result["formula"],
            output=result,
            rule_version=MODULE_VERSION,
        )
        context.record_tool(name, arguments, result, pages=sorted(read_pages))
        return (result, page_reads, False, None)

    if name == "submit_final_analysis":
        analysis = arguments.get("analysis")
        if not isinstance(analysis, dict):
            raise ValueError("analysis 必须是对象。")
        result = _clean_final(analysis, facts, calculations, context.file_sha256, read_pages)
        context.save_artifact("solvency_final_result", result)
        context.record_tool(name, {"analysis_status": result.get("analysis_status")}, {"finding_count": len(result["findings"])}, pages=sorted(read_pages))
        return ({"saved": True, "finding_count": len(result["findings"]), "analysis_status": result["analysis_status"]}, page_reads, True, result)

    raise ValueError(f"模块工具不开放：{name}")


def run(context: ReportContext) -> dict[str, Any]:
    instruction = _load_prompt("专业指导_v1.md")
    data_contract = _load_prompt("数据与结果说明_v1.md")
    initial_pages = select_initial_pages(context.pages)
    if not initial_pages:
        initial_pages = list(context.initial_pages)
    if not initial_pages:
        result = _clean_final(
            {
                "analysis_status": "insufficient_data",
                "headline": "当前提取文本中没有找到可用于模块五分析的债务或资金页面。",
                "findings": [],
                "limitations": ["页面初筛没有找到债务或资金线索；请检查 PDF 是否可提取文字，或人工确认相关附注页。"],
            },
            {},
            {},
            context.file_sha256,
        )
        result["read_pages"] = []
        result["usage"] = {"prompt_tokens": 0, "completion_tokens": 0}
        result["prompt_version"] = MODULE_VERSION
        result["tool_call_count"] = 0
        context.save_artifact("solvency_final_result", result)
        context.progress("模块五：无法初筛报告", "未完成", "没有找到模块五可读的债务或资金页面。")
        return {"result": result, "trace": [], "read_pages": []}

    context.recorder.save_artifact("solvency_selected_initial_pages", initial_pages)
    context.progress("模块五：识别债务和资金页面", "已完成", f"初筛 {len(initial_pages)} 页；后续由 DeepSeek 自主查页。")
    facts: dict[str, dict[str, Any]] = {}
    calculations: dict[str, dict[str, Any]] = {}
    # Initial excerpts are leads, not full-page reads and not calculation evidence.
    read_pages: set[int] = set()
    page_texts: dict[int, str] = {}
    page_reads = 0
    model_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    related = _compact_related(context.related_results)
    user_task = {
        "report": {
            "file_name": context.file_name,
            "document_sha256": context.file_sha256,
            "page_count": context.page_count,
        },
        "initial_pages": initial_pages,
        "initial_excerpt_pages": [int(item["page"]) for item in initial_pages],
        "source_priority": "期限搜索结果若并列报告期和比较期，先完整读取报告年度的期限表。若续表页不重复列标题，读取相邻表头页，并在该表格事实的 evidence 中同时列出行页和表头页。",
        "first_search_suggestions": list(SEARCH_STARTERS),
        "other_module_clues_unverified": related,
            "task": "独立完成本年报的债务与资金压力分析。先读完整页并确认公司、年度、合并或母公司范围、表格行列和单位。执行顺序上，先读完年报中合并资产负债表的相关页，把表内可取得的合并资产总计、负债合计、流动资产合计、流动负债合计、所有者权益合计、货币资金、存货、短期借款、一年内到期非流动负债、长期借款、应付债券和租赁负债作为第一批事实登记；报告没有披露的项目不填零，也不拿母公司数替代合并数。先完成这份基础账本，再调用工具计算已有输入支持的核心指标，然后使用剩余查页机会调查现金流量表、利息与利润、附注期限、现金限制、非融资负债及本公司的特殊压力点；分析专题和结论仍由报告证据决定，不要求每个方向都写成固定板块。每个合计债务都必须梳理组成关系，确保不把父项和子项重复相加。只要输入事实已经齐备，就必须调用工具计算资产负债率、流动比率、融资债务合计、短期债务占比、净债务或净负债率、全年经营现金流对平均流动负债保障倍数、费用化利息保障倍数中适用的项目；资料不齐也应尝试有代表性的可算项，并把工具返回的具体缺口写入限制，不能把未计算说成已完成。金额优先引用工作账本中程序给出的 display_value_100m_cny（亿元）；不得自行换算或手算差额、合计、增长比例、债务占比等推导数值。任何推导金额或百分比都必须引用对应 calculation_id；没有计算记录时只作定性描述或说明缺口。期间现金流的 measurement_basis 用 period_flow 或 statement_cash_flow，时点余额用 ending_balance 或 statement_carrying_amount。自动摘录/数字匹配不等于财务语义核验；不要把系统匹配状态描述成已人工核实。请继续读取必要原文页，提交事实、调用适用计算，最后通过 submit_final_analysis 提交。",
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instruction + "\n\n" + data_contract},
        {"role": "user", "content": json.dumps(user_task, ensure_ascii=False)},
    ]
    tool_specs = _tools()
    total_tool_calls = 0
    retrieval_tool_calls = 0
    final_result: dict[str, Any] | None = None
    model_call_failed = False

    context.progress("模块五：DeepSeek 查页和核验事实", "进行中", "工具调用、原文及中间事实将逐步保存。")
    round_limit = int(os.getenv("FINLAB_SOLVENCY_TOOL_ROUNDS", str(MAX_ROUNDS)))
    round_limit = max(4, min(round_limit, 16))

    for round_index in range(round_limit):
        if total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
            break
        request_messages, retained_groups = _messages_for_model(
            messages,
            facts,
            calculations,
            read_pages,
            page_texts,
            include_initial_pages=round_index == 0,
            retrieval_budget_exhausted=(retrieval_tool_calls >= MAX_TOTAL_RETRIEVAL_TOOL_CALLS or page_reads >= MAX_PAGE_READS),
        )
        blocked_retrieval_tools = set()
        if retrieval_tool_calls >= MAX_TOTAL_RETRIEVAL_TOOL_CALLS:
            blocked_retrieval_tools.update({"search_pdf_pages", "read_pdf_page"})
        elif page_reads >= MAX_PAGE_READS:
            blocked_retrieval_tools.add("read_pdf_page")
        request_tools = [tool for tool in tool_specs if tool["function"]["name"] not in blocked_retrieval_tools]
        context.recorder.record(
            "solvency_model_context_prepared",
            stage="analysis",
            round=round_index + 1,
            original_characters=sum(len(str(item.get("content") or "")) for item in messages),
            sent_characters=sum(len(str(item.get("content") or "")) for item in request_messages),
            retained_history_groups=retained_groups,
            fact_count=len(facts),
            calculation_count=len(calculations),
        )
        try:
            response = _call_model(
                context,
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                messages=request_messages,
                tools=request_tools,
                tool_choice="auto",
                stream=False,
            )
        except ModelCallError as exc:
            context.recorder.record(
                "solvency_model_call_failed",
                stage="analysis",
                error_type=type(exc).__name__,
            )
            model_call_failed = True
            break
        usage = getattr(response, "usage", None)
        if usage is not None:
            model_usage["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            model_usage["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
        if not getattr(response, "choices", None):
            context.recorder.record(
                "solvency_model_call_failed",
                stage="analysis",
                error_type="EmptyModelResponse",
            )
            model_call_failed = True
            break
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        messages.append(message.model_dump(exclude_none=True))
        if not tool_calls:
            text = str(getattr(message, "content", "") or "")
            analysis = _parse_json_text(text)
            if analysis is None:
                analysis = {
                    "analysis_status": "partial",
                    "headline": text[:6000] or "模型结束本轮但未提交结构化分析结果。",
                    "limitations": ["模型未调用模块结果提交工具；以上自然语言尚未与事实 ID 自动关联。"],
                    "findings": [],
                }
            final_result = _clean_final(analysis, facts, calculations, context.file_sha256, read_pages)
            context.save_artifact("solvency_final_result", final_result)
            context.recorder.record("module_finalized_without_tool", analysis_status=final_result.get("analysis_status"))
            break

        for tool_call in tool_calls:
            name = str(tool_call.function.name)
            try:
                arguments = _parse_arguments(tool_call.function.arguments)
                retrieval_budget_error = None
                is_retrieval = name in {"search_pdf_pages", "read_pdf_page"}
                if is_retrieval:
                    if retrieval_tool_calls >= MAX_TOTAL_RETRIEVAL_TOOL_CALLS:
                        retrieval_budget_error = "本次总检索预算已用完。请先完成可计算指标，再在结果中列明未解决缺口。"
                    elif name == "read_pdf_page" and page_reads >= MAX_PAGE_READS:
                        retrieval_budget_error = "完整读页预算已用完。请使用持续工作账本中的已读页面，提交已有结论并标明缺口。"
                if retrieval_budget_error:
                    # A refused read/search is not an executed tool call and must
                    # not consume the budget needed for calculations/finalization.
                    output = {"error": retrieval_budget_error}
                    page_reads_after = page_reads
                    is_final = False
                    final = None
                else:
                    if total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
                        output = {"error": "本次已执行工具调用预算用完。请直接提交已取得的事实、计算结果和剩余限制。"}
                        context.record_tool(name, arguments, output, error=output["error"])
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": json.dumps(output, ensure_ascii=False),
                            }
                        )
                        _snapshot(context, facts, calculations, messages)
                        break
                    total_tool_calls += 1
                    if is_retrieval:
                        retrieval_tool_calls += 1
                    output, page_reads_after, is_final, final = _execute_tool(
                        name,
                        arguments,
                        context=context,
                        facts=facts,
                        calculations=calculations,
                        read_pages=read_pages,
                        page_texts=page_texts,
                        page_reads=page_reads,
                    )
                page_reads = page_reads_after
                if name not in {"submit_financial_facts", "calculate_solvency_metric", "submit_final_analysis"}:
                    context.record_tool(name, arguments, output, pages=sorted(read_pages))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(output, ensure_ascii=False, default=str),
                    }
                )
                _snapshot(context, facts, calculations, messages)
                if is_final:
                    final_result = final
                    break
            except Exception as exc:
                context.record_tool(name, {"arguments": tool_call.function.arguments}, {"error": str(exc)}, error=str(exc))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": str(exc)}, ensure_ascii=False),
                    }
                )
                _snapshot(context, facts, calculations, messages)
        if final_result is not None:
            break

    if final_result is None:
        context.progress("模块五：提交已完成分析", "进行中", "查页或模型轮次预算已到，要求 DeepSeek 保存已有结论及剩余限制。")
        messages.append(
            {
                "role": "user",
                "content": "立即基于已登记的来源事实和计算保存部分分析，明确未完成与未知项。系统的摘录/数字匹配不代表表格行列、单位或会计含义已经核验，不要声称已人工核实。",
            }
        )
        request_messages = _finalizer_messages(messages, facts, calculations, read_pages, page_texts)
        context.recorder.record(
            "solvency_model_context_prepared",
            stage="finalization",
            original_characters=sum(len(str(item.get("content") or "")) for item in messages),
            sent_characters=sum(len(str(item.get("content") or "")) for item in request_messages),
            retained_history_groups=0,
            fact_count=len(facts),
            calculation_count=len(calculations),
        )
        try:
            response = _call_model(
                context,
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                messages=request_messages,
                tools=[tool_specs[-1]],
                # DeepSeek may reject named/required tool-choice on thinking-mode calls.
                # The isolated finalizer prompt and single-tool set make auto unambiguous.
                tool_choice="auto",
                stream=False,
            )
        except ModelCallError as exc:
            context.recorder.record(
                "solvency_model_call_failed",
                stage="finalization",
                error_type=type(exc).__name__,
            )
            model_call_failed = True
            response = None
        usage = getattr(response, "usage", None) if response is not None else None
        if usage is not None:
            model_usage["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            model_usage["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
        if response is not None and getattr(response, "choices", None):
            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            if calls:
                final_call = next(
                    (call for call in calls if str(call.function.name) == "submit_final_analysis"),
                    None,
                )
                if final_call is None:
                    context.recorder.record(
                        "module_finalization_failed",
                        error_type="UnexpectedToolCall",
                        tool_names=[str(call.function.name) for call in calls],
                    )
                else:
                    try:
                        args = _parse_arguments(final_call.function.arguments)
                        _output, page_reads, _is_final, final_result = _execute_tool(
                            "submit_final_analysis",
                            args,
                            context=context,
                            facts=facts,
                            calculations=calculations,
                            read_pages=read_pages,
                            page_texts=page_texts,
                            page_reads=page_reads,
                        )
                    except Exception as exc:
                        context.recorder.record(
                            "module_finalization_failed",
                            error_type=type(exc).__name__,
                            error_message=str(exc)[:500],
                        )
            elif getattr(message, "content", None):
                model_text = str(message.content)
                analysis = _parse_json_text(model_text)
                if analysis is not None:
                    final_result = _clean_final(analysis, facts, calculations, context.file_sha256, read_pages)
                    context.save_artifact("solvency_final_result", final_result)
                else:
                    final_result = _clean_final(
                        {
                            "analysis_status": "partial" if facts else "insufficient_data",
                            "headline": model_text[:6000],
                            "findings": [],
                            "limitations": ["模型未按结构化工具格式提交；已保留原文，但专题和证据引用需复核。"],
                            "additional_sections": {"unstructured_model_conclusion": model_text[:12000]},
                        },
                        facts,
                        calculations,
                        context.file_sha256,
                        read_pages,
                    )
                    context.save_artifact("solvency_final_result", final_result)
        if final_result is None:
            fallback_headline = (
                "DeepSeek 调用未能完成；已保留此前保存的事实与计算，请按下方限制复核。"
                if model_call_failed
                else "本次分析未取得完整结构化结论；请查看已保存的事实和运行记录。"
            )
            final_result = _clean_final(
                {
                    "analysis_status": "partial" if facts else "insufficient_data",
                    "headline": fallback_headline,
                    "findings": [],
                    "limitations": [
                        "模型未能提交模块结论；下方事实和计算保留为部分结果，不能视为完整分析。"
                        if model_call_failed
                        else "模型未能在预算内提交模块结论。"
                    ],
                },
                facts,
                calculations,
                context.file_sha256,
                read_pages,
            )
            context.save_artifact("solvency_final_result", final_result)

    final_result["read_pages"] = sorted(read_pages)
    final_result["usage"] = model_usage
    final_result["prompt_version"] = MODULE_VERSION
    final_result["tool_call_count"] = total_tool_calls
    context.save_artifact("solvency_final_result", final_result)
    context.recorder.record(
        "solvency_analysis_completed",
        analysis_status=final_result.get("analysis_status"),
        fact_count=len(facts),
        calculation_count=len(calculations),
        finding_count=len(final_result.get("findings", [])),
        read_pages=final_result["read_pages"],
        tool_call_count=total_tool_calls,
        usage=model_usage,
    )
    successful_calculations = sum(item.get("status") in {"calculated", "not_applicable"} for item in calculations.values())
    progress_status = {
        "complete": "已完成",
        "partial": "部分完成",
        "insufficient_data": "未完成",
    }.get(str(final_result.get("analysis_status")), "部分完成")
    context.progress(
        "模块五：DeepSeek 查页和核验事实",
        progress_status,
        f"本轮查阅和事实登记已结束；分析状态：{final_result.get('analysis_status')}。",
    )
    context.progress(
        "模块五：提交已完成分析",
        progress_status,
        f"已保存结构化结果，分析状态：{final_result.get('analysis_status')}。",
    )
    context.progress("模块五：分析结果已保存", progress_status, f"事实 {len(facts)} 条，摘录与数字匹配 {sum(item.get('validation', {}).get('status') == 'quote_and_number_matched' for item in facts.values())} 条，计算尝试 {len(calculations)} 项（其中成功或不适用 {successful_calculations} 项），专题 {len(final_result.get('findings', []))} 项。")
    return {"result": final_result, "trace": [], "read_pages": final_result["read_pages"]}
