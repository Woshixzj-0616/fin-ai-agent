"""V3.0.1-compatible entry point for module six."""

from __future__ import annotations

from backend.core.context import ReportContext
import json
import re
from pathlib import Path
from typing import Any

from backend.deepseek_client import ModelCallError
from modules.part6_disclosure.agent import (
    MODULE_ID,
    MODULE_VERSION,
    _normalise_quote,
    _parse_object,
    _safe_model_error,
    analyze_disclosure_report,
)


def run(context: ReportContext) -> dict:
    """Analyze an annual report and preserve all module-specific result fields."""
    return analyze_disclosure_report(context)


def partial_result_from_checkpoint(checkpoint: dict, reason: str) -> dict:
    """Adapt the module's saved checkpoint for the workbench without hiding raw fields."""
    provisional = checkpoint.get("provisional_result")
    overview = checkpoint.get("submitted_overview")
    result = dict(provisional) if isinstance(provisional, dict) else {}
    if isinstance(overview, dict):
        result = {**overview, **result}
    result["module_id"] = MODULE_ID
    result["module_version"] = MODULE_VERSION
    result["module_title"] = "披露可信度与特殊事项"
    result["findings"] = checkpoint.get("findings") or result.get("findings") or []
    result["impacts"] = checkpoint.get("impacts") or result.get("impacts") or []
    result["calculations"] = checkpoint.get("calculations") or result.get("calculations") or []
    result["read_pages"] = checkpoint.get("read_pages") or result.get("read_pages") or []
    result["executive_summary"] = result.get("executive_summary") or "模型在完整分析提交前中断；下方保留已提交的检查点内容。"
    result["audit_profile"] = result.get("audit_profile") if isinstance(result.get("audit_profile"), dict) else {}
    result["report_context"] = result.get("report_context") if isinstance(result.get("report_context"), dict) else checkpoint.get("report_context", {})
    result["coverage"] = result.get("coverage") if isinstance(result.get("coverage"), list) else []
    result["limitations"] = result.get("limitations") if isinstance(result.get("limitations"), list) else []
    result["limitations"] = [*result["limitations"], f"本次分析未完成：{reason}"]
    result["reading_guide"] = result.get("reading_guide") or "以下内容是分析检查点，不是完整结论；请结合已读取页和未完成原因复核。"
    quality = result.get("run_quality") if isinstance(result.get("run_quality"), dict) else {}
    gaps = list(quality.get("critical_gaps") or [])
    gaps.extend(str(item) for item in (checkpoint.get("completion_issues") or []) if item)
    if reason and reason not in gaps:
        gaps.append(f"分析中断：{reason}")
    quality["critical_gaps"] = list(dict.fromkeys(gaps))
    quality["checkpoint_status"] = checkpoint.get("checkpoint_status", "partial")
    quality["notice"] = quality.get("notice") or "检查点只表示已保存的模型工作，不代表会计或审计判断已验证。"
    result["run_quality"] = quality
    result["checkpoint"] = {
        "status": checkpoint.get("checkpoint_status", "partial"),
        "model_turns_completed": checkpoint.get("model_turns_completed"),
        "model_usage_so_far": checkpoint.get("model_usage_so_far", {}),
        "completion_issues": checkpoint.get("completion_issues", []),
    }
    result["partial_reason"] = reason
    return result


def insufficient_material_result(file_name: str, page_count: int) -> dict:
    return {
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "module_title": "披露可信度与特殊事项",
        "report_context": {"file_name": file_name, "page_count": page_count},
        "executive_summary": "PDF 中没有可提取的年报正文，模块六无法核对审计意见或特殊事项。",
        "audit_profile": {},
        "findings": [],
        "impacts": [],
        "coverage": [],
        "calculations": [],
        "limitations": ["该 PDF 可能是扫描件或文字层损坏；当前工作台没有 OCR 结果。"],
        "reading_guide": "请提供带文字层的年报 PDF 后重新分析。",
        "run_quality": {"critical_gaps": ["没有可供模块六核对的年报正文。"], "notice": "没有分析结论。"},
        "read_pages": [],
    }


