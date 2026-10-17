"""Render an edited, source-linked local report package as a readable Markdown export."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def references(pages: list[int]) -> str:
    return "（" + "、".join(f"年报第{page}页" for page in dict.fromkeys(pages)) + "）" if pages else ""


def render(report: dict) -> str:
    lines = [
        f"# {report['title']}", "", f"**{report['subtitle']}**", "",
        f"{report['company']} · {report['period']} · {report['scope']}", "",
        "## 核心判断", "", report["lead"], "",
    ]
    for item in report["highlights"]:
        lines.extend([f"- **{item['title']}**：{item['detail']} {references(item['pages'])}"])
    lines.extend(["", "## 关键财务数据", "", "| 指标 | 2023 年 | 2022 年 | 变化 | 来源 |", "| --- | ---: | ---: | ---: | --- |"])
    for item in report["metrics"]:
        lines.append(f"| {item['label']} | {item['current']} {item['unit']} | {item['previous']} {item['unit']} | {item['change']} | {references(item['pages'])} |")
    for section in report["sections"]:
        lines.extend(["", f"## {section['number']} {section['title']}", "", f"*{section['summary']}*", ""])
        for block in section["blocks"]:
            if block["type"] == "table":
                lines.extend([f"**{block['title']}**", "", "| " + " | ".join(block["columns"]) + " |", "| " + " | ".join("---" for _ in block["columns"]) + " |"])
                lines.extend("| " + " | ".join(row) + " |" for row in block["rows"])
                lines.extend(["", f"{block['unit']}。{references(block['pages'])}", ""])
            else:
                prefix = "**口径说明：**" if block["type"] == "note" else ""
                lines.extend([f"{prefix}{block['text']} {references(block['pages'])}", ""])
    lines.extend(["## 资料与计算口径", "", f"{report['source_title']}。{report['method']}", ""])
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    source = Path(sys.argv[1])
    report = json.loads(source.read_text(encoding="utf-8"))
    output = source.with_name("report.md")
    output.write_text(render(report), encoding="utf-8")
    print(output)
