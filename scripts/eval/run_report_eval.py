"""研报文体样例端到端评测（离线固定协议样例）。

评什么
------
- 统一走当前 check_text：分块、主张门控、本地确定性裁决、失败与检查点保留。
- 逐条原子陈述评分：命中、判定对错、弃答、漏拆、超时、失败分别记录。
- 参考答案（gold_status）只进评分程序；回放客户端只看协议字段，不读答案。

用法（仓库根）：
  python scripts/eval/run_report_eval.py
  python scripts/eval/run_report_eval.py --file data/eval/report_samples/S1_茅台.md
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from audit.grounding import split_draft  # noqa: E402
from eval_harness import (  # noqa: E402
    ReplayClient,
    load_answers,
    run_full_flow,
    score_atomic,
)
from main import extract_selected  # noqa: E402
from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402

GOLD = ROOT / "data" / "eval" / "report_samples" / "gold_labels.json"


def expect_for(gold_status: str) -> dict:
    """金标状态 → 评分期望；只有评分程序读得到这里。"""
    if gold_status in {"证据支持", "确认错误"}:
        return {"kind": "status", "status": gold_status}
    if gold_status == "模型判断":
        return {"kind": "model_track"}
    if gold_status in {"证据不足", "口径冲突／需人工复核"}:
        return {"kind": "status", "status": gold_status}
    return {"kind": "nonverdict"}


def align_quotes(sample: dict, sentences: list[dict]) -> dict:
    """claim_id → 草稿句号；只按原文定位，不读参考答案。"""
    aligned = {}
    for claim in sample["claims"]:
        item = claim["item"]
        quote = claim.get("quote") or item.get("quote") or ""
        candidates = [row for row in sentences if quote and quote in row["text"]]
        sentence = candidates[0] if len(candidates) == 1 else None
        aligned[item.get("claim_id")] = (sentence["sentence_id"] if sentence else None, quote)
    return aligned


def build_items(sample: dict, aligned: dict) -> list[dict]:
    """协议项：只从 item 的协议字段构造，回放客户端看不到任何金标状态。"""
    items = []
    for claim in sample["claims"]:
        item = json.loads(json.dumps(claim["item"], ensure_ascii=False))
        sentence_id, quote = aligned[item.get("claim_id")]
        if sentence_id is None:
            continue
        item["sentence_id"] = sentence_id
        item["quote"] = quote or item["quote"]
        items.append(item)
    return items


def build_statements(sample: dict, aligned: dict) -> list[dict]:
    """原子陈述（含期望）：只交给评分程序，不进入草稿、提示词或客户端。"""
    statements = []
    for claim in sample["claims"]:
        item = claim["item"]
        sentence_id, quote = aligned[item.get("claim_id")]
        statements.append({"id": item.get("claim_id"), "sample": sample["id"],
                           "sentence_id": sentence_id, "quote": quote,
                           "expect": expect_for(claim.get("gold_status"))})
    return statements


def select_samples(samples: list[dict], wanted: str | None) -> list[dict]:
    if not wanted:
        return samples
    name = Path(wanted).name
    selected = [sample for sample in samples if sample.get("file") == name]
    if not selected:
        raise ValueError(f"样例不存在：{name}")
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线研报原子陈述评测")
    parser.add_argument("--file")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    answers = load_answers(GOLD)
    try:
        samples = select_samples(answers["samples"], args.file)
    except ValueError as exc:
        parser.error(str(exc))
    out = args.output_dir.resolve() if args.output_dir else Path(tempfile.mkdtemp(prefix="fin_report_eval_"))
    return evaluate(samples, out)


def evaluate(samples: list[dict], run_root: Path, output_dir: Path | None = None) -> int:
    out = Path(output_dir) if output_dir is not None else run_root
    out.mkdir(parents=True, exist_ok=True)

    run = Run(run_root, "run_report_eval", {"samples": len(samples)})
    print(f"输出目录：{out}；运行记录：{run.folder}", flush=True)
    facts = []
    for code, year in (("600519", 2024), ("000333", 2024), ("600036", 2023)):
        _m, got, _f = extract_selected(AGENT_ROOT, run, code, year, False)
        facts.extend(got)
        print(f"  证据 {code} {year}: {len(got)}", flush=True)

    all_rows, all_extras, summaries = [], [], []
    for sample_index, sample in enumerate(samples, 1):
        draft = sample["text"]
        aligned = align_quotes(sample, split_draft(draft))
        items, statements = build_items(sample, aligned), build_statements(sample, aligned)
        sample_run = Run(run_root / f"sample_{sample_index:04d}", "report_eval", {"sample": sample["id"]})
        client = ReplayClient(items=items)
        bundle = run_full_flow(draft, facts, sample_run, client, use_loop=False)
        scored = score_atomic(bundle, statements)
        for row in scored["rows"]:
            row["gold"] = next((claim.get("gold_status") for claim in sample["claims"]
                                if claim["item"].get("claim_id") == row["id"]), None)
        all_rows.extend(scored["rows"])
        all_extras.extend({"sample": sample["id"], "prediction": row} for row in scored["extra_predictions"])
        summary = {**scored["summary"], "sample": sample["id"],
                   "run_folder": str(sample_run.folder), "binding_sha256": bundle.get("binding_sha256"),
                   "mode": bundle.get("mode"),
                   "chunks": len(bundle.get("chunks") or [])}
        summaries.append(summary)
        print(f"  {sample['id']}: 判定 {summary['correct']}/{summary['judged']} · "
              f"弃答 {summary['abstain']} · 漏拆 {summary['omitted']} · 失败 {summary['failed']}",
              flush=True)

    totals = {
        "samples": len(summaries),
        "source": "离线固定协议样例（回放），非真实模型请求",
        "total": sum(item["total"] for item in summaries),
        "judged": sum(item["judged"] for item in summaries),
        "correct": sum(item["correct"] for item in summaries),
        "wrong": sum(item["wrong"] for item in summaries),
        "abstain": sum(item["abstain"] for item in summaries),
        "abstain_expected": sum(item["abstain_expected"] for item in summaries),
        "omitted": sum(item["omitted"] for item in summaries),
        "timeout": sum(item["timeout"] for item in summaries),
        "failed": sum(item["failed"] for item in summaries),
        "model_track": sum(item["model_track"] for item in summaries),
        "expected_judge": sum(item["expected_judge"] for item in summaries),
        "citation": sum(item["citation"] for item in summaries),
        **{key: sum(item[key] for item in summaries)
           for key in ("tp", "fp", "tn", "fn", "extra_predictions")},
    }
    totals["accuracy"] = round(totals["correct"] / totals["judged"], 4) if totals["judged"] else None
    totals["error_rate"] = round(totals["wrong"] / totals["judged"], 4) if totals["judged"] else None
    totals["correct_coverage"] = round(totals["correct"] / totals["expected_judge"], 4) if totals["expected_judge"] else None
    tp, fp, tn, fn = (totals[key] for key in ("tp", "fp", "tn", "fn"))
    totals["precision"] = round(tp / (tp + fp), 4) if tp + fp else None
    totals["recall"] = round(tp / (tp + fn), 4) if tp + fn else None
    totals["false_positive_rate"] = round(fp / (fp + tn), 4) if fp + tn else None
    write_json(out / "report_eval.json", {"summary": totals, "samples": summaries,
                                          "rows": all_rows, "extra_predictions": all_extras,
                                          "note": "离线固定协议样例；不是真实模型准确率"})
    report = ["# 研报文体样例端到端（P0 · 走完整 check_text）", "",
              f"- 样例 **{totals['samples']}** 篇 · 原子陈述 **{totals['total']}** 条",
              f"- 确定性判定 **{totals['correct']}/{totals['judged']}**"
              f"（已判定正确率 {totals['accuracy']}）· 应判覆盖 **{totals['correct']}/{totals['expected_judge']}**"
              f"（正确覆盖 {totals['correct_coverage']}）",
              f"- 合理弃答 {totals['abstain_expected']}/{totals['abstain']} · 漏拆 {totals['omitted']}"
              f" · 超时 {totals['timeout']} · 失败 {totals['failed']} · 模型轨 {totals['model_track']}",
              f"- 引用覆盖（带 evidence_id）：{totals['citation']}/{totals['total']}",
              "", "| 样例 | ID | 金标 | 实际 | 轨道 | 结局 | 证据 | ✓ |",
              "|---|---|---|---|---|---|---|---|"]
    for row in all_rows:
        report.append(
            f"| {row['sample']} | {row['id']} | {row.get('gold') or '—'} | {row.get('status') or '—'} "
            f"| {row.get('track') or '—'} | {row['outcome']} "
            f"| {'✓' if row['evidence_ids'] else '—'} "
            f"| {'✓' if row['ok'] else ('✗' if row['ok'] is False else '—')} |")
    report += ["", f"重复/多余预测：{totals['extra_predictions']}；"
               f"错误检出 TP/FP/TN/FN：{tp}/{fp}/{tn}/{fn}（确认错误为正类，仅含已判定项）。",
               "口径：文本为研报文体改写（非券商原件）；数字以年报抽取证据为准。",
               "本表为功能回归：使用固定协议样例回放，**不含真实模型请求**，"
               "因此不能当作业务准确率。", ""]
    (out / "report_eval_report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"端到端 {totals['correct']}/{totals['judged']} · 报告 {out / 'report_eval_report.md'}")
    run.finish(status="ok", **{key: totals[key] for key in
                               ("samples", "total", "judged", "correct", "abstain", "omitted")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
