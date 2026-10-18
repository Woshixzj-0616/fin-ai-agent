"""把 docs/初赛/计划书.md 排成 PDF（决赛/初赛提交用）。中文用系统字体。"""
from __future__ import annotations

import re
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "初赛" / "计划书.md"
OUT = ROOT / "docs" / "初赛" / "计划书.pdf"
BUILD = OUT.with_name(OUT.stem + "-build.pdf")
NUMBERED = OUT.with_name(OUT.stem + "-numbered.pdf")


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
            out.append(f"<p class='section'>{line[3:]}</p>")
        elif line.startswith("### "):
            out.append(f"<p class='subsection'>{line[4:]}</p>")
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
    @page { size: A4; margin: 17mm 16mm 16mm; }
    body { font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; font-size: 10.5pt; line-height: 1.55; color: #222; margin: 0; }
    h1 { font-size: 20pt; color: #1a365d; border-bottom: 2px solid #2b6cb0; padding-bottom: 6px; }
    p.section { font-size: 15pt; font-weight: bold; color: #2b6cb0; margin: 16px 0 6px; break-after: avoid; }
    p.subsection { font-size: 12pt; font-weight: bold; color: #2c5282; margin: 10px 0 4px; break-after: avoid; }
    table { border-collapse: collapse; width: 100%; margin: 8px 0; }
    tr { break-inside: avoid; }
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
    browser = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not browser.exists():
        browser = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    if not browser.exists():
        raise FileNotFoundError("未找到 Edge 或 Chrome，无法生成 PDF")
    BUILD.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="fin_plan_") as temp_dir:
        temp = Path(temp_dir)
        html_path = temp / "plan.html"
        profile = temp / "browser-profile"
        html_path.write_text(html, encoding="utf-8")
        profile.mkdir()
        result = subprocess.run(
            [
                str(browser),
                "--headless=new",
                "--disable-gpu",
                "--disable-extensions",
                "--no-first-run",
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={BUILD}",
                html_path.as_uri(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
    if result.returncode != 0 or not BUILD.exists():
        raise RuntimeError((result.stderr or result.stdout or "浏览器未生成 PDF").strip())
    doc = pymupdf.open(BUILD)
    total = doc.page_count
    for index, page in enumerate(doc, start=1):
        label = f"{index} / {total}"
        point = pymupdf.Point(page.rect.width / 2 - 10, page.rect.height - 20)
        page.insert_text(point, label, fontsize=8, fontname="helv", color=(0.35, 0.35, 0.35))
    doc.save(NUMBERED, garbage=4, deflate=True)
    doc.close()
    os.replace(NUMBERED, OUT)
    try:
        BUILD.unlink(missing_ok=True)
    except PermissionError:
        print(f"临时文件暂时被占用，已保留：{BUILD}")
    print(f"已生成 {OUT}（{total} 页）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
