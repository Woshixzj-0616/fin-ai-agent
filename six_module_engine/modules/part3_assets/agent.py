"""Professional two-pass DeepSeek analysis for report asset quality."""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.core.legacy_runtime import _json_object, _safe_model_error
from backend.deepseek_client import ModelCallError
from modules.part3_assets.calculations import calculate_asset_metrics
from modules.part3_assets.discovery import discover_asset_pages
from modules.part3_assets.facts import merge_asset_facts, validate_asset_facts


MODULE_ID = "assets"
MODULE_VERSION = "v3.3.2"
REQUIRED_ANALYSIS_FIELDS = (
    "summary", "asset_map", "receivables", "inventory", "long_term_assets",
    "efficiency", "findings", "limitations", "handoff", "additional_facts",
)
MAX_ANALYSIS_ROUNDS = 4
MAX_PAGE_CHARS = 5_000
MAX_NEW_FACTS = 40
MAX_OUTPUT_TOKENS = int(os.getenv("ASSETS_MAX_OUTPUT_TOKENS", "12000"))
MAX_EXTRACTION_TOKENS = int(os.getenv("ASSETS_EXTRACTION_MAX_TOKENS", "18000"))
MAX_TOOL_STEP_TOKENS = int(os.getenv("ASSETS_TOOL_STEP_MAX_TOKENS", "7000"))
ANALYSIS_REASONING_EFFORT = os.getenv("ASSETS_REASONING_EFFORT", "low").strip().lower()
PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"

PDF_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_pdf_pages",
            "description": "搜索当前年报，定位资产负债表、应收、账龄、存货或项目附注。只用于获取线索，关键证据应再读取原文页。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 120}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pdf_page",
            "description": "读取当前年报指定 PDF 物理页原文，页码从 1 开始。",
            "parameters": {
                "type": "object",
                "properties": {"page_number": {"type": "integer", "minimum": 1}},
                "required": ["page_number"],
                "additionalProperties": False,
            },
        },
    },
]

