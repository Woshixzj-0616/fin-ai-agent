"""把 docs/初赛/计划书.md 排成 PDF（决赛/初赛提交用）。中文用系统字体。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "初赛" / "计划书.md"
OUT = ROOT / "docs" / "初赛" / "计划书.pdf"


def md_to_html(text: str) -> str:
    lines = text.splitlines()
    out = []
    in_table = False
    in_list = False
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if in_list and not line.startswith(("- ", "* ")):
            out.append("</ul>")
            in_list = False
        if line.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{line[2:]}</blockquote>")
        elif line.startswith("---"):
            out.append("<hr/>")
        elif line.startswith("```"):
            continue
        elif line.strip():
            out.append(f"<p>{line}</p>")
    if in_table:
        out.append("</table>")
    if in_list:
        out.append("</ul>")
    body = "\n".join(out)
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
    body = re.sub(r"`([^`]+)`", r"<code>\1</code>", body)
    css = """
    body { font-family: sans-serif; font-size: 11px; line-height: 1.45; color: #222; }
    h1 { font-size: 20px; color: #1a365d; border-bottom: 2px solid #2b6cb0; padding-bottom: 6px; }
    h2 { font-size: 15px; color: #2b6cb0; margin-top: 16px; }
    h3 { font-size: 12px; color: #2c5282; }
    table { border-collapse: collapse; width: 100%; margin: 8px 0; }
    th, td { border: 1px solid #cbd5e0; padding: 4px 6px; text-align: left; }
    th { background: #edf2f7; }
    blockquote { color: #4a5568; border-left: 3px solid #a0aec0; margin: 6px 0; padding: 2px 10px; }
    code { background: #edf2f7; padding: 1px 4px; }
    hr { border: none; border-top: 1px solid #e2e8f0; margin: 12px 0; }
    li { margin: 2px 0; }
    """
    return f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"


def main() -> int:
    html = md_to_html(SRC.read_text(encoding="utf-8"))
    story = pymupdf.Story(html=html)
    writer = pymupdf.DocumentWriter(str(OUT))
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (40, 40, -40, -40)
    more, pages = 1, 0
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
        pages += 1
    writer.close()
    print(f"已生成 {OUT}（{pages} 页）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
