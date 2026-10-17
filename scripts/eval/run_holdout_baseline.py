"""P0 留出集首轮基线运行器。

用法（仓库根）：
  python scripts/eval/run_holdout_baseline.py --mode replay --runs 3
  python scripts/eval/run_holdout_baseline.py --mode live --runs 3 --from-travel-env

评分字段只在评分阶段读取；Agent 只收到 draft、claim items 和冻结 facts。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

from eval_harness import ReplayClient, run_full_flow, score_atomic  # noqa: E402
from holdout_eval import (  # noqa: E402
    compare_runs,
    render_markdown,
    rows_from_scored,
    runtime_payload,
    unified_metrics,
    verify_dataset,
)
from llm_check import LLMClient, LLMError  # noqa: E402
from materials import Run, write_json  # noqa: E402
from run_llm_eval import load_travel_env, pick_provider  # noqa: E402

DATASET = ROOT / "data" / "holdout"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _statements_for_scoring(gold: dict) -> list[dict]:
    return [
        {"id": statement["id"], "sentence_id": index,
         "quote": statement["text"], "expect": statement["expect"]}
        for index, statement in enumerate(gold["statements"], 1)
    ]


def _assert_runtime_isolated(draft: str, items: list[dict], facts: list[dict]) -> None:
    serialized = json.dumps({"draft": draft, "items": items, "facts": facts},
                            ensure_ascii=False, sort_keys=True)
    forbidden = ('"expect"', '"allowed_document_ids"', '"allowed_evidence"',
                 '"reason_category"', '"abstention_allowed"', 'GOLD_ONLY')
    leaked = [token for token in forbidden if token in serialized]
    if leaked:
        raise ValueError("运行输入包含评分侧字段：" + ",".join(leaked))


def _share_ms(rows: list[dict], model_ms: float, local_ms: float) -> list[dict]:
    count = len(rows) or 1
    for row in rows:
        row["model_ms"] = round(model_ms / count, 3)
        row["local_ms"] = round(local_ms / count, 3)
    return rows


def _scored_run(bundle: dict, gold: dict, facts: list[dict], *,
                model_ms: float, local_ms: float) -> dict:
    statements = _statements_for_scoring(gold)
    scored = score_atomic(bundle, statements)
    rows = rows_from_scored(scored, gold["statements"], facts,
                            model_ms=model_ms, local_ms=local_ms)
    _share_ms(rows, model_ms, local_ms)
    return {
        "bundle_status": bundle.get("status"),
        "bundle_reason": bundle.get("reason"),
        "rows": rows,
        "atomic_summary": scored["summary"],
        "extras": scored.get("extra_predictions") or [],
    }


def run_replay_once(gold: dict, facts: list[dict], index: int) -> dict:
    draft, items = runtime_payload(gold)
    _assert_runtime_isolated(draft, items, facts)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"p0_holdout_replay_{index}_") as temp:
        run = Run(Path(temp), "p0_holdout_replay", {"run": index})
        bundle = run_full_flow(draft, facts, run, ReplayClient(items=items),
                               use_loop=False)
    elapsed_ms = (time.perf_counter() - started) * 1000
    result = _scored_run(bundle, gold, facts, model_ms=0, local_ms=elapsed_ms)
    result.update({"run": index, "mode": "replay", "elapsed_ms": round(elapsed_ms, 3)})
    return result


class TimingClient:
    """透明计时包装，不改变 Agent 客户端协议。"""

    def __init__(self, client):
        self.client = client
        self.model_ms = 0.0

    def __getattr__(self, name):
        return getattr(self.client, name)

    def _timed(self, name, *args, **kwargs):
        started = time.perf_counter()
        try:
            return getattr(self.client, name)(*args, **kwargs)
        finally:
            self.model_ms += (time.perf_counter() - started) * 1000

    def extract(self, *args, **kwargs):
        return self._timed("extract", *args, **kwargs)

    def chat(self, *args, **kwargs):
        return self._timed("chat", *args, **kwargs)

    def chat_tools(self, *args, **kwargs):
        return self._timed("chat_tools", *args, **kwargs)


def run_live_once(gold: dict, facts: list[dict], index: int, *,
                  client: LLMClient, use_loop: bool, output_dir: Path) -> dict:
    draft, items = runtime_payload(gold)
    _assert_runtime_isolated(draft, items, facts)
    run_dir = output_dir / f"live-run-{index:02d}"
    run = Run(run_dir, "p0_holdout_live", {"run": index, "use_loop": use_loop})
    timed = TimingClient(client)
    started = time.perf_counter()
    try:
        bundle = run_full_flow(draft, facts, run, timed, use_loop=use_loop)
    except LLMError as exc:
        bundle = {
            "status": "failed",
            "reason": str(exc),
            "checks": [],
            "unfinished_sentence_ids": list(range(1, len(gold["statements"]) + 1)),
            "sentences": [{"sentence_id": item} for item in
                          range(1, len(gold["statements"]) + 1)],
        }
    elapsed_ms = (time.perf_counter() - started) * 1000
    local_ms = max(0.0, elapsed_ms - timed.model_ms)
    result = _scored_run(bundle, gold, facts, model_ms=timed.model_ms,
                         local_ms=local_ms)
    result.update({
        "run": index,
        "mode": "live",
        "elapsed_ms": round(elapsed_ms, 3),
        "model_ms": round(timed.model_ms, 3),
        "local_ms": round(local_ms, 3),
        "run_dir": str(run_dir.relative_to(ROOT)).replace("\\", "/"),
    })
    return result


def _git_value(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _hash_paths(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file():
            digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _version_record(dataset: dict, mode: str, model: str | None,
                    base_url: str | None) -> dict:
    return {
        "code_commit": _git_value("rev-parse", "HEAD"),
        "branch": _git_value("branch", "--show-current"),
        "model": model,
        "base_url": base_url,
        "prompt_version": _hash_paths([
            ROOT / "agent" / "llm_check.py",
            ROOT / "agent" / "audit" / "loop.py",
            ROOT / "agent" / "audit" / "service.py",
        ]),
        "rule_version": _hash_paths([
            ROOT / "agent" / "finance.py",
            ROOT / "agent" / "audit" / "claim_check.py",
            ROOT / "agent" / "audit" / "grounding.py",
        ]),
        "harness_version": _hash_paths([
            ROOT / "agent" / "eval_harness.py",
            ROOT / "agent" / "holdout_eval.py",
            Path(__file__).resolve(),
        ]),
        "dataset_version": dataset["dataset_version"],
        "run_mode": mode,
    }


def _run_table(metrics_by_run: list[dict]) -> str:
    lines = [
        "| 轮次 | 准确率 | 错误召回 | 误报率 | 引用支持 | 合理弃答 | 失败率 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for index, metrics in enumerate(metrics_by_run, 1):
        lines.append(
            f"| {index} | {metrics.get('judgment_accuracy')} | {metrics.get('error_recall')} "
            f"| {metrics.get('false_positive_rate')} | {metrics.get('citation_support_rate')} "
            f"| {metrics.get('reasonable_abstention_rate')} | {metrics.get('failure_rate')} |"
        )
    return "\n".join(lines)


def _write_result(output_dir: Path, runs: list[dict], gold: dict, dataset: dict,
                  *, mode: str, model: str | None, base_url: str | None) -> dict:
    rows = [row for run in runs for row in run["rows"]]
    combined = unified_metrics(rows)
    consistency = compare_runs([run["rows"] for run in runs])
    run_metrics = [unified_metrics(run["rows"]) for run in runs]
    versions = _version_record(dataset, mode, model, base_url)
    meta = {
        "dataset_id": gold["dataset_id"],
        "dataset_version": dataset["dataset_version"],
        "independent_review": bool(gold.get("independent_review")),
        "run_mode": mode,
        "run_count": len(runs),
        "model": model,
        "base_url": base_url,
        "version": versions,
        "gold_sha256": dataset["gold_sha256"],
        "manifest_sha256": dataset["manifest_sha256"],
        "facts_sha256": dataset["facts_sha256"],
        "run_metrics": run_metrics,
    }
    report = render_markdown(combined, consistency, meta)
    report += "\n## 分轮指标\n\n" + _run_table(run_metrics) + "\n"
    outputs = {
        "status": "valid",
        "dataset_version": dataset["dataset_version"],
        "combined_metrics": combined,
        "run_metrics": run_metrics,
        "consistency": consistency,
        "version": versions,
        "dataset": dataset,
        "gold_hash_before": dataset["gold_sha256"],
        "runs": runs,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.md").write_text(report + "\n", encoding="utf-8")
    write_json(output_dir / "raw.json", outputs)
    write_json(output_dir / "differences.json", consistency["differences"])
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P0 留出集回归基线")
    parser.add_argument("--mode", choices=["replay", "live"], required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--from-travel-env", action="store_true")
    parser.add_argument("--no-loop", action="store_true")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.runs > 10:
        parser.error("--runs 必须在 1 到 10 之间")
    output_dir = (args.output_dir or (
        ROOT / "results" / "holdout" /
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.mode}"
    )).resolve()

    dataset = verify_dataset(DATASET)
    if not dataset["ok"]:
        print("数据集冻结校验失败：")
        for issue in dataset["issues"]:
            print(f"- {issue}")
        return 2
    manifest = _read_json(DATASET / "manifest.json")
    gold = _read_json(DATASET / "gold.json")
    facts_bundle = _read_json(DATASET / "facts.json")
    facts = facts_bundle["facts"]

    model = base_url = None
    client = None
    if args.mode == "live":
        picked = load_travel_env() if args.from_travel_env else {}
        base_url, model, key = pick_provider(picked)
        if not key:
            print("未配置模型密钥；请设置 LLM_API_KEY 或加 --from-travel-env")
            return 3
        client = LLMClient(base_url, model, key, "json_object")

    runs = []
    for index in range(1, args.runs + 1):
        if args.mode == "replay":
            result = run_replay_once(gold, facts, index)
        else:
            result = run_live_once(gold, facts, index, client=client,
                                   use_loop=not args.no_loop, output_dir=output_dir)
        runs.append(result)
        print(f"{args.mode} 第 {index}/{args.runs} 轮完成："
              f"{result['atomic_summary']['correct']}/{result['atomic_summary']['total']} 正确")

    dataset_after = verify_dataset(DATASET)
    if not dataset_after["ok"]:
        print("运行后数据冻结校验失败；本轮结果作废：")
        for issue in dataset_after["issues"]:
            print(f"- {issue}")
        return 4
    if dataset_after["dataset_version"] != dataset["dataset_version"]:
        print("数据集在运行后发生变化；本轮结果作废")
        return 4

    outputs = _write_result(output_dir, runs, gold, dataset, mode=args.mode,
                            model=model, base_url=base_url)
    metrics = outputs["combined_metrics"]
    print(f"准确率 {metrics['judgment_accuracy']}；错误召回 {metrics['error_recall']}；"
          f"失败率 {metrics['failure_rate']}；一致性 {outputs['consistency']['consistency_rate']}")
    print(f"报告 {output_dir / 'summary.md'}")
    print(f"原始结果 {output_dir / 'raw.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
