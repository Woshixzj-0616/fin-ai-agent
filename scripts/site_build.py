"""把 agent 产出整理成 GitHub Pages 前端用的静态数据。"""
import csv
import json
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
SITE = ROOT / "docs"

(SITE / "data").mkdir(parents=True, exist_ok=True)
(SITE / "pages").mkdir(parents=True, exist_ok=True)

# 1. 材料台账
mats = []
with (ROOT / "data" / "agent" / "manifest.csv").open(encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        mats.append({
            "code": row["company_code"], "name": row["company_name"],
            "year": int(row["report_year"]), "title": row["title"],
            "announcement_id": row["announcement_id"],
            "sha256": row["sha256"], "pages": int(row["page_count"]),
            "size_bytes": int(row["size_bytes"]),
            "source_url": row["source_url"], "local_file": row["local_file"],
        })
mats.sort(key=lambda m: (m["code"], m["year"]))
(SITE / "data" / "materials.json").write_text(
    json.dumps(mats, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"materials: {len(mats)}")

# 2. 证据（带 bbox）
evidence = json.loads((RESULTS / "evidence.json").read_text(encoding="utf-8"))
# 页图：统一命名 pages/p<N>.png（相对 evidence 的 page）
page_names = {}
for p in RESULTS.glob("*.png"):
    # 例：600519_2024_5299f494_p5.png
    stem = p.stem
    if "_p" in stem:
        pn = int(stem.rsplit("_p", 1)[1])
        dst = f"pages/{pn}.png"
        shutil.copy2(p, SITE / dst)
        page_names[pn] = dst
print(f"pages: {page_names}")

# 3. 陈述核查
checks = json.loads((RESULTS / "sample_checks.json").read_text(encoding="utf-8"))

# 4. 同比分析
analysis = json.loads((RESULTS / "analysis.json").read_text(encoding="utf-8"))

# 5. 审计日志
events = []
for line in (RESULTS / "events.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        events.append(json.loads(line))

bundle = {
    "generated_from": "agent/results",
    "materials": mats,
    "evidence": evidence,
    "page_images": page_names,
    "checks": checks,
    "analysis": analysis,
    "events": events,
}
(SITE / "data" / "bundle.json").write_text(
    json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
print(f"bundle.json: {len(json.dumps(bundle))} bytes  "
      f"evidence={len(evidence)} checks={len(checks)} events={len(events)}")
