"""构建标明来源与模拟性质的公告样例、期间演示及补充审计输入。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from announcements import extract_announcement
from materials import Run, register, sha256, write_json
from trace import build_trace


def synthetic_pdf():
    doc = pymupdf.open()
    samples = [
        "模拟股份质押公告（测试材料，不对应真实交易）\n股东名称：模拟甲公司\n质押数量：100万股\n质权人：模拟银行\n质押起始日：2025年9月1日\n质押到期日：2026年9月1日\n质押用途：经营周转\n占公司总股本比例：2.5%",
        "模拟中标公告（测试材料，不对应真实交易）\n1、项目名称：模拟智慧校园项目\n2、中标人：模拟乙公司\n3、中标金额：人民币350万元\n4、采购人：模拟高校\n5、公告日期：2025年9月1日\n6、合同期限：180日",
        "模拟股份转让公告（测试材料，不对应真实交易）\n转让方：模拟甲公司\n受让方：模拟乙公司\n转让股数：200万股\n转让日期：2025年9月2日\n转让比例：5%\n转让方式：协议转让\n交易金额：人民币1200万元",
    ]
    for content in samples:
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname="demo_cjk", fontbuffer=pymupdf.Font("cjk").buffer)
        remaining = page.insert_textbox(pymupdf.Rect(40, 45, 555, 780), content, fontname="demo_cjk", fontsize=13)
        if remaining < 0:
            raise ValueError("模拟材料文字超出页面")
    return doc


def main():
    folder = ROOT / "data/demo"
    folder.mkdir(parents=True, exist_ok=True)
    doc = synthetic_pdf()
    doc.save(folder / "模拟公告样例.pdf", deflate=True)
    scan = pymupdf.open()
    page = scan.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=doc[1].get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png"))
    scan.save(folder / "模拟扫描中标公告.pdf", deflate=True)
    scan.close()
    doc.close()
    urls = {
        "moutai_2025_q1": "https://static.cninfo.com.cn/finalpage/2025-04-30/1223413786.PDF",
        "moutai_2025_half": "https://static.cninfo.com.cn/finalpage/2025-08-13/1224462930.PDF",
        "fosun_2025_pledge": "https://static.cninfo.com.cn/finalpage/2025-08-16/1224499267.PDF",
        "suitang_2025_winning": "https://static.cninfo.com.cn/finalpage/2025-06-17/1223898906.PDF",
    }
    sources = []
    for name, url in urls.items():
        path = folder / "real" / (name+".pdf")
        blob = path.read_bytes()
        with pymupdf.open(path) as report:
            sources.append({"name": name, "local_file": str(path.relative_to(ROOT)), "source_url": url,
                            "sha256": sha256(blob), "page_count": report.page_count,
                            "retrieved_on": "2026-10-07", "simulated": False,
                            "license_status": "public_disclosure_redistribution_unverified"})
    write_json(folder / "sources.json", sources)
    run = Run(ROOT, "register-interim-demos", {"sources": "data/demo/sources.json"})
    for name, title, announcement, disclosed in (
        ("moutai_2025_q1", "贵州茅台2025年第一季度报告", "1223413786", "2025-04-30"),
        ("moutai_2025_half", "贵州茅台2025年半年度报告", "1224462930", "2025-08-13")):
        path = folder / "real" / (name+".pdf")
        register(ROOT, run.read(path), {"company_code": "600519", "company_name": "贵州茅台", "report_year": 2025,
            "announcement_id": announcement, "title": title, "disclosed_at": disclosed,
            "disclosure_date_status": "source_url_date_unverified_against_api", "source_url": urls[name],
            "version_policy": "fixed_validation_fixture", "needs_version_review": True,
            "license_status": "public_disclosure_redistribution_unverified", "local_file": str(path.relative_to(ROOT))}, run, by_reference=True)
    run.finish(status="ok", registered=2)
    samples = [
        ("真实复星股份质押公告", folder / "real/fosun_2025_pledge.pdf", "pledge", False, False),
        ("真实隧唐科技中标公告（预估金额需复核）", folder / "real/suitang_2025_winning.pdf", "winning_bid", False, False),
        *[("模拟"+label, folder / "模拟公告样例.pdf", kind, True, False) for label, kind in (
            ("质押", "pledge"), ("中标", "winning_bid"), ("股权变动", "equity_change"))],
        ("模拟扫描中标公告", folder / "模拟扫描中标公告.pdf", "winning_bid", True, True),
    ]
    bundles = []
    (ROOT / "docs/pages").mkdir(parents=True, exist_ok=True)
    for label, path, kind, simulated, ocr in samples:
        with tempfile.TemporaryDirectory() as temp:
            run = Run(Path(temp), "announcement-demo", {"sample": label, "ocr": ocr})
            output = extract_announcement(run.read(path), kind, run=run, source_file=str(path.relative_to(ROOT)), ocr=ocr)
            evidence = []
            with pymupdf.open(path) as pdf:
                for event in output["events"]:
                    for field, value in event["fields"].items():
                        for i, location in enumerate(value["evidence"]):
                            page = location["page"]
                            image = f"announcement_{output['source_sha256'][:12]}_p{page}.png"
                            pdf[page-1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(ROOT / "docs/pages" / image)
                            evidence.append({"evidence_id": f"{event['event_id']}_{field}_{i}", "metric_name": field,
                                "page": page, "page_image": f"pages/{image}", "value": location["value"],
                                "raw_value": location["quote"], "original_label": field,
                                "value_bbox": location["bbox"], "unit": location.get("unit"),
                                "normalized_value": location["normalized_value"], "normalized_unit": location.get("normalized_unit"),
                                "source_file": str(path.relative_to(ROOT)), "source_sha256": output["source_sha256"],
                                "extraction_method": "OCR / needs_review" if ocr else "labeled_text_or_table"})
            run.finish(status=output["status"], events=len(output["events"]))
            bundles.append({**output, "sample_name": label, "simulated": simulated,
                            "evidence": evidence, "trace": build_trace(run.events, evidence)})
    write_json(folder / "announcement_examples.json", bundles)
    claims = [
        {"id": "PE-DEMO", "kind": "valuation", "sentence": "贵州茅台2024年EPS口径，假设2026-10-07股价1000元/股，PE为20.00倍。",
         "company_code": "600519", "period_year": 2024, "multiple": "PE", "basis": "annual", "as_of": "2026-10-07", "value": "20.00",
         "numerator": {"company_code": "600519", "as_of": "2026-10-07", "value": "1000", "unit": "元/股",
                       "source": "synthetic_draft_assumption", "source_quote": "假设2026-10-07股价1000元/股"}},
        {"id": "REF-OOB", "kind": "reference", "sentence": "贵州茅台2024年营业收入来自PDF页序999999页。",
         "company_code": "600519", "source_report_year": 2024, "period_year": 2024, "metric": "revenue", "page": 999999, "numbering": "physical"},
        {"id": "REF-SUPPORTED", "kind": "reference", "sentence": "贵州茅台2024年营业收入1708.99亿元，见PDF页序5页。",
         "company_code": "600519", "source_report_year": 2024, "period_year": 2024, "metric": "revenue", "page": 5, "numbering": "physical", "value": "1708.99", "unit": "亿元"},
        {"id": "REF-UNKNOWN", "kind": "reference", "sentence": "贵州茅台2024年营业收入见第58页。",
         "company_code": "600519", "source_report_year": 2024, "period_year": 2024, "metric": "revenue", "page": 58, "numbering": "unknown"},
    ]
    write_json(folder / "audit_claims.json", claims)
    print(f"公告样例 {len(bundles)} 组；真实来源 {len(sources)} 份；补充核查主张 {len(claims)} 条")


if __name__ == "__main__":
    main()
