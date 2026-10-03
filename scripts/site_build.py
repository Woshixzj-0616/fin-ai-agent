"""把 agent 产出整理成 GitHub Pages 审计台（支持多公司）。

用法（仓库根）：
  python scripts/site_build.py
  python scripts/site_build.py --codes 600519,000333,002594,600036,000858
  python scripts/site_build.py --use-existing   # 不重新抽取，只打包已有 results/
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

RESULTS = ROOT / "results"
SITE = ROOT / "docs"
DEFAULT_CODES = ["600519", "000333", "002594", "600036", "000858"]
CORE = {"revenue", "parent_net_profit", "adjusted_parent_net_profit",
        "operating_cash_flow", "total_revenue", "basic_eps"}


def load_materials() -> list[dict]:
    mats = []
    path = ROOT / "data" / "agent" / "manifest.csv"
    with path.open(encoding="utf-8-sig") as f:
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
    return mats


def extract_multi(codes: list[tuple[str, int]]) -> list[dict]:
    from main import extract_selected
    from materials import Run
    run = Run(RESULTS.parent, "site_build", {"codes": [c for c, _ in codes]})
    print(f"抽取运行：{run.folder}", flush=True)
    all_facts: list[dict] = []
    for code, year in codes:
        _m, facts, failed = extract_selected(ROOT, run, code, year, True)
        all_facts.extend(facts)
        print(f"  {code} {year}: 证据 {len(facts)}", flush=True)
        # 页图已由 run.output 写入 results/，此处无需再拷
    write = RESULTS / "evidence.json"
    write.write_text(json.dumps(all_facts, ensure_ascii=False, indent=1), encoding="utf-8")
    return all_facts


def copy_pages(evidence: list[dict]) -> dict[str, str]:
    """页图拷进 docs/pages，key 用 company_year_page，避免多公司撞名。"""
    (SITE / "pages").mkdir(parents=True, exist_ok=True)
    page_map: dict[str, str] = {}
    # results 下 PNG：{code}_{year}_{sha8}_p{page}.png
    for p in RESULTS.glob("*.png"):
        stem = p.stem
        if "_p" not in stem:
            continue
        head, page = stem.rsplit("_p", 1)
        parts = head.split("_")
        if len(parts) < 3:
            continue
        code, year = parts[0], parts[1]
        try:
            page_no = int(page)
        except ValueError:
            continue
        key = f"{code}_{year}_{page_no}"
        dst = f"pages/{key}.png"
        shutil.copy2(p, SITE / dst)
        page_map[key] = dst
        page_map[str(page_no)] = dst  # 兼容旧键（单公司）
    # 每条证据挂 page_image
    for e in evidence:
        key = f"{e.get('company_code')}_{e.get('report_year')}_{e.get('page')}"
        e["page_image"] = page_map.get(key) or page_map.get(str(e.get("page")))
    return page_map


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", help="逗号分隔 证券代码_年份，如 600519_2024,000333_2024")
    ap.add_argument("--use-existing", action="store_true", help="不重新抽取，打包已有 evidence.json")
    args = ap.parse_args()

    pairs: list[tuple[str, int]] = []
    if args.codes:
        for token in args.codes.split(","):
            token = token.strip()
            if "_" in token:
                c, y = token.split("_", 1)
                pairs.append((c, int(y)))
            else:
                pairs.append((token, 2024))
    else:
        pairs = [(c, 2024 if c != "600036" else 2023) for c in DEFAULT_CODES]

    if args.use_existing:
        evidence = json.loads((RESULTS / "evidence.json").read_text(encoding="utf-8"))
    else:
        evidence = extract_multi(pairs)

    page_map = copy_pages(evidence)
    mats = load_materials()
    (SITE / "data").mkdir(parents=True, exist_ok=True)
    (SITE / "data" / "materials.json").write_text(
        json.dumps(mats, ensure_ascii=False, indent=1), encoding="utf-8")

    checks_path = RESULTS / "sample_checks.json"
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.is_file() else []
    analysis_path = RESULTS / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.is_file() else {}
    events = []
    events_path = RESULTS / "events.jsonl"
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass

    companies = sorted({f"{e.get('company_code')}|{e.get('company_name')}" for e in evidence})
    bundle = {
        "generated_from": "agent/results",
        "companies": [{"code": c.split("|")[0], "name": c.split("|", 1)[-1]} for c in companies],
        "materials": mats,
        "evidence": evidence,
        "page_images": page_map,
        "checks": checks,
        "analysis": analysis,
        "events": events[-400:],
    }
    (SITE / "data" / "bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    print(f"materials={len(mats)} evidence={len(evidence)} pages={len(page_map)} "
          f"companies={len(companies)} bundle={ (SITE / 'data' / 'bundle.json').stat().st_size } bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
