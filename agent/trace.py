"""将真实运行事件转成可读执行轨迹，不生成模型思维链。"""
from __future__ import annotations

import json

LABELS = {
    "run_started": ("开始任务", "程序", "按本次参数创建隔离运行"),
    "file_read": ("读取材料", "程序", "记录文件路径、SHA-256 与字节数"),
    "material_registered": ("登记来源", "程序", "公司、报告身份与内容指纹检查"),
    "extraction_channel": ("抽取通道", "解析器", "按表头和几何坐标抽取，不补猜缺失值"),
    "extraction_dedup": ("合并与去重", "解析器", "按明确期间合并；冲突值保留待复核"),
    "extraction_completed": ("抽取完成", "解析器", "检查核心字段、单位与口径"),
    "extraction_failed": ("抽取失败", "解析器", "失败不计为证据支持"),
    "period_header_carry": ("跨页表头", "解析器", "仅相邻页、列边界相同的续表沿用已确认表头"),
    "quarter_dedup": ("合并单季数据", "本地规则", "不同来源的单季值等值才合流；冲突不参与环比"),
    "ocr_used": ("识别扫描页", "本地 OCR", "记录语言数据指纹；所有识别字段转人工复核"),
    "event_extracted": ("公告事件字段", "解析器", "字段逐一挂原文位置；缺失或冲突明确标记"),
    "calculation": ("确定性计算", "Python / Decimal", "记录公式、操作数、期间与容差"),
    "draft_loaded": ("读取草稿", "程序", "凭证检查后登记输入与句子数量"),
    "llm_request": ("请求模型", "LLM", "模型负责结构化拆句；不授予裁决权"),
    "llm_response": ("收到模型响应", "LLM", "响应通过本地 JSON Schema 校验后才继续"),
    "model_parse": ("模型拆句 JSON", "LLM", "展示实际输入与结构化输出，不编造 rationale"),
    "agent_loop_start": ("开始工具循环", "程序", "工具与轮次有明确预算"),
    "agent_loop_round": ("模型请求下一步", "LLM", "只接受工具请求或结构化主张"),
    "agent_tool": ("执行工具", "本地工具", "记录实际工具名、参数及结果"),
    "tool_summary": ("工具调用汇总", "程序", "汇总实际发生的调用"),
    "loop_fallback": ("工具循环回退", "程序", "保留回退原因，仍进行本地校验"),
    "claim_gate": ("主张门控", "本地规则", "数字、单位、公司、期间必须在原句中有依据"),
    "text_claim_checked": ("数值裁决", "本地规则", "最终结果由证据与确定性计算生成"),
    "supplemental_check": ("倍数 / 引用核查", "本地规则", "区分内部一致性、来源支持性和待核实"),
    "run_finished": ("完成运行", "程序", "汇总结果并保存可复现记录"),
}


def build_trace(events: list, facts: list[dict] | None = None) -> list[dict]:
    lookup_provided = facts is not None
    facts = facts or []
    index = {f["evidence_id"]: f for f in facts if f.get("evidence_id")}
    steps = []
    for entry in events:
        event = json.loads(entry) if isinstance(entry, str) else entry
        kind = event.get("event", "unknown")
        title, actor, rule = LABELS.get(kind, (kind, "程序", "历史事件仅展示已记录信息"))
        ids = list(event.get("evidence_ids") or [])
        for key in ("evidence_id", "previous_evidence_id", "winner_evidence_id"):
            if event.get(key):
                ids.append(event[key])
        detail = {k: v for k, v in event.items() if k not in {"event", "time", "run_id"}}
        if kind == "event_extracted":
            for field, value in event.get("fields", {}).items():
                ids.extend(f"{event['event_id']}_{field}_{i}" for i, _ in enumerate(value.get("evidence", [])))
        ids = list(dict.fromkeys(ids))
        unavailable = [i for i in ids if i not in index] if lookup_provided else []
        if unavailable:
            detail["unavailable_evidence_ids"] = unavailable
            detail["evidence_link_note"] = "原通道候选未进入最终证据；查看本步原始记录及后续去重步骤。"
            ids = [i for i in ids if i in index]
        steps.append({"step": len(steps) + 1, "time": event.get("time"),
                      "run_id": event.get("run_id"), "event": kind,
                      "title": title, "actor": actor, "rule": rule,
                      "evidence_ids": ids, "evidence": [
                          {k: index[i].get(k) for k in (
                              "evidence_id", "page", "source_file", "value_bbox", "document_id")}
                          for i in ids if i in index], "detail": detail})
    return steps