ANALYSIS_TOOLS = PDF_TOOLS + [
    {
        "type": "function",
        "function": {
            "name": "submit_asset_fact",
            "description": "把从年报进一步查出的一个数字登记为候选事实。数字和原文摘录必须实际来自本年报；传入科目、期间、范围、单位及分段证据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "object",
                        "properties": {
                            "metric_key": {"type": "string"},
                            "original_label": {"type": "string"},
                            "dimension": {"type": "string"},
                            "value": {"type": "string"},
                            "unit": {"type": "string"},
                            "currency": {"type": "string"},
                            "period_type": {"type": "string", "enum": ["instant", "flow"]},
                            "period_label": {"type": "string"},
                            "period_start": {"type": "string"},
                            "period_end": {"type": "string"},
                            "as_of_date": {"type": "string"},
                            "reporting_scope": {"type": "string"},
                            "measurement_basis": {"type": "string"},
                            "adjustment_basis": {"type": "string"},
                            "evidence": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "page": {"type": "integer"},
                                        "role": {"type": "string", "enum": ["label", "value", "unit", "currency", "period", "scope"]},
                                        "quote": {"type": "string"},
                                    },
                                    "required": ["page", "role", "quote"],
                                    "additionalProperties": False,
                                },
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["metric_key", "original_label", "value", "unit", "currency", "period_type", "reporting_scope", "measurement_basis", "evidence"],
                        "additionalProperties": False,
                    }
                },
                "required": ["fact"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_asset_metrics",
            "description": "让程序依据已登记且摘录含有原科目行和数字的事实，计算可用的资产变化、总资产占比、周转和应收/存货总额准备净额勾稽。计算会记录公式和输入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 80},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]


def _read_prompt(name: str) -> str:
    return (PROMPT_ROOT / name).read_text(encoding="utf-8")


def _content(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def _invoke(
    context: Any,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, str] | None = None,
    max_tokens: int | None = None,
    thinking: str = "disabled",
    reasoning_effort: str | None = None,
):
    request: dict[str, Any] = {
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens or MAX_OUTPUT_TOKENS,
        # DeepSeek Flash defaults to high thinking. Extraction needs concise
        # structured facts; the analysis pass can explicitly request low effort.
        "extra_body": {"thinking": {"type": thinking}},
    }
    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort
    if tools:
        request["tools"] = tools
        request["tool_choice"] = "auto"
    if response_format:
        request["response_format"] = response_format
    return context.call_model(**request)


def _safe_error(exc: Exception) -> ModelCallError:
    if isinstance(exc, ModelCallError):
        return exc
    try:
        return _safe_model_error(exc)
    except Exception:
        return ModelCallError("模块三调用 DeepSeek 失败，已保留当前运行记录。请查看配置后重试。")


def _dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    context: Any,
    facts: list[dict[str, Any]],
    known_calculations: list[dict[str, Any]],
    pages_read: set[int],
) -> dict[str, Any]:
    if name == "search_pdf_pages":
        query = str(arguments.get("query") or "").strip()[:120]
        if not query:
            return {"error": "搜索词不能为空。"}
        hits = context.search_pages(query, limit=6)
        pages = [int(item["page"]) for item in hits if item.get("page") is not None]
        context.record_tool(name, {"query": query}, {"pages": pages}, pages=pages)
        return {"query": query, "results": [{"page": item.get("page"), "text": str(item.get("text") or "")[:2_400]} for item in hits[:6]]}
    if name == "read_pdf_page":
        try:
            page_number = int(arguments.get("page_number"))
        except (TypeError, ValueError):
            return {"error": "页码必须是整数。"}
        if not 1 <= page_number <= int(context.page_count):
            return {"error": f"页码超出范围；本 PDF 共 {context.page_count} 页。"}
        text = context.read_page(page_number)
        if text is None:
            return {"error": "该页没有可提取的原文。"}
        pages_read.add(page_number)
        content = text[:MAX_PAGE_CHARS]
        context.record_tool(name, {"page_number": page_number}, {"characters": len(content)}, pages=[page_number])
        return {"page": page_number, "text": content}
    if name == "submit_asset_fact":
        item = arguments.get("fact")
        previous_count = len(facts)
        facts, notes = merge_asset_facts(facts, [item] if isinstance(item, dict) else [], context)
        accepted = facts[previous_count] if len(facts) > previous_count else None
        if accepted:
            context.record_tool(name, item, {"fact_id": accepted["fact_id"], "validation": accepted["validation"]}, pages=[int(e["page"]) for e in accepted["evidence"] if e.get("page")])
        return {"accepted": bool(accepted), "fact": accepted, "review_notes": notes}
    if name == "calculate_asset_metrics":
        calculations, limitations = calculate_asset_metrics(facts)
        existing = {_calculation_fingerprint(item) for item in known_calculations}
        new_calculations = [item for item in calculations if _calculation_fingerprint(item) not in existing]
        for item in new_calculations:
            _record_calculation(context, item)
            known_calculations.append(item)
        requested_ids = {str(value) for value in arguments.get("fact_ids", []) if value}
        selected = [item for item in calculations if not requested_ids or requested_ids.intersection(item["input_fact_ids"])]
        context.record_tool(name, {"fact_ids": sorted(requested_ids)}, {"new_count": len(new_calculations), "returned_count": len(selected)})
        return {"calculations": selected[:100], "limitations": limitations}
    return {"error": "该工具不可用。"}


def _calculation_fingerprint(item: dict[str, Any]) -> str:
    return json.dumps(
        [item.get("name"), item.get("formula"), item.get("input_fact_ids"), item.get("input_calculation_ids"), item.get("output")],
        ensure_ascii=False,
        sort_keys=True,
    )


