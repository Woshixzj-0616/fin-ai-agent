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
    from main import extract_selected, report_markdown
    from materials import Run, write_csv, write_json
    from finance import analyze, check_claim
    run = Run(RESULTS.parent, "site_build", {"codes": [c for c, _ in codes]})
    print(f"抽取运行：{run.folder}", flush=True)
    all_facts: list[dict] = []
    selected_materials = []
    failures = []
    for code, year in codes:
        _m, facts, failed = extract_selected(ROOT, run, code, year, True)
        all_facts.extend(facts)
        selected_materials.extend(_m)
        failures.extend(failed)
        print(f"  {code} {year}: 证据 {len(facts)}", flush=True)
        # 页图已由 run.output 写入 results/，此处无需再拷
    write_json(run.output("evidence.json"), all_facts)
    write_csv(run.output("evidence.csv"), all_facts, [
        "evidence_id", "company_code", "company_name", "report_year", "period_year",
        "period_start", "period_end", "period_kind", "metric", "metric_name", "value",
        "unit", "normalized_value", "normalized_unit", "currency", "scope", "adjustment",
        "page", "source_file", "source_sha256", "announcement_id", "source_url",
    ])
    write_json(run.output("failures.json"), failures)
    analysis = analyze(all_facts, run)
    write_json(run.output("analysis.json"), analysis)
    run.output("report.md").write_text(report_markdown(analysis, selected_materials), encoding="utf-8")
    samples = json.loads(run.read(ROOT / "data/agent/samples.json"))
    write_json(run.output("sample_checks.json"), [check_claim(c, all_facts) for c in samples["claims"]])
    run.finish(snapshot_prefix="analysis", status="partial_failure" if failures else "ok", evidence_count=len(all_facts), failures=len(failures))
    return all_facts


def copy_pages(evidence: list[dict]) -> dict[str, str]:
    """页图拷进 docs/pages，key 包含文件指纹，避免同公司同年不同报告撞名。"""
    (SITE / "pages").mkdir(parents=True, exist_ok=True)
    page_map: dict[str, str] = {}
    required_pages = {f"{e['company_code']}_{e['report_year']}_{e['source_sha256'][:8]}_{e['page']}"
                      for e in evidence if e.get("source_sha256") and e.get("page")}
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
        fingerprint = parts[2]
        key = f"{code}_{year}_{fingerprint}_{page_no}"
        if key not in required_pages:
            continue
        dst = f"pages/{key}.png"
        shutil.copy2(p, SITE / dst)
        page_map[key] = dst
    # 每条证据挂 page_image
    for e in evidence:
        key = f"{e.get('company_code')}_{e.get('report_year')}_{(e.get('source_sha256') or '')[:8]}_{e.get('page')}"
        e["page_image"] = page_map.get(key)
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
    audit_path = RESULTS / "audit_checks.json"
    audit_matches = False
    if audit_path.is_file():
        from materials import sha256
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("evidence_sha256") == sha256((RESULTS / "evidence.json").read_bytes()):
            checks.extend(audit.get("checks", []))
            audit_matches = True
    analysis_path = RESULTS / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.is_file() else {}
    events = []
    event_paths = [RESULTS / "analysis_events.jsonl"]
    if audit_matches:
        event_paths.append(RESULTS / "audit_events.jsonl")
    if not event_paths[0].is_file():
        event_paths[0] = RESULTS / "events.jsonl"
    for events_path in event_paths:
        if events_path.is_file():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass

    companies = sorted({f"{e.get('company_code')}|{e.get('company_name')}" for e in evidence})
    from trace import build_trace
    sample_path = ROOT / "data" / "demo" / "announcement_examples.json"
    announcements = json.loads(sample_path.read_text(encoding="utf-8")) if sample_path.is_file() else []
    bundle = {
        "generated_from": "results/（本次实跑）",
        "companies": [{"code": c.split("|")[0], "name": c.split("|", 1)[-1]} for c in companies],
        "materials": mats,
        "evidence": evidence,
        "page_images": page_map,
        "checks": checks,
        "analysis": analysis,
        "events": events[-400:],
        "trace": build_trace(events, evidence) + [step for sample in announcements for step in sample.get("trace", [])],
        "announcements": announcements,
        "analysis_run": json.loads((RESULTS / "analysis_run.json").read_text(encoding="utf-8")) if (RESULTS / "analysis_run.json").is_file() else None,
    }
    (SITE / "data" / "bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    print(f"materials={len(mats)} evidence={len(evidence)} pages={len(page_map)} "
          f"companies={len(companies)} bundle={ (SITE / 'data' / 'bundle.json').stat().st_size } bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