def answer_follow_up(
    context: ReportContext,
    *,
    initial_result: dict[str, Any],
    history: list[dict[str, Any]],
    question: str,
) -> dict[str, Any]:
    """Answer a follow-up with module-six guidance, saved results, and fresh PDF retrieval."""
    prompt_path = Path(__file__).resolve().parent / "prompts" / "专业指导_v1.md"
    if not prompt_path.is_file():
        raise ModelCallError("未找到模块六的专业指导文件，无法继续追问。")
    prompt = prompt_path.read_text(encoding="utf-8")
    system = prompt + """

【本轮追问规则】
你正在回答同一份年报的模块六追问。每次调用都是独立请求，不能假设模型记得前次对话。
本轮用户消息包含本次模块六已有结果、最近问答和刚从 PDF 搜索并读取的页面。先用这些材料回答；不得把摘要、检查范围或引用文字匹配说成审计师已确认，也不得将未核实推断写成事实。
只引用本轮实际提供的 PDF 物理页。明确区分年报披露、程序摘录核对、分析推断和待人工复核事项。材料不足时直接说尚不能判断，并说明还需查什么。
严格返回 JSON：{"answer":"面向用户的中文回答","evidence":[{"page":1,"quote":"本轮读取页面中的连续原文摘录","supports":"该摘录支持的有限事实"}],"limitations":["需要人工复核的限制"]}。证据不适用时返回空数组。
"""
    context.tool_activity("search_report", question[:180])
    hits = context.search_pages(question, limit=6)
    pages_by_number: dict[int, str] = {}
    for item in hits:
        try:
            number = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        if number in pages_by_number or number < 1 or number > context.page_count:
            continue
        text = context.read_page(number)
        if text:
            pages_by_number[number] = str(text)[:6000]
        if len(pages_by_number) >= 4:
            break

    snapshot_keys = (
        "module_id", "module_version", "module_title", "report_context", "executive_summary",
        "audit_profile", "findings", "impacts", "coverage", "calculations", "limitations",
        "reading_guide", "quality_flags", "run_quality", "partial_reason", "checkpoint",
        "read_pages",
    )
    snapshot = {key: initial_result.get(key) for key in snapshot_keys if key in initial_result}
    recent_history = [
        {"question": str(turn.get("question") or "")[:1200], "answer": str(turn.get("answer") or "")[:1800]}
        for turn in history[-5:]
        if turn.get("status") == "completed"
    ]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"本次报告文件：{context.file_name}\nPDF 物理页数：{context.page_count}\n"
            f"模块六现有结果（可能是部分结果）：\n{json.dumps(snapshot, ensure_ascii=False, default=str)[:36000]}\n\n"
            f"最近追问：\n{json.dumps(recent_history, ensure_ascii=False)}\n\n"
            f"本轮新检索并实际读取的年报原文页面：\n{json.dumps([{'page': page, 'text': text} for page, text in pages_by_number.items()], ensure_ascii=False)[:28000]}\n\n"
            f"本轮问题：{question[:1200]}"
        )},
    ]
    context.save_artifact("disclosure_followup_context", {
        "question": question,
        "result_keys": list(snapshot),
        "history_count": len(recent_history),
        "retrieved_pages": sorted(pages_by_number),
    })
    try:
        response = context.call_model(
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=5_000,
            stream=False,
        )
    except Exception as exc:
        raise _safe_model_error(exc) from exc
    choices = getattr(response, "choices", None) or []
    if not choices or not getattr(choices[0].message, "content", None):
        raise ModelCallError("DeepSeek 没有生成本轮追问答复。")
    raw = str(choices[0].message.content)
    parsed = _parse_object(raw) or {}
    answer = str(parsed.get("answer") or raw).strip()[:6000]
    evidence_rows: list[dict[str, Any]] = []
    for item in parsed.get("evidence", []) if isinstance(parsed.get("evidence"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        quote = str(item.get("quote") or "").strip()
        page_text = pages_by_number.get(page, "")
        matched = bool(page_text and quote and _normalise_quote(quote) in _normalise_quote(page_text))
        evidence_rows.append({
            "page": page,
            "quote": quote,
            "supports": str(item.get("supports") or ""),
            "verification": "quote_present" if matched else "quote_not_found_or_page_not_read",
            "quote_present": matched,
        })
    cited = sorted({int(value) for value in re.findall(r"(?:PDF\s*第\s*|第\s*)(\d+)\s*页", answer)})
    unsupported = [page for page in cited if page not in pages_by_number]
    if unsupported:
        answer += "\n\n本轮回答提到的页码中，有页码没有在本轮实际读取；这些引用无法由程序核对，请打开原文复核。"
    if evidence_rows:
        answer += "\n\n**本轮原文回查**\n"
        answer += "\n".join(
            f"- 第 {row['page']} 页（{'摘录文字可回查' if row['quote_present'] else '摘录未核对成功'}）：“{row['quote'][:500]}”"
            for row in evidence_rows
        )
        answer += "\n摘录文字可回查只说明文字存在于所引页，不等于专业判断已被审计师确认。"
    for limitation in parsed.get("limitations", []) if isinstance(parsed.get("limitations"), list) else []:
        value = str(limitation).strip()
        if value and value not in answer:
            answer += f"\n\n**复核限制：**{value[:500]}"
    usage_obj = getattr(response, "usage", None)
    usage = {
        "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
    }
    trace = [{"tool": "disclosure_followup_retrieval", "pages": sorted(pages_by_number), "evidence": evidence_rows}]
    return {
        "answer": answer,
        "trace": trace,
        "read_pages": sorted(pages_by_number),
        "usage": usage,
        "unsupported_pages": unsupported,
    }