def _record_calculation(context: Any, item: dict[str, Any]) -> None:
    context.record_calculation(
        str(item.get("name") or "模块三计算"),
        inputs={
            "fact_ids": item.get("input_fact_ids"),
            "calculation_ids": item.get("input_calculation_ids"),
            "facts": item.get("inputs"),
        },
        formula=str(item.get("formula") or ""),
        output={"value": item.get("output"), "unit": item.get("unit"), "note": item.get("note")},
        rule_version=str(item.get("rule_version") or MODULE_VERSION),
    )


def _final_json_request(context: Any, messages: list[dict[str, Any]], *, reason: str):
    messages.append({
        "role": "user",
        "content": (
            "现在停止调用工具，立即交付最终结果。只依据已验证事实、已有程序计算和已经读取的原文；"
            "不补造数字，证据不足处写入限制。必须返回有效 JSON 对象，保留 asset_map、receivables、"
            "inventory、long_term_assets、efficiency、findings、limitations、handoff 等模块内容。"
            f"当前要求：{reason}"
        ),
    })
    return _invoke(
        context,
        messages,
        response_format={"type": "json_object"},
        max_tokens=MAX_OUTPUT_TOKENS,
        thinking="disabled",
    )


def _tool_loop(context: Any, messages: list[dict[str, Any]], *, facts, calculations, pages_read, available_tools):
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for round_index in range(MAX_ANALYSIS_ROUNDS):
        response = _invoke(
            context,
            messages,
            tools=available_tools,
            max_tokens=MAX_TOOL_STEP_TOKENS,
            thinking="enabled",
            reasoning_effort=ANALYSIS_REASONING_EFFORT,
        )
        response_usage = getattr(response, "usage", None)
        if response_usage:
            usage["prompt_tokens"] += int(getattr(response_usage, "prompt_tokens", 0) or 0)
            usage["completion_tokens"] += int(getattr(response_usage, "completion_tokens", 0) or 0)
        if not getattr(response, "choices", None):
            raise ModelCallError("DeepSeek 没有返回模块三分析结果。")
        assistant_message = response.choices[0].message
        tool_calls = getattr(assistant_message, "tool_calls", None) or []
        messages.append(assistant_message.model_dump(exclude_none=True))
        if not tool_calls:
            content = _content(messages)
            if _json_object(content):
                return content, usage
            # Some responses may stop after prose or incomplete JSON. Give the
            # model one bounded, schema-constrained opportunity to finalize.
            final_response = _final_json_request(context, messages, reason="上一条没有形成有效 JSON，请整理已知内容。")
            usage = _add_usage(usage, _usage(final_response))
            return _response_content(final_response), usage
        for tool_call in tool_calls:
            function = tool_call.function
            try:
                arguments = json.loads(function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                output = _dispatch_tool(
                    function.name,
                    arguments,
                    context=context,
                    facts=facts,
                    known_calculations=calculations,
                    pages_read=pages_read,
                )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                output = {"error": f"工具参数或输入有误：{type(exc).__name__}"}
                context.record_tool(function.name, getattr(function, "arguments", ""), output, error=type(exc).__name__)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(output, ensure_ascii=False, default=str)[:18_000]})
        if round_index == MAX_ANALYSIS_ROUNDS - 1:
            final_response = _final_json_request(
                context,
                messages,
                reason="原文追查轮数已到上限；请根据当前结果总结，并把未解决问题写入 limitations。",
            )
            return _response_content(final_response), _add_usage(usage, _usage(final_response))
    return "", usage


def _metadata(raw: Any, context: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "company": str(raw.get("company") or "").strip()[:240],
        "security_code": str(raw.get("security_code") or "").strip()[:32],
        "report_year": str(raw.get("report_year") or "").strip()[:12],
        "period_start": str(raw.get("period_start") or "").strip()[:20],
        "period_end": str(raw.get("period_end") or "").strip()[:20],
        "reporting_scope": str(raw.get("reporting_scope") or "unknown").strip()[:60],
        "currency": str(raw.get("currency") or "unknown").strip()[:24],
        "file_name": context.file_name,
        "report_id": context.report_id,
        "file_sha256": context.file_sha256,
        "page_count": context.page_count,
    }


