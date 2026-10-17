"""草稿核查报告和证据引用生成。"""
from __future__ import annotations

import json


def render_report(results: list[dict], *, model: str, run_id: str,
                  tools_used: list[dict] | None = None,
                  mode: str = "") -> str:
    def cell(value):
        return str(value or "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ")

    det = [r for r in results if r.get("track") == "deterministic" and r.get("status") in {"确认错误", "证据支持"}]
    model_rows = [r for r in results if r.get("track") == "model"]
    review = [r for r in results if r.get("track") != "model" and r not in det]

    lines = ["# 草稿核查报告（双轨）", "",
             f"运行：{run_id}；结构化录入模型：{cell(model)}。", "",
             "**A 栏 = 代码裁定（可复算）**：对错、正确值、计算式全部由本地 Python 生成。", "",
             "**B 栏 = 模型判断（未完全核实）**：语义解释，仅供参考，不作为对错结论。", "",
             "核查上下文：期间、币种、口径与来源须可确认；营业收入与经营现金流按已记录的合并口径。", "",
             "## A. 确定结论（代码裁定）", "",
             "| ID | 主张 | 裁决 | 原因或建议 | 证据与页码 |",
             "|---|---|---|---|---|"]
    for result in det:
        citations = [f"[{fact['evidence_id']}](../{fact['source_file']})（PDF第{fact['page']}页）"
                     for fact in result["evidence"]]
        lines.append(f"| {result['claim_id']} | {cell(result.get('quote') or result['original_sentence'])} | "
                     f"{result['status']} | {cell(result.get('suggestion') or result['reason'])} | "
                     f"{'；'.join(citations) or '无可用证据'} |")
    if not det:
        lines.append("| — | （无确定结论） | | | |")
    for row in det:
        if row.get("verification_scope"):
            lines += ["", f"- {cell(row['claim_id'])} 核查范围：{cell(row['verification_scope'])}。"]
        if row.get("correction"):
            lines += [f"- {cell(row['claim_id'])} 替换：{cell(row['correction'].get('before'))} → {cell(row['correction'].get('after'))}。"]

    lines += ["", "## B. 模型判断（未完全核实 · 仅供参考）", "",
              "| ID | 主张 | 解释 | 依据原文 | 状态 |",
              "|---|---|---|---|---|"]
    for result in model_rows:
        lines.append(f"| {result['claim_id']} | {cell(result.get('plain_claim') or result.get('quote'))} | "
                     f"{cell(result.get('reason'))} | {cell(result.get('quote'))} | {cell(result.get('status'))} |")
    if not model_rows:
        lines.append("| — | （无） | | | |")

    lines += ["", "## C. 需人工 / 证据不足", "",
              "| ID | 原因码 | 说明 |", "|---|---|---|"]
    for result in review:
        lines.append(f"| {result['claim_id']} | {cell(result.get('reason_code'))} | {cell(result.get('reason'))} |")
    if not review:
        lines.append("| — | （无） | |")

    lines += ["", "## 程序计算明细（仅 A 栏）", ""]
    for result in det:
        if result.get("calculation"):
            lines += [f"### {result['claim_id']}", "", "```json",
                      json.dumps(result["calculation"], ensure_ascii=False, indent=2), "```", ""]
    lines += ["", "## 审计", "",
              f"- 确定结论 {len(det)} 条；模型判断 {len(model_rows)} 条；需人工 {len(review)} 条。",
              "- 模型不产生「确认错误/证据支持」；这两类仅来自本地比较程序。",
              f"- 运行模式：{mode or 'single_shot'}", ""]

    lines += ["## 工具调用链（agent）", ""]
    if tools_used:
        lines += ["| # | 轮次 | 工具 | 状态 |", "|---|---|---|---|"]
        for i, t in enumerate(tools_used, 1):
            args = t.get("arguments") or {}
            brief = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
            lines.append(f"| {i} | {t.get('round', '—')} | `{t.get('name')}` | "
                         f"{t.get('status') or '—'} {('· ' + brief) if brief else ''} |")
        lines += ["", "说明：`compare_claim` 是唯一产生对错的工具；其余只供取数/理解。"]
    else:
        lines.append("（本次为单次拆解模式，未走工具循环）")
    lines.append("")
    return "\n".join(lines)
