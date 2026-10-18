"""P3 真实长文档稳定性运行器。

用法（仓库根）：
  python -B scripts/eval/run_live_stability.py --runs 3 --repeats 4 \
    --from-travel-env --output-dir results/stability/p3-live-20261017
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "scripts" / "eval"))

from backend_runtime import load_config  # noqa: E402
from holdout_eval import verify_dataset  # noqa: E402
from live_stability import render_summary, run_live_rounds  # noqa: E402
from llm_check import LLMClient  # noqa: E402
from materials import write_json  # noqa: E402
from run_llm_eval import load_travel_env, pick_provider  # noqa: E402

DATASET = ROOT / "data" / "holdout"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P3 真实长文档稳定性运行器")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "results" / "stability" / "p3-live-20261017")
    parser.add_argument("--from-travel-env", action="store_true")
    parser.add_argument("--use-loop", action="store_true")
    parser.add_argument("--chunk-sentences", type=int)
    args = parser.parse_args(argv)

    dataset = verify_dataset(DATASET)
    if not dataset["ok"]:
        print("数据集冻结校验失败：")
        for issue in dataset["issues"]:
            print(f"- {issue}")
        return 2

    picked = load_travel_env() if args.from_travel_env else {}
    base_url, model, key = pick_provider(picked)
    if not key:
        print("未配置模型密钥；请设置 LLM_API_KEY 或加 --from-travel-env")
        return 3

    config = load_config()
    if args.chunk_sentences is not None:
        config["document"]["chunk_sentences"] = args.chunk_sentences

    def client_factory():
        return LLMClient(base_url, model, key, "json_object", config=config)

    gold = _read_json(DATASET / "gold.json")
    facts = _read_json(DATASET / "facts.json")["facts"]
    output_dir = args.output_dir.resolve()
    host = urlsplit(base_url).hostname
    print(f"真实长文协议：{len(gold.get('statements') or []) * args.repeats} 句，"
          f"{args.runs} 轮，provider={host}，model={model}")
    result = run_live_rounds(
        gold,
        facts,
        output_dir,
        client_factory=client_factory,
        runs=args.runs,
        repeats=args.repeats,
        config=config,
        use_loop=args.use_loop,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = render_summary(result)
    (output_dir / "summary.md").write_text(summary + "\n", encoding="utf-8")
    write_json(output_dir / "raw.json", result)
    print(summary)
    print(f"\n输出：{output_dir}")
    print("P3 live stability:", result["assessment"]["status"])
    return 0 if result["assessment"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