def _render_summary(result: dict[str, Any]) -> str:
    summary = str(result.get("summary") or "").strip()
    quality_issues = result.get("quality_issues")
    if isinstance(quality_issues, list) and quality_issues:
        warning = "数据复核提醒：" + "；".join(str(item) for item in quality_issues[:3])
        summary = f"{warning}\n\n{summary}" if summary else warning
    if summary:
        return summary[:2_400]
    findings = result.get("findings")
    if isinstance(findings, list):
        parts = [str(item.get("title") or item.get("claim") or "").strip() for item in findings if isinstance(item, dict)]
        return "；".join(part for part in parts[:6] if part)[:2_400]
    return "模块三已完成原文检查和数值计算；请查看各专题结果、来源与限制。"


_MONEY_MENTION = re.compile(r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>亿元|万元|亿|万|元)")
_TRANSFER_TERMS = re.compile(r"转入固定资产|转固")


def _money_in_yuan(amount_text: str, unit: str) -> Decimal | None:
    try:
        value = Decimal(amount_text.replace(",", ""))
    except Exception:
        return None
    multiplier = {"亿元": Decimal(100_000_000), "亿": Decimal(100_000_000), "万元": Decimal(10_000), "万": Decimal(10_000), "元": Decimal(1)}.get(unit)
    return value * multiplier if multiplier is not None else None


def _transfer_claims(text: str, source: str) -> list[tuple[str, Decimal]]:
    claims: list[tuple[str, Decimal]] = []
    for term in _TRANSFER_TERMS.finditer(text):
        start = max(0, term.start() - 24)
        end = min(len(text), term.end() + 36)
        window = text[start:end]
        mentions = list(_MONEY_MENTION.finditer(window))
        if not mentions:
            continue
        mention = min(mentions, key=lambda item: min(abs(item.start() - (term.start() - start)), abs(item.end() - (term.end() - start))))
        value = _money_in_yuan(mention.group("amount"), mention.group("unit"))
        if value is not None:
            claims.append((source, value))
    return claims


def _iter_strings(value: Any, path: str = ""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, f"{path}[{index}]")


