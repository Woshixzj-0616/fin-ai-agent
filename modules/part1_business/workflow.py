"""Two-stage report extraction and interpretation for module one."""

from __future__ import annotations

import json
from typing import Any, Callable

from backend.deepseek_client import ModelCallError
from backend.core.legacy_runtime import (
    MODEL,
    TOOLS,
    _client,
    _json_object,
    _run_tool_loop,
    _safe_model_error,
    recorded_completion as _run_recorded_completion,
)
from modules.part1_business.prompts import business_prompt
from modules.part1_business.results import _normalize_business_analysis
from modules.part1_business.merge import _merge_interpretation
from modules.part1_business.retrieval import _business_preflight_search


def analyze_business_report(
    *,
    file_name: str,
    page_count: int,
    initial_pages: list[dict[str, Any]],
    get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
    on_tool: Callable[[str, str], None] | None = None,
    on_stage: Callable[[str, str, str], None] | None = None,
    recorder: Any = None,
) -> dict[str, Any]:
    provided_pages = {int(page["page"]) for page in initial_pages}
    retrieval: dict[str, Any] | None = None
    retrieval_trace: list[dict[str, Any]] = []
    model_pages = list(initial_pages)
    if on_stage:
        on_stage("定位模块一必查内容", "进行中", "按业务、行业、战略、收入、依赖和风险主题检索全文。")
    retrieval, extra_pages, retrieval_trace = _business_preflight_search(
        search_pages=search_pages,
        get_page=get_page,
        already_provided=provided_pages,
    )
    model_pages.extend(extra_pages)
    provided_pages.update(int(page["page"]) for page in extra_pages)
    if on_stage:
        hit_sections = sum(bool(item["candidate_pages"]) for item in retrieval["sections"].values())
        on_stage("定位模块一必查内容", "已完成", f"6 类检索主题中 {hit_sections} 类找到候选页；候选不等于已证实。")
        on_stage("DeepSeek提取年报事实", "进行中", "先提取原文事实、报告口径和收入表，不做经营归因。")
    material = "\n\n".join(
        f"【文档：{file_name}；PDF第{int(page['page'])}页】\n{page['text']}" for page in model_pages
    )
    retrieval_summary = (
        "\n\n程序预检到的模块一候选页（检索未命中不代表年报未披露）：\n"
        + json.dumps(retrieval, ensure_ascii=False)
        if retrieval
        else ""
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": business_prompt("extract"),
        },
        {
            "role": "user",
            "content": (
                f"这是模块一第一阶段：仅从年报提取事实和出处，不做原因归纳或经营评价。文件：{file_name}；PDF 共 {page_count} 页。"
                + "以下包含首批页面和程序预检的分主题候选页；如证据不足仍可调用工具补查。\n"
                + retrieval_summary
                + "\n\n"
                + material
            ),
        },
    ]
    content, trace, usage = _run_tool_loop(
        messages=messages,
        page_count=page_count,
        get_page=get_page,
        search_pages=search_pages,
        on_tool=on_tool,
        available_tools=TOOLS[:2],
        recorder=recorder,
    )
    used_pages = provided_pages | set(usage.pop("_tool_pages", []))

    extraction_data = _json_object(content)
    if extraction_data is None:
        result = _normalize_business_analysis(content, used_pages, get_page, sorted(used_pages), retrieval)
        result["model"] = MODEL
        result["usage"] = usage
        return {"result": result, "trace": retrieval_trace + trace, "read_pages": sorted(used_pages)}

    if on_stage:
        extracted_count = len(extraction_data.get("revenue_segments", [])) if isinstance(extraction_data.get("revenue_segments"), list) else 0
        on_stage("DeepSeek提取年报事实", "已完成", f"已整理公司业务事实和 {extracted_count} 项收入分部候选。")
        on_stage("DeepSeek分析经营变化和交接问题", "进行中", "第二阶段只根据已提取并带出处的事实分析变化与后续问题。")

    extracted_result = _normalize_business_analysis(
        content, used_pages, get_page, sorted(used_pages), retrieval
    )
    segment_context = [
        {key: value for key, value in item.items() if key not in {"current_share_denominator", "previous_share_denominator"}}
        for item in extracted_result.get("revenue_segments", [])
    ]
    interpretation_context = {
        "company": extracted_result.get("company"),
        "period": extracted_result.get("period"),
        "reporting_scope": extracted_result.get("reporting_scope"),
        "currency": extracted_result.get("currency"),
        "business_summary": extracted_result.get("business_summary"),
        "business_summary_evidence": extracted_result.get("business_summary_evidence"),
        "business_flow": extracted_result.get("business_flow"),
        "industry_context": extracted_result.get("industry_context"),
        "strategy_competitiveness": extracted_result.get("strategy_competitiveness"),
        "revenue_total": extracted_result.get("revenue_total"),
        "revenue_totals": extracted_result.get("revenue_totals"),
        "revenue_segments": segment_context,
        "growth_drivers_extracted": extracted_result.get("growth_drivers"),
        "dependencies": extracted_result.get("dependencies"),
        "risk_factors": extracted_result.get("risk_factors"),
        "major_changes": extracted_result.get("major_changes"),
        "industry_metrics": extracted_result.get("industry_metrics"),
        "coverage_search": retrieval,
        "uncertainties": extracted_result.get("uncertainties"),
    }
    interpretation_messages = [
        {"role": "system", "content": business_prompt("interpret")},
        {
            "role": "user",
            "content": (
                "这是模块一第二阶段。只分析下面已从当前年报提取的材料事实；不要重新提取或修改收入数字。"
                "请给出有边界的经营解释、行业/战略分析、后续模块问题和摘要。摘录与页码只可从输入事实中选择。\n\n"
                + json.dumps(interpretation_context, ensure_ascii=False)
            ),
        },
    ]
    interpretation_raw = ""
    interpretation_data: dict[str, Any] | None = None
    interpretation_error = ""
    try:
        response = _run_recorded_completion(
            recorder=recorder,
            client=_client(),
            model=MODEL,
            messages=interpretation_messages,
            stream=False,
        )
        if response.usage:
            usage["prompt_tokens"] += response.usage.prompt_tokens or 0
            usage["completion_tokens"] += response.usage.completion_tokens or 0
        if not response.choices:
            raise ModelCallError("DeepSeek 没有返回分析阶段结果。")
        interpretation_raw = response.choices[0].message.content or ""
        interpretation_data = _json_object(interpretation_raw)
    except ModelCallError as exc:
        interpretation_error = str(exc)
    except Exception as exc:
        interpretation_error = str(_safe_model_error(exc))

    if interpretation_data is not None:
        merged_data = _merge_interpretation(extraction_data, interpretation_data)
    else:
        merged_data = dict(extraction_data)
        issue = interpretation_error or "模型分析阶段未返回可识别的结构化结果。"
        merged_data["uncertainties"] = list(merged_data.get("uncertainties", [])) + [
            f"经营解释阶段没有完成，已保留第一阶段抽取结果：{issue}"
        ]

    result = _normalize_business_analysis(
        json.dumps(merged_data, ensure_ascii=False),
        used_pages,
        get_page,
        sorted(used_pages),
        retrieval,
    )
    if interpretation_data is None:
        result["analysis_review_status"] = "事实已提取，解释阶段未完成"
    if on_stage:
        on_stage(
            "DeepSeek分析经营变化和交接问题",
            "已完成" if interpretation_data is not None else "需要复核",
            "第二阶段结果已合并；引用和口径由程序再次校验。" if interpretation_data is not None else "已保留第一阶段事实，经营解释需重试。",
        )
        on_stage("程序校验出处、收入口径与计算", "进行中", "正在检查页码、摘录、单位、期间、范围及可比条件。")
    if interpretation_data is not None:
        trace.append(
            {
                "tool": "module1_interpretation_stage",
                "arguments": {"phase": "analysis_and_handoff"},
                "pages": [],
                "result": {"status": "completed"},
            }
        )
    else:
        trace.append(
            {
                "tool": "module1_interpretation_stage",
                "arguments": {"phase": "analysis_and_handoff"},
                "pages": [],
                "result": {"status": "needs_review"},
            }
        )
    if on_stage:
        on_stage("程序校验出处、收入口径与计算", "已完成", "程序已执行引用匹配和收入计算门槛。")
        on_stage("核对六类模块覆盖与待查问题", "已完成", result.get("analysis_review_status", "需要复核"))
    result["model"] = MODEL
    result["usage"] = usage
    return {"result": result, "trace": retrieval_trace + trace, "read_pages": sorted(used_pages)}
