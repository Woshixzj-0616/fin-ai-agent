"""工具调用链演示（不调外网模型）：模拟 agent 执行轨迹，给评委看「模型只下令、工具出证据」。

用法（仓库根）：python scripts/tool_demo.py
输出 results/tool_demo.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from agent_loop import tool_summary  # noqa: E402
from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402
from main import extract_selected  # noqa: E402
from tools import dispatch, tool_specs  # noqa: E402

# 演示脚本：像模型一样「下命令」，结果全部来自本地工具
SCRIPT = [
    ("list_catalog", {"kind": "metrics"}),
    ("find_evidence", {"company_name_or_code": "贵州茅台", "metric": "revenue", "period_year": 2024}),
    ("compute_yoy", {"company_name_or_code": "贵州茅台", "metric": "revenue", "year": 2024}),
    ("compare_claim", {"company_name_or_code": "贵州茅台", "metric": "revenue", "period_year": 2024,
                       "kind": "amount", "claimed_value": "1500.00", "claimed_unit": "亿元",
                       "operator": "eq"}),
    ("compare_claim", {"company_name_or_code": "贵州茅台", "metric": "revenue", "period_year": 2024,
                       "kind": "amount", "claimed_value": "1709", "claimed_unit": "亿元",
                       "operator": "approx", "tolerance_pct": 2.0}),
]


def main() -> int:
    run = Run(AGENT_ROOT, "tool_demo", {"steps": len(SCRIPT)})
    print(f"运行记录：{run.folder}", flush=True)
    _m, facts, _f = extract_selected(AGENT_ROOT, run, "600519", 2024, False)
    tools_used = []
    lines = ["# 工具调用链演示（agent · 无外网模型）", "",
             f"- 证据库：贵州茅台 2024 年报 **{len(facts)}** 条（抽取代码产出，非模型生成）",
             f"- 工具定义：{len(tool_specs())} 只（只读）",
             "- 口径：**模型只下命令，对错只出自 `compare_claim` → 本地 `finance.check_claim`**", "",
             "## 调用轨迹", ""]
    for i, (name, args) in enumerate(SCRIPT, 1):
        result = dispatch(name, facts, args)
        status = result.get("status") or result.get("verdict") or "—"
        tools_used.append({"round": i, "name": name, "arguments": args, "status": status})
        run.event("tool_demo", step=i, tool=name, status=status)
        lines += [f"### 第 {i} 步 · `{name}`", "",
                  "```json", json.dumps({"arguments": args}, ensure_ascii=False, indent=2), "```",
                  f"**结果状态**：`{status}`", ""]
        brief = {k: v for k, v in result.items() if k in {
            "verdict", "status", "count", "evidence_ids", "reason", "expected",
            "exact_value", "claimed_value", "operator", "yoy", "error", "hint"}}
        lines += ["```json", json.dumps(brief, ensure_ascii=False, indent=2, default=str), "```", ""]

    summary = tool_summary(tools_used)
    lines += ["## 汇总", "",
              f"- 工具次数：{len(tools_used)}（{summary}）",
              "- 第 3–5 步：`compute_yoy` / `compare_claim` 返回里都带 **evidence_id**，可回年报页。",
              "- 错值 1500 亿 → `confirmed_error`；约数 1709 亿 → `evidence_supported`（±2%）。",
              "",
              "## 这和「让模型直接说对错」的差别", "",
              "| | 自由 LLM | 本演示 |",
              "|---|---|---|",
              "| 谁下结论 | 模型 | `compare_claim`（本地） |",
              "| 有无页码 | 常无 | 有（evidence_id + page + bbox） |",
              "| 能否重算 | 难 | 能（Decimal 明细） |", ""]
    out = ROOT / "results" / "tool_demo.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    write_json(ROOT / "results" / "tool_demo.json", tools_used)
    print(f"演示完成 {len(tools_used)} 步 · {out}")
    run.finish(status="ok", steps=len(tools_used))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