def _quality_issues(extraction_status: str, facts: list[dict[str, Any]], calculations: list[dict[str, Any]], analysis: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if extraction_status != "complete":
        issues.append(f"事实抽取状态为 {extraction_status}，本轮抽取可能不完整。")
    core_metrics = {"total_assets", "accounts_receivable", "inventory", "operating_revenue", "operating_cost"}
    core_facts = [fact for fact in facts if fact.get("metric_key") in core_metrics]
    if core_facts and not calculations and not any(fact.get("calculation_ready") for fact in core_facts):
        issues.append(f"识别到 {len(core_facts)} 条核心财务事实，但没有事实通过口径校验，程序未能生成计算结果。")

    claims: list[tuple[str, Decimal]] = []
    for path, text in _iter_strings(analysis.get("long_term_assets"), "long_term_assets"):
        claims.extend(_transfer_claims(text, path))
    for path, text in _iter_strings(analysis.get("summary"), "summary"):
        claims.extend(_transfer_claims(text, path))
    for path, text in _iter_strings(analysis.get("findings"), "findings"):
        claims.extend(_transfer_claims(text, path))
    unique_values = {amount for _source, amount in claims}
    if len(unique_values) > 1:
        rendered = "、".join(f"{value / Decimal(100_000_000):.2f}亿元" for value in sorted(unique_values))
        issues.append(f"长期资产板块对在建工程转固金额出现不一致表述（{rendered}），需按年报原文复核。")
    return issues


def _audit_finding_references(
    analysis: dict[str, Any],
    facts: list[dict[str, Any]],
    calculations: list[dict[str, Any]],
    *,
    page_count: int,
    pages_read: set[int],
) -> list[str]:
    """Check that user-visible findings point to registered facts and calculations."""
    findings = analysis.get("findings")
    if not isinstance(findings, list):
        return ["分析结果没有 findings 列表；具体结论仍需人工查看原始结果。"]
    facts_by_id = {str(fact.get("fact_id")): fact for fact in facts}
    calculations_by_id = {str(item.get("calculation_id")): item for item in calculations}
    audit_notes: list[str] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            audit_notes.append(f"第{index}条发现不是对象，无法核对引用。")
            continue
        issues: list[str] = []
        evidence_refs = finding.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            evidence_refs = []
            issues.append("缺少 evidence_refs。")
        resolved_evidence = 0
        for ref in evidence_refs[:40]:
            if not isinstance(ref, dict):
                issues.append("存在格式无效的证据引用。")
                continue
            fact_id = str(ref.get("fact_id") or "").strip()
            try:
                page = int(ref.get("page"))
            except (TypeError, ValueError):
                page = 0
            fact = facts_by_id.get(fact_id) if fact_id else None
            if fact_id and fact is None:
                issues.append(f"未登记事实 {fact_id}。")
                continue
            if page and not 1 <= page <= page_count:
                issues.append(f"页码 {page} 超出报告范围。")
                continue
            if fact:
                matched_pages = {
                    int(item["page"])
                    for item in fact.get("evidence", [])
                    if item.get("page") is not None and item.get("quote_matched")
                }
                if not matched_pages:
                    issues.append(f"{fact_id} 没有任何匹配原文的摘录。")
                    continue
                if page and page not in matched_pages:
                    issues.append(f"{fact_id} 没有匹配第{page}页的原文摘录。")
                    continue
                if not page:
                    ref["pages"] = sorted(matched_pages)
                resolved_evidence += 1
            elif page and page in pages_read:
                issues.append(f"第{page}页已读取，但该引用未关联通过摘录检查的事实 ID。")
            elif page:
                issues.append(f"第{page}页不在本轮已读取页面中。")
            else:
                issues.append("证据引用缺少 fact_id 或有效页码。")

        calculation_refs = finding.get("calculation_refs")
        if not isinstance(calculation_refs, list):
            calculation_refs = []
            issues.append("calculation_refs 应为数组。")
        for calculation_id in calculation_refs[:40]:
            if str(calculation_id) not in calculations_by_id:
                issues.append(f"未登记计算 {calculation_id}。")

        if issues:
            finding["citation_audit"] = {"status": "needs_review", "issues": issues}
            audit_notes.extend(f"第{index}条发现：{issue}" for issue in issues)
        elif resolved_evidence:
            finding["citation_audit"] = {
                "status": "source_linked",
                "resolved_fact_refs": resolved_evidence,
                "resolved_calculation_refs": len(calculation_refs),
                "note": "仅核对引用 ID、页码和摘录关联；不代表表格行列语义或结论因果已独立验证。",
            }
        else:
            finding["citation_audit"] = {"status": "missing", "issues": ["未提供可核对的来源事实。"]}
            audit_notes.append(f"第{index}条发现未提供可核对的来源事实。")
    return audit_notes


def analyze_asset_report(context: Any) -> dict[str, Any]:
    context.progress("模块三：资产质量与经营效率", "进行中", "正在为本报告定位资产负债表、应收、存货和长期资产附注。")
    initial_pages, search_log = discover_asset_pages(context)
    if not initial_pages:
        raise ModelCallError("年报中没有找到可提取的资产相关文字，无法开始模块三分析。")
    pages_read = {int(page["page"]) for page in initial_pages}
    context.save_artifact("assets_initial_material", initial_pages)
    context.progress("模块三：抽取资产事实", "进行中", f"已选择 {len(initial_pages)} 个候选原文页，模型会按需要继续查页。")

    extraction_messages = [
        {"role": "system", "content": _read_prompt("模块三专业指导_v1.md") + "\n\n" + _read_prompt("事实抽取_v1.md")},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "从当前报告抽取资产结构、应收、存货、长期资产及适用效率分析的关键事实。引用的 PDF 页码必须来自以下原文。",
                    "report_id": context.report_id,
                    "file_name": context.file_name,
                    "page_count": context.page_count,
                    "candidate_pages": initial_pages,
                    "output_reminder": "返回事实抽取_v1.md 指定的 JSON 对象。不要根据文件名猜公司或报告年度。",
                },
                ensure_ascii=False,
            ),
        },
    ]
    context.save_artifact("assets_extraction_prompt", extraction_messages)
    try:
        extraction_response = _invoke(
            context,
            extraction_messages,
            response_format={"type": "json_object"},
            max_tokens=MAX_EXTRACTION_TOKENS,
            thinking="disabled",
        )
    except Exception as exc:
        raise _safe_error(exc) from exc
    extraction_usage = _usage(extraction_response)
    extraction_content = _response_content(extraction_response)
    parsed_extraction = _json_object(extraction_content)
    finish_reason = str(getattr(extraction_response.choices[0], "finish_reason", "") or "") if getattr(extraction_response, "choices", None) else ""
    extraction_status = (
        "truncated" if finish_reason == "length"
        else "empty_response" if not extraction_content.strip()
        else "invalid_json" if parsed_extraction is None
        else "complete" if isinstance(parsed_extraction.get("facts"), list)
        else "missing_facts_field"
    )
    extracted = parsed_extraction or {}
    metadata = _metadata(extracted.get("metadata"), context)
    facts, source_notes = validate_asset_facts(extracted.get("facts", []), context)
    extraction_notes = extracted.get("extraction_notes", [])
    if not isinstance(extraction_notes, list):
        extraction_notes = []
    extraction_notes = [str(note)[:600] for note in extraction_notes[:80]]
    calculations, calculation_limits = calculate_asset_metrics(facts)
    for item in calculations:
        _record_calculation(context, item)

    context.progress("模块三：核对资产变化", "进行中", f"已登记 {len(facts)} 条候选事实，完成 {len(calculations)} 项适用计算，正在分析重点及资料缺口。")
    analysis_state = {
        "report": metadata,
        "candidate_facts": facts,
        "calculation_results": calculations,
        "extraction_notes": extraction_notes,
        "source_validation_notes": source_notes[:80],
        "calculation_limitations": calculation_limits[:80],
    }
    analysis_messages = [
        {"role": "system", "content": _read_prompt("模块三专业指导_v1.md") + "\n\n" + _read_prompt("分析与结果_v1.md")},
        {"role": "user", "content": "请分析以下已从本报告抽取并标注出处的资产事实及精确计算。分析可自由选择专题；若缺资料，使用搜索和读页工具，若发现新数字先登记事实再按需运行程序计算。所有事实摘录、计算和结论都要对应来源；不要推断证据未支持的业务原因。\n\n" + json.dumps(analysis_state, ensure_ascii=False, default=str)},
    ]
    context.save_artifact("assets_analysis_context", analysis_state)
    context.progress("模块三：形成专题结果", "进行中", "模型正在结合资产结构、计算和原文证据形成分析。")
    analysis_error = ""
    try:
        analysis_content, analysis_usage = _tool_loop(
            context,
            analysis_messages,
            facts=facts,
            calculations=calculations,
            pages_read=pages_read,
            available_tools=ANALYSIS_TOOLS,
        )
    except Exception as exc:
        safe_error = _safe_error(exc)
        analysis_error = str(safe_error)
        analysis_content = ""
        analysis_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    analysis_object = _json_object(analysis_content)
    missing_analysis_fields = [
        field for field in REQUIRED_ANALYSIS_FIELDS
        if not isinstance(analysis_object, dict) or field not in analysis_object
    ]
    required_types = {
        "summary": str,
        "findings": list,
        "limitations": list,
        "handoff": list,
        "additional_facts": list,
    }
    required_types.update({
        field: (dict, list, str)
        for field in ("asset_map", "receivables", "inventory", "long_term_assets", "efficiency")
    })
    analysis_shape_issues = [
        f"{field} 类型应为 {', '.join(kind.__name__ for kind in expected) if isinstance(expected, tuple) else expected.__name__}"
        for field, expected in required_types.items()
        if isinstance(analysis_object, dict) and field in analysis_object
        and not isinstance(analysis_object[field], expected)
    ]
    analysis_status = (
        "interpretation_failed" if analysis_error
        else "analysis_text_needs_format_review" if not analysis_object
        else "partial" if missing_analysis_fields or analysis_shape_issues
        else "complete"
    )
    additional_facts = []
    if isinstance(analysis_object, dict):
        raw_additions = analysis_object.get("additional_facts", [])
        if isinstance(raw_additions, list) and raw_additions:
            previous_count = len(facts)
            facts, addition_notes = merge_asset_facts(facts, raw_additions[:MAX_NEW_FACTS], context)
            additional_facts = facts[previous_count:]
            source_notes.extend(addition_notes)
            refreshed, refreshed_limits = calculate_asset_metrics(facts)
            known = {_calculation_fingerprint(item) for item in calculations}
            for item in refreshed:
                if _calculation_fingerprint(item) not in known:
                    calculations.append(item)
                    _record_calculation(context, item)
            calculation_limits = refreshed_limits
    else:
        analysis_object = {
            "summary": (f"资产事实抽取和 {len(calculations)} 项计算已保存；分析解读未完成。{analysis_error}" if analysis_error else "模型未返回有效 JSON，原始解读已保留供复核。"),
            "analysis_text": analysis_content,
            "analysis_error": analysis_error,
            "findings": [],
        }

    evidence_reference_notes = _audit_finding_references(
        analysis_object,
        facts,
        calculations,
        page_count=int(context.page_count),
        pages_read=pages_read,
    ) if isinstance(analysis_object, dict) else []
    quality_issues = _quality_issues(extraction_status, facts, calculations, analysis_object)
    if quality_issues and analysis_status == "complete":
        analysis_status = "partial"
    core_metrics = {"total_assets", "accounts_receivable", "inventory", "operating_revenue", "operating_cost"}
    core_fact_count = sum(1 for fact in facts if fact.get("metric_key") in core_metrics)
    calculation_status = "available" if calculations else ("blocked" if core_fact_count else "not_applicable")
    result = {
        **analysis_object,
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "analysis_status": analysis_status,
        "analysis_missing_fields": missing_analysis_fields,
        "analysis_shape_issues": analysis_shape_issues,
        "extraction_status": extraction_status,
        "report": metadata,
        "facts": facts,
        "calculations": calculations,
        "evidence_notes": source_notes[:120],
        "evidence_reference_notes": evidence_reference_notes[:120],
        "extraction_notes": extraction_notes,
        "calculation_limitations": calculation_limits[:120],
        "discovery": {"candidate_page_count": len(initial_pages), "search_log": search_log},
        "usage": _add_usage(extraction_usage, analysis_usage),
        "prompt_version": "模块三专业指导_v1 / 事实抽取_v1 / 分析与结果_v1",
        "requested_new_fact_count": len(additional_facts),
        "analysis_error": analysis_error,
        "calculation_status": calculation_status,
        "quality_issues": quality_issues,
    }
    result["summary"] = _render_summary(result)
    context.save_artifact("assets_extraction_result", {"metadata": metadata, "facts": facts, "notes": extraction_notes, "source_notes": source_notes})
    context.save_artifact("assets_calculated_result", calculations)
    context.save_artifact("assets_analysis_result", analysis_object)
    final_status = "已完成" if analysis_status == "complete" else "有待复核"
    context.progress("模块三：资产质量与经营效率", final_status, f"已形成 {len(result.get('findings', [])) if isinstance(result.get('findings'), list) else 0} 条重点分析，登记 {len(facts)} 条事实，程序计算 {len(calculations)} 项。")
    return {"result": result, "trace": [], "read_pages": sorted(pages_read)}


def _response_content(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    return str(getattr(choices[0].message, "content", "") or "") if choices else ""


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
    }


def _add_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    return {key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in ("prompt_tokens", "completion_tokens")}
