"""Build a clearly labeled synthetic report for review-mode acceptance testing."""

from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "cases" / "模拟材料"
OUTPUT_MIXED = OUTPUT_DIR / "人工构造_宁德时代_2024_混合主张.pdf"
OUTPUT_CAUSAL = OUTPUT_DIR / "人工构造_宁德时代_2024_因果主张.pdf"
FONT = Path(r"C:\Windows\Fonts\simhei.ttf")

CLAIMS = [
    "年报显示，宁德时代2024年度营业收入合计为362,012,554千元。",
    "宁德时代2024年度营业收入同比增长9.70%。",
    "宁德时代2024年度储能电池系统收入占营业收入19.83%。",
    "宁德时代2024年度前五名客户销售额占年度销售总额37.03%。",
    "宁德时代2024年度前五名供应商采购额占年度采购总额37.03%。",
    "年报明确表示，2024年境外收入下降完全由汇率波动造成。",
    "预计宁德时代2025年度营业收入将同比增长20%。",
]


def build(output: Path, claims: list[str]) -> None:
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_font(fontname="SIMHEI", fontfile=str(FONT))
    title = "宁德时代2024年报观察（人工构造测试材料）"
    notice = "这不是任何券商或分析师发布的真实研报；仅用于检验系统的核查能力。"
    page.insert_text((45, 52), title, fontname="SIMHEI", fontsize=17)
    page.insert_text((45, 82), notice, fontname="SIMHEI", fontsize=10)
    page.insert_text((45, 105), "材料日期：2025年3月17日    对照：宁德时代2024年年度报告", fontname="SIMHEI", fontsize=10)
    y = 140
    for number, claim in enumerate(claims, 1):
        remaining = page.insert_textbox(
            pymupdf.Rect(45, y, 550, y + 65),
            f"{number}. {claim}",
            fontname="SIMHEI",
            fontsize=12,
            lineheight=1.35,
        )
        if remaining < 0:
            raise SystemExit(f"第 {number} 条文字超出预留区域。")
        y += 82
    pdf.save(output, garbage=4, deflate=True)
    pdf.close()

    check = pymupdf.open(output)
    extracted = check[0].get_text("text")
    check.close()
    missing = [claim for claim in claims if claim not in extracted]
    if missing:
        raise SystemExit(f"生成后文字提取未通过：{len(missing)} 条缺失。")
    print(str(output))


def main() -> None:
    if not FONT.is_file():
        raise SystemExit("缺少用于生成中文测试 PDF 的系统字体。")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build(OUTPUT_MIXED, CLAIMS)
    build(OUTPUT_CAUSAL, [CLAIMS[5]])


if __name__ == "__main__":
    main()
