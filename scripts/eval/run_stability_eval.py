"""P3 首批离线稳定性基线：不连接真实模型，不读取真实密钥。

用法（仓库根）：
  python scripts/eval/run_stability_eval.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from materials import write_json  # noqa: E402
from stability import run_offline_fault_matrix  # noqa: E402

OFFLINE_SECRET = "p3-offline-test-credential"


def render_markdown(result: dict) -> str:
    lines = [
        "# P3 离线稳定性基线",
        "",
        "- 范围：失败分类、有限重试、协议完整性、部分完成保留和密钥安全。",
        "- 真实模型调用：0。",
        "- 独立人工复核：无。",
        "",
        "## 故障矩阵",
        "",
        "| 场景 | 失败码 | 尝试数 | 结果 |",
        "|---|---|---:|---|",
    ]
    for row in result["cases"]:
        lines.append(
            f"| {row['name']} | `{row['code']}` | {row['attempts']} | "
            f"{'PASS' if row['passed'] else 'FAIL'} |"
        )
    lines += [
        "",
        "## 结论",
        "",
        f"- 场景：{result['case_count']}；通过：{sum(row['passed'] for row in result['cases'])}。",
        f"- 总体：{'PASS' if result['ok'] else 'FAIL'}。",
        "",
        "## 边界",
        "",
        "- 本基线用本地 MockTransport 和内存注入，不证明真实网络、真实服务商限流或真实模型质量。",
        "- 超时、429、401、无效 JSON、截断 SSE、模型身份变化、部分成功和密钥泄露均有显式断言。",
        "- 中断恢复、取消和工具失败由既有全量回归共同覆盖，本表不重复折算成模型准确率。",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results" / "stability" / "p3-baseline-20261017")
    args = parser.parse_args(argv)
    result = run_offline_fault_matrix(OFFLINE_SECRET)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = render_markdown(result)
    (output / "summary.md").write_text(summary + "\n", encoding="utf-8")
    write_json(output / "raw.json", result)
    print(summary)
    print(f"\n输出：{output}")
    print("P3 offline stability:", "PASS" if result["ok"] else "FAIL")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
