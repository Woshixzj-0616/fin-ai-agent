# -*- coding: utf-8 -*-
"""真实年报全量抽取演示：跑 extract → analyze，输出覆盖率与样本证据。"""
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from materials import Run, write_json
from extract import extract_material
from materials import load_materials
from finance import analyze

print("== 真实年报全量抽取 ==", flush=True)
t0 = time.time()
run = Run(ROOT, "full-extract-demo", {"scope": "all registered annual reports"})
registry = ROOT / "data" / "agent" / "materials.jsonl"
materials = [m for m in load_materials(ROOT)]
# 只要年报（排除期间报告），保证 70 份
annual = [m for m in materials if (m.get("report_kind") or "annual") == "annual"]
print(f"registered={len(materials)} annual={len(annual)}", flush=True)

facts, failures = [], []
by_doc = {}
for i, m in enumerate(annual, 1):
    t1 = time.time()
    try:
        found = extract_material(ROOT, m, run)
        facts.extend(found)
        by_doc[m["document_id"]] = {
            "company": m["company_code"] + " " + m.get("company_name", ""),
            "year": m["report_year"],
            "evidence": len(found),
            "metrics": sorted({f["metric"] for f in found}),
            "issues": sum(1 for f in found if f.get("issues")),
            "sec": round(time.time() - t1, 1),
        }
        print(f"[{i:02d}/{len(annual)}] {m['company_code']} {m['report_year']} → {len(found)} 证据 "
              f"({by_doc[m['document_id']]['sec']}s)", flush=True)
    except Exception as exc:
        failures.append({"document_id": m.get("document_id"), "error": f"{type(exc).__name__}: {exc}"})
        print(f"[{i:02d}/{len(annual)}] {m['company_code']} {m['report_year']} FAIL {exc}", flush=True)

analysis = analyze(facts, run)
write_json(run.output("evidence.json"), facts)
write_json(run.output("analysis.json"), analysis)
write_json(run.output("full_extract_report.json"), {
    "documents": by_doc,
    "failures": failures,
    "total_evidence": len(facts),
    "elapsed_sec": round(time.time() - t0, 1),
})

# 覆盖率：核心指标 × 公司 × 年
CORE = ["revenue", "parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow",
        "total_assets", "parent_equity", "basic_eps", "weighted_roe"]
COVER = defaultdict(set)
for f in facts:
    if f.get("metric") in CORE and f.get("period_kind") == "annual":
        COVER[f["metric"]].add((f["company_code"], f["period_year"]))

total_slots = len(annual)  # 每文档至少应有核心指标
print("\n== 核心指标覆盖率（有值的 公司×年） ==", flush=True)
for m in CORE:
    cells = COVER[m]
    print(f"  {m:32s} {len(cells):3d} / {total_slots}", flush=True)

print("\n== 按公司汇总 ==", flush=True)
by_co = defaultdict(lambda: {"docs": 0, "evidence": 0, "issues": 0})
for d in by_doc.values():
    code = d["company"].split()[0]
    by_co[code]["docs"] += 1
    by_co[code]["evidence"] += d["evidence"]
    by_co[code]["issues"] += d["issues"]
for code, s in sorted(by_co.items()):
    print(f"  {code}  docs={s['docs']} evidence={s['evidence']} issues={s['issues']}", flush=True)

print("\n== 茅台 2024 样本证据（前 8 条） ==", flush=True)
samples = [f for f in facts if f.get("company_code") == "600519" and f.get("period_year") == 2024
           and f.get("period_kind") == "annual"]
for f in samples[:8]:
    print(f"  {f['metric']:28s} {f['value']:>18s} {f.get('unit') or '':6s} p{f.get('page')} "
          f"eid={f['evidence_id'][:8]}", flush=True)

print("\n== 分析行（同比核对，前 8 行） ==", flush=True)
for row in analysis.get("rows", [])[:8]:
    yoy = row.get("yoy") or {}
    rate = yoy.get("value") if yoy.get("status") == "ok" else yoy.get("status")
    check = row.get("reported_yoy_check")
    print(f"  {row.get('company_code')} {row.get('period_year')} {row.get('metric'):24s} "
          f"yoy={rate} check={check}", flush=True)

print(f"\nTOTAL evidence={len(facts)} failures={len(failures)} "
      f"elapsed={round(time.time()-t0,1)}s run={run.folder}", flush=True)
run.finish(status="ok" if not failures else "partial_failure",
           evidence_count=len(facts), failures=len(failures),
           documents=len(by_doc), elapsed_sec=round(time.time() - t0, 1))
