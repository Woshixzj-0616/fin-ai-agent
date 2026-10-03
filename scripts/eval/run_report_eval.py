import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from llm_check import check_one_claim  # noqa: E402
from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402
from main import extract_selected  # noqa: E402

GOLD = ROOT / "data" / "eval" / "report_samples" / "gold_labels.json"


def main() -> int:
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    run = Run(AGENT_ROOT, "run_report_eval", {"samples": len(data["samples"])})
    print(f"运行记录：{run.folder}", flush=True)
    facts = []
    for code, year in (("600519", 2024), ("000333", 2024), ("600036", 2023)):
        _m, got, _f = extract_selected(AGENT_ROOT, run, code, year, False)
        facts.extend(got)
        print(f"  证据 {code} {year}: {len(got)}", flush=True)

    rows = []
    for sample in data["samples"]:
        for claim in sample["claims"]:
            item = claim["item"]
            text = claim["quote"]
            result = check_one_claim(item, facts, sample["text"], sample["text"], item["claim_id"])
            want = claim["gold_status"]
            ok = result.get("status") == want or (
                want == "模型判断" and result.get("track") == "model")
            rows.append({
                "sample": sample["id"], "id": item["claim_id"],
                "quote": text[:40], "gold": want,
                "status": result.get("status"), "track": result.get("track"),
                "reason": result.get("reason_code"),
                "evidence": bool(result.get("evidence_ids")), "ok": ok,
            })
    hit = sum(1 for r in rows if r["ok"])
    det = [r for r in rows if r["track"] == "deterministic"]
    det_hit = sum(1 for r in det if r["ok"])
    model_rows = [r for r in rows if r["track"] == "model"]

    out = ROOT / "results" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "report_eval.json", {"rows": rows, "hit": hit, "total": len(rows)})
    report = ["# 研报文体样例端到端（P0）", "",
              f"- 样例 **{len(data['samples'])}** 篇 · 主张 **{len(rows)}** 条；"
              f"- 总体 **{hit}/{len(rows)} = {hit/len(rows):.2%}**",
              f"- 确定性轨 {det_hit}/{len(det)}；模型轨 {len(model_rows)} 条（预测/定性）",
              f"- 证据覆盖：有 evidence_id {sum(1 for r in rows if r['evidence'])}/{len(rows)}",
              "",
              "| 样例 | ID | 金标 | 实际 | 轨 | 证据 | ✓ |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        report.append(f"| {r['sample']} | {r['id']} | {r['gold']} | {r['status']} | {r['track']} | "
                      f"{'✓' if r['evidence'] else '—'} | {'✓' if r['ok'] else '✗'} |")
    report += ["", "口径：文本为研报文体改写（非券商原件）；数字以年报抽取证据为准。", ""]
    (out / "report_eval_report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"端到端 {hit}/{len(rows)} · 报告 {out / 'report_eval_report.md'}")
    run.finish(status="ok", hit=hit, total=len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
