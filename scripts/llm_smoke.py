# -*- coding: utf-8 -*-
"""真实 DeepSeek 联通 + 工具循环冒烟：直接喂 results/evidence.json，跳过 70 份全量重抽。"""
import json
import sys
from pathlib import Path

from materials import Run, write_json
from llm_check import LLMClient, check_text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
facts = json.load(open(ROOT / "results" / "evidence.json", encoding="utf-8"))
print("facts", len(facts), flush=True)

run = Run(ROOT, "check-text", {
    "command": "check-text",
    "file": "results/llm_smoke_draft.txt",
    "note": "direct facts path; skip re-extract",
})
client = LLMClient.from_environment()
print("host", client.host, "model", client.model, "mode", client.mode, flush=True)

bundle = check_text(ROOT / "results" / "llm_smoke_draft.txt", facts, run, client, use_loop=True)
write_json(run.output("text_checks.json"), bundle)

report = ["# 真实 DeepSeek 工具循环核查", "",
          f"- mode: `{bundle.get('mode')}`",
          f"- tools_used: {len(bundle.get('tools_used') or [])}",
          f"- counts: `{json.dumps(bundle.get('counts'), ensure_ascii=False)}`",
          "", "## checks", ""]
for c in bundle.get("checks") or []:
    report.append(f"- **{c.get('status')}** | {(c.get('sentence') or '')[:50]} | evid={c.get('evidence_ids')}")
run.output("text_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

run.finish(status="ok", mode=bundle.get("mode"),
           checks=len(bundle.get("checks") or []),
           tools_used=len(bundle.get("tools_used") or []))

print("MODE:", bundle.get("mode"), flush=True)
print("COUNTS:", bundle.get("counts"), flush=True)
print("TOOLS:", [t.get("name") for t in (bundle.get("tools_used") or [])], flush=True)
for c in (bundle.get("checks") or [])[:10]:
    print("CHECK:", c.get("status"), "|", (c.get("sentence") or "")[:40], "|", c.get("evidence_ids"), flush=True)
print("RUNDIR:", run.folder, flush=True)
