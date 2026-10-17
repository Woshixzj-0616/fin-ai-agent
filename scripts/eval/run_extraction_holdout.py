"""P2 独立抽取留出集运行器。

用法（仓库根）：
  python scripts/eval/run_extraction_holdout.py
  python scripts/eval/run_extraction_holdout.py --output results/extraction_holdout/p2-baseline-20261017
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from extraction_holdout import run_holdout  # noqa: E402
from materials import write_json  # noqa: E402

PROTOCOL = ROOT / "data" / "extraction_holdout" / "manifest.json"


def render_markdown(report: dict, protocol: dict) -> str:
    metrics = report["dataset_verification"]
    lines = [
        "# P2 独立抽取留出集基线",
        "",
        f"- 数据集：`{protocol['dataset_id']}`",
        f"- 源数据版本：`{metrics['dataset_version']}`",
        f"- 公司数：{metrics['company_count']}",
        f"- 材料数：{metrics['material_count']}",
        f"- 目标事实：{report['total']}",
        f"- 独立人工复核：{'有' if protocol.get('independent_review') else '无'}",
        f"- 盲测状态：{protocol['blind_status']}",
        "",
        "## 字段核对",
        "",
        "| 字段 | 正确 | 错误 |",
        "|---|---:|---:|",
        f"| 归一化数值 | {report['value_correct']} / {report['total']} | {report['mismatch_counts']['value']} |",
        f"| 期间 | {report['period_correct']} / {report['total']} | {report['mismatch_counts']['period']} |",
        f"| 单位 | {report['unit_correct']} / {report['total']} | {report['mismatch_counts']['unit']} |",
        f"| 页码 | {report['page_correct']} / {report['total']} | {report['mismatch_counts']['page']} |",
        f"| 标签 | {report['label_correct']} / {report['total']} | {report['mismatch_counts']['label']} |",
        f"| 位置/bbox | {report['located']} / {report['total']} | {report['mismatch_counts']['location']} |",
        "",
        "## 安全指标",
        "",
        f"- 全字段正确：{report['correct']} / {report['total']}",
        f"- 材料失败：{report['material_failures']}（全部显式转复核）",
        f"- 缺失目标：{report['missing']}",
        f"- 显式不确定：{report['explicit_uncertain']}",
        f"- 静默错误值：{report['silent_errors']}",
        "",
        "## 材料状态",
        "",
        "| 文档 | 状态 | 事实数 | 待复核 |",
        "|---|---|---:|---:|",
    ]
    for material in report["material_runs"]:
        lines.append(
            f"| {material['document_id']} | {material['status']} | "
            f"{material.get('fact_count', '—')} | {material.get('needs_review', '—')} |")
    lines += [
        "",
        "## 边界",
        "",
        "- 只在评分阶段读取冻结 facts；不对失败材料补猜值。",
        "- 不得称为盲测准确率；当前无独立人工复核。",
        "- 扫描件和无法唯一定位期间的材料必须显式转人工复核。",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results" / "extraction_holdout" / "p2-baseline-20261017")
    args = parser.parse_args(argv)
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    result = run_holdout(ROOT, PROTOCOL)
    report = result["report"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_md = render_markdown(report, protocol)
    (output / "summary.md").write_text(report_md + "\n", encoding="utf-8")
    write_json(output / "raw.json", result)
    passed = (report["material_failures"] == 0
              and report["correct"] == report["total"]
              and report["silent_errors"] == 0)
    print(report_md)
    print(f"\n输出：{output}")
    print("P2 extraction holdout:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
