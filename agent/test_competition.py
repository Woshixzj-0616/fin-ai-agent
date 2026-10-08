"""赛题补齐回归：真实材料读值、期间、公告冲突、倍数、引用与秘密门控。

真实 PDF 的固定期望值由原表逐项核对录入，不作为独立人工盲测准确率。
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from announcements import extract_announcement, normalize_field
from audit_checks import check_draft_supplements, check_reference, check_valuation
from finance import analyze, check_claim, decimal
from extract import corroborate_scope, metric_for, statement_sections
from interim import extract_interim, header_period
from materials import ROOT, Run, sha256
from periods import derive_quarter, growth, report_period
from qa import answer
from trace import build_trace
from webui import _check_draft, _parse_multipart, run_announcement_pipeline, run_pipeline


def text_pdf(content: str, labels=False) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=1000, height=800)
    page.insert_font(fontname="fixture_cjk", fontbuffer=pymupdf.Font("cjk").buffer)
    page.insert_textbox(pymupdf.Rect(30, 30, 970, 750), content,
                        fontname="fixture_cjk", fontsize=12)
    if labels:
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 58}])
    blob = doc.tobytes()
    doc.close()
    return blob


def pledge_table(rows, body="") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=1000, height=800)
    page.insert_font(fontname="fixture_cjk", fontbuffer=pymupdf.Font("cjk").buffer)
    page.insert_text((30, 35), "模拟公司股份质押公告（人工构造测试）", fontname="fixture_cjk", fontsize=12)
    headers = ["股东名称", "质押数量（万股）", "质权人", "质押起始日"]
    bounds = [30, 250, 450, 700, 970]
    for ri, row in enumerate([headers, *rows]):
        for ci, raw in enumerate(row):
            rect = pymupdf.Rect(bounds[ci], 70+ri*45, bounds[ci+1], 115+ri*45)
            page.draw_rect(rect)
            page.insert_text((rect.x0+5, rect.y0+24), raw, fontname="fixture_cjk", fontsize=11)
    if body:
        page.insert_textbox(pymupdf.Rect(30, 260, 970, 700), body, fontname="fixture_cjk", fontsize=12)
    blob = doc.tobytes()
    doc.close()
    return blob


def fact(**changes):
    f = {"company_code": "600519", "company_name": "贵州茅台", "metric": "revenue", "metric_name": "营业收入",
         "period_year": 2025, "report_year": 2025, "period_start": "2025-01-01", "period_end": "2025-03-31",
         "period_kind": "quarter", "duration_months": 3, "value": "100", "unit": "元",
         "normalized_value": "100", "normalized_unit": "元", "currency": "CNY", "scope": "consolidated",
         "adjustment": "as_reported", "issues": [], "evidence_id": "q1", "document_id": "q1",
         "comparison_group": "q1", "source_file": "data/source.pdf", "source_sha256": "", "page": 1}
    f.update(changes)
    return f


class PeriodRegressionTests(unittest.TestCase):
    def test_annual_claim_with_interim_materials(self):
        annual = fact(evidence_id="annual", document_id="annual", period_kind="annual", period_end="2025-12-31", duration_months=12, value="1000", normalized_value="1000")
        claim = {"id": "annual", "sentence": "2025全年收入1000元", "company_code": "600519", "period_year": 2025,
                 "source_report_year": 2025, "metric": "revenue", "kind": "amount", "value": "1000", "unit": "元",
                 "currency": "CNY", "scope": "consolidated", "period_kind": "annual", "operator": "eq"}
        out = check_claim(claim, [fact(), annual])
        self.assertEqual(out["status"], "证据支持")
        self.assertEqual(out["evidence_ids"], ["annual"])

    def test_qa_period_filters_and_precise_labels(self):
        q1 = fact(value="100", normalized_value="100")
        prior = fact(evidence_id="prior", period_year=2024, period_start="2024-01-01", period_end="2024-03-31", value="80", normalized_value="80")
        half = fact(evidence_id="half", period_kind="half", period_end="2025-06-30", duration_months=6, value="250", normalized_value="250")
        out = answer("贵州茅台2025年第一季度营业收入同比是多少？", [q1, prior, half], use_llm=False)
        self.assertEqual(out["status"], "ok")
        self.assertIn("25%", out["answer"])
        self.assertIn("2025-03-31", out["answer"])
        self.assertEqual(set(out["evidence_ids"]), {"q1", "prior"})
        self.assertEqual(answer("贵州茅台2025年营业收入是多少？", [q1, half], use_llm=False)["status"], "insufficient_evidence")
        self.assertEqual(answer("贵州茅台2025年全年营业收入是多少？", [half], use_llm=False)["status"], "insufficient_evidence")

    def test_qa_overview_keeps_periods_and_rejects_flagged(self):
        half = fact(evidence_id="half", document_id="half", period_kind="half", period_end="2025-06-30", duration_months=6, value="250")
        out = answer("指标概览", [fact(), half], use_llm=False)
        self.assertEqual(set(out["evidence_ids"]), {"q1", "half"})
        self.assertIn("2025-06-30", out["answer"])
        self.assertEqual(answer("2025年第一季度营业收入是多少？", [fact(issues=["value_conflict"])], use_llm=False)["status"], "insufficient_evidence")

    def test_report_identity_and_header_excludes_growth(self):
        self.assertEqual(metric_for("扣除非经常性损益后的加权平\n均净资产收益率（%）"), "deducted_weighted_roe")
        for title, kind, month in [("2025年半年度报告", "half", 6), ("2025年第三季度报告", "quarter", 9),
                                   ("2025年年度报告", "annual", 12)]:
            with self.subTest(title=title):
                identity = report_period(title, 2025)
                self.assertEqual((identity["report_kind"], identity["report_month"]), (kind, month))
        q3 = report_period("2025年第三季度报告")
        self.assertEqual(header_period("年初至报告期末", q3, False)["duration_months"], 9)
        self.assertIsNone(header_period("本报告期比上年同期\n增减(%)", q3, False))

    def test_half_yoy_and_unmatched_period(self):
        a = fact(period_kind="half", duration_months=6, period_end="2025-06-30", value="120")
        b = fact(period_year=2024, period_start="2024-01-01", period_end="2024-06-30", value="100")
        self.assertEqual(decimal(growth(a, b)["value"]), decimal("20"))
        self.assertEqual(growth(a, fact(period_year=2024, period_start="2024-01-01", period_end="2024-03-31"))["status"], "not_comparable")

    def test_split_quarter_and_qoq(self):
        q1 = fact()
        half = fact(evidence_id="h1", duration_months=6, period_kind="half", period_end="2025-06-30", value="250")
        d = derive_quarter(half, q1)
        self.assertEqual(d["value"], "150")
        self.assertEqual(d["evidence_ids"], ["h1", "q1"])
        q2 = fact(**{k: d[k] for k in ("period_start", "period_end", "value")})
        self.assertEqual(decimal(growth(q2, q1, "qoq")["value"]), decimal("50"))
        self.assertEqual(growth(half, q1, "qoq")["status"], "not_comparable")

    def test_no_splitting_stocks_eps_or_roe(self):
        for metric in ("total_assets", "parent_equity", "basic_eps", "weighted_roe"):
            with self.subTest(metric=metric):
                self.assertEqual(derive_quarter(fact(metric=metric))["status"], "not_comparable")

    def test_bad_base_company_scope_currency_and_missing(self):
        half = fact(duration_months=6, period_end="2025-06-30", value="250")
        for changed in ({"company_code": "000858"}, {"currency": "USD"}, {"scope": "parent_only"},
                        {"issues": ["unit_unknown"]}, {"adjustment": "before"}):
            with self.subTest(changed=changed):
                self.assertEqual(derive_quarter(half, fact(**changed))["status"], "not_comparable")
        self.assertEqual(derive_quarter(half)["status"], "missing")
        self.assertEqual(derive_quarter(fact(issues=["conflict"]))["status"], "not_comparable")
        self.assertEqual(growth(fact(period_start="2025-04-01", period_end="2025-06-30"), fact(value="-10"), "qoq")["status"], "negative_base")


class AnnouncementRegressionTests(unittest.TestCase):
    def test_normalization_keeps_original_and_converts_units(self):
        self.assertEqual(normalize_field("shares", "1,250", "质押数量（万股）")["normalized_value"], "12500000")
        self.assertEqual(normalize_field("amount", "人民币3.50亿元")["normalized_value"], "350000000.00")
        self.assertEqual(normalize_field("start_date", "2025年2月30日")["issues"], ["date_unresolved"])
        self.assertEqual(normalize_field("pledgor", "模拟\n公司")["normalized_value"], "模拟公司")

    def test_three_schemas_with_labeled_text(self):
        samples = {
            "pledge": "模拟股份质押公告\n股东名称：模拟甲公司\n质押数量：100万股\n质权人：模拟银行\n质押起始日：2025年9月1日",
            "winning_bid": "模拟中标公告\n1、项目名称：模拟智慧校园项目\n2、中标人：模拟乙公司\n3、投标报价：人民币3.50亿元",
            "equity_change": "模拟股份转让公告\n转让方：模拟甲公司\n受让方：模拟乙公司\n转让股数：200万股\n转让日期：2025年9月2日"}
        for kind, content in samples.items():
            with self.subTest(kind=kind):
                result = extract_announcement(text_pdf(content), kind)
                self.assertEqual(len(result["events"]), 1)
                self.assertEqual(result["events"][0]["missing_required"], [])
                self.assertEqual(result["events"][0]["status"], "extracted")

    def test_same_pledgor_two_transactions_stay_separate(self):
        pdf = pledge_table([["模拟甲公司", "100", "模拟银行甲", "2025-09-01"],
                            ["模拟甲公司", "200", "模拟银行乙", "2025-09-02"]])
        events = extract_announcement(pdf, "pledge")["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual({e["fields"]["shares"]["normalized_value"] for e in events}, {"1000000", "2000000"})
        self.assertTrue(all(not e["conflicts"] for e in events))

    def test_table_and_body_conflict_is_reported(self):
        pdf = pledge_table([["模拟甲公司", "100", "模拟银行", "2025-09-01"]],
                           "股东名称：模拟甲公司\n质押数量：200万股\n质权人：模拟银行\n质押起始日：2025-09-01")
        events = extract_announcement(pdf, "pledge")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["conflicts"], ["shares"])
        self.assertEqual(events[0]["status"], "needs_review")

    def test_missing_and_scan_do_not_claim_success(self):
        event = extract_announcement(text_pdf("模拟中标公告\n项目名称：测试项目\n中标人：模拟公司\n未公布中标金额"), "winning_bid")["events"][0]
        self.assertEqual(event["missing_required"], ["amount"])
        doc = pymupdf.open()
        doc.new_page()
        with self.assertRaisesRegex(ValueError, "扫描"):
            extract_announcement(doc.tobytes(), "pledge")

    def test_configured_ocr_scanned_labels_are_reviewed(self):
        if not all((ROOT / f"data/ocr/tessdata/{lang}.traineddata").is_file() for lang in ("chi_sim", "eng")):
            self.skipTest("可选 OCR 语言数据未配置；先运行 scripts/setup_ocr.py")
        original = pymupdf.open(stream=text_pdf("模拟中标公告\n项目名称：模拟智慧校园项目\n中标人：模拟乙公司\n中标金额：人民币350万元"), filetype="pdf")
        scan = pymupdf.open()
        page = scan.new_page(width=1000, height=800)
        page.insert_image(page.rect, stream=original[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png"))
        out = extract_announcement(scan.tobytes(), "winning_bid", ocr=True)
        self.assertTrue(out["ocr_used"])
        self.assertEqual(out["events"][0]["fields"]["amount"]["normalized_value"], "3500000")
        self.assertEqual(out["events"][0]["status"], "needs_review")
        self.assertTrue(all(f["status"] != "extracted" for f in out["events"][0]["fields"].values()))
        with tempfile.TemporaryDirectory() as temp:
            payload = run_announcement_pipeline(scan.tobytes(), "winning_bid", root=Path(temp), ocr=True)
            self.assertEqual(len(payload["issues"]), len(payload["evidence"]))
            self.assertTrue(payload["issues"])
            self.assertIn("3500000", payload["report_md"])
            self.assertIn("needs_review", payload["report_md"])
            self.assertEqual(payload["counts"]["events"], 1)
        original.close()
        scan.close()


class AuditRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        path = self.root / "data/source.pdf"
        path.parent.mkdir()
        path.write_bytes(text_pdf("贵州茅台2024年年度报告\n营业收入：100元\n原文表格与文字证据，仅用于自动化测试。", labels=True))
        self.facts = [fact(period_year=2024, report_year=2024, period_kind="annual", duration_months=12,
                           period_start="2024-01-01", period_end="2024-12-31", source_sha256=sha256(path.read_bytes()))]
        self.eps = fact(metric="basic_eps", metric_name="基本每股收益", period_year=2024, report_year=2024,
                        period_kind="annual", period_end="2024-12-31", value="10", unit="元/股")
        self.claim = {"id": "v1", "sentence": "贵州茅台2024年PE为20倍", "multiple": "PE", "company_code": "600519",
                      "period_year": 2024, "basis": "annual", "as_of": "2025-06-01", "value": "20",
                      "numerator": {"value": "100", "unit": "元/股", "company_code": "600519", "as_of": "2025-06-01",
                                    "source": "draft", "source_quote": "2025-06-01股价100元"}}
    def tearDown(self):
        self.temp.cleanup()

    def test_multiple_correction_and_internal_scope(self):
        out = check_valuation(self.claim, [self.eps])
        self.assertEqual(out["status"], "确认错误")
        self.assertEqual(out["correction"]["revised_sentence"], "贵州茅台2024年PE为10倍")
        self.assertIn("未独立核实", out["verification_scope"])

    def test_pb_and_ev_inputs(self):
        pb = check_valuation({**self.claim, "multiple": "PB", "value": "10"}, [{**self.eps, "metric": "book_value_per_share"}])
        self.assertEqual(pb["status"], "证据支持")
        ev = {**self.claim, "multiple": "EV/EBITDA", "value": "10", "basis": "draft_explicit",
              "numerator": {**self.claim["numerator"], "unit": "亿元"},
              "denominator": {"company_code": "600519", "period_year": 2024, "source": "draft", "source_quote": "EBITDA10亿元", "value": "10", "unit": "亿元"}}
        self.assertEqual(check_valuation(ev, [])["status"], "证据支持")

    def test_multiple_bad_inputs_abstain(self):
        for changes, eps in [({"basis": "TTM"}, self.eps), ({"as_of": "2024-06-01"}, self.eps),
                             ({}, {**self.eps, "value": "0"}), ({}, {**self.eps, "value": "-1"}),
                             ({}, {**self.eps, "value": "invalid"}), ({"operator": "bogus"}, self.eps)]:
            with self.subTest(changes=changes, value=eps["value"]):
                out = check_valuation({**self.claim, **changes}, [eps])
                self.assertEqual(out["status"], "口径冲突／需人工复核")

    def test_reference_physical_printed_and_out_of_range(self):
        c = {"id": "r1", "company_code": "600519", "source_report_year": 2024, "period_year": 2024,
             "metric": "revenue", "page": 1, "numbering": "physical", "value": "100", "unit": "元"}
        self.assertEqual(check_reference(c, self.facts, root=self.root)["status"], "证据支持")
        self.assertEqual(check_reference({**c, "page": 58, "numbering": "printed"}, self.facts, root=self.root)["status"], "证据支持")
        self.assertEqual(check_reference({**c, "page": 58}, self.facts, root=self.root)["reason_code"], "reference_page_out_of_range")
        self.assertEqual(check_reference({**c, "numbering": "unknown"}, self.facts, root=self.root)["reason_code"], "reference_page_numbering_unknown")

    def test_missing_source_and_unsupported_quote_are_not_fake(self):
        c = {"company_code": "600519", "source_report_year": 2024, "page": 1, "numbering": "physical", "source_quote": "并不存在的引用"}
        out = check_reference(c, self.facts, root=self.root)
        self.assertEqual(out["reason_code"], "reference_not_supported")
        self.assertNotEqual(out["status"], "确认错误")
        (self.root / "data/source.pdf").unlink()
        self.assertEqual(check_reference(c, self.facts, root=self.root)["status"], "证据不足")

    def test_two_companies_do_not_borrow_price(self):
        facts = [self.eps, {**self.eps, "company_code": "000858", "company_name": "五粮液", "evidence_id": "wly"}]
        draft = "贵州茅台2024年，2025-06-01股价100元。五粮液2024年PE为10倍。"
        out = check_draft_supplements(draft, facts, root=self.root)
        self.assertEqual(out[0]["reason_code"], "missing_valuation_date")

    def test_secret_rejected_before_persistence(self):
        secret = "sk-" + "s"*36
        draft = "贵州茅台2024年PE为20倍。" + secret
        with self.assertRaisesRegex(ValueError, "密钥"):
            check_draft_supplements(draft, [self.eps], root=self.root)
        run = Run(self.root, "secret-test", {})
        with patch("llm_check.LLMClient.from_environment") as client:
            out = _check_draft(draft, [self.eps], run)
        client.assert_not_called()
        self.assertEqual(out["status"], "failed")
        self.assertFalse((self.root / "results/webui_draft.txt").exists())
        self.assertNotIn(secret, "".join(run.events))

    def test_run_archives_all_core_sources_and_final_trace(self):
        run = Run(self.root, "trace-test", {})
        run.event("calculation", formula="120/100-1", evidence_ids=["q1"])
        run.finish(status="ok")
        self.assertTrue({"words.py", "tools.py", "webui.py", "agent_loop.py", "announcements.py", "requirements.txt"} <= set(run.sources))
        trace = json.loads((run.folder / "trace.json").read_text(encoding="utf-8"))["steps"]
        self.assertEqual(trace[-1]["event"], "run_finished")
        self.assertEqual(build_trace(run.events, [fact()])[1]["evidence"][0]["evidence_id"], "q1")
        step = build_trace([{"event": "extraction_channel", "evidence_ids": ["q1", "rejected_candidate"]}], [fact()])[0]
        self.assertEqual(step["evidence_ids"], ["q1"])
        self.assertIn("rejected_candidate", step["detail"]["unavailable_evidence_ids"])

    def test_multipart_preserves_pdf_bytes_and_comparison(self):
        blob = b"%PDF-fixture\r\n\r\n"
        part = lambda name: b'--fixture\r\nContent-Disposition: form-data; name="'+name+b'"; filename="a.pdf"\r\n\r\n'+blob+b'\r\n'
        body = part(b"pdf") + part(b"comparison_pdf") + b"--fixture--\r\n"
        handler = type("FakeHandler", (), {"headers": {"Content-Type": "multipart/form-data; boundary=fixture", "Content-Length": str(len(body))}, "rfile": io.BytesIO(body)})()
        fields, actual = _parse_multipart(handler)
        self.assertEqual(actual, blob)
        self.assertEqual(fields["_comparison_pdfs"], [blob])


class RealCompetitionMaterialTests(unittest.TestCase):
    def test_real_annual_cash_flow_folded_scope(self):
        material = next(json.loads(line) for line in (ROOT / "data/agent/materials.jsonl").read_text(encoding="utf-8").splitlines()
                        if (lambda m: m.get("company_code") == "600519" and m.get("report_year") == 2025
                            and "年度报告" in m.get("title", ""))(json.loads(line)))
        path = ROOT / material["local_file"]
        if not path.is_file():
            self.skipTest("需要固定九份年报中的茅台 2025 年报")
        current = fact(metric="operating_cash_flow", value="61522204989.35", normalized_value="61522204989.35")
        with pymupdf.open(path) as doc:
            source = corroborate_scope(doc, statement_sections(doc), current)
        self.assertIsNotNone(source)
        self.assertEqual((source["scope"], source["page"]), ("consolidated", 65))

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.facts = []
        for name in ("moutai_2025_q1", "moutai_2025_half"):
            path = ROOT / "data/demo/real" / (name+".pdf")
            if not path.is_file():
                raise unittest.SkipTest("需要 data/demo/real 中的真实季报与半年报")
            blob = path.read_bytes()
            meta = dict(document_id=name, company_code="600519", company_name="贵州茅台", report_year=2025,
                        local_file=str(path.relative_to(ROOT)), sha256=sha256(blob), source_url="https://static.cninfo.com.cn/", announcement_id=name)
            with pymupdf.open(path) as doc:
                cls.facts.extend(extract_interim(doc, meta, Run(cls.root, "real-interim", {})))
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_values_folded_labels_and_continued_stock_table(self):
        expected = [("moutai_2025_q1", "adjusted_parent_net_profit", "26849883702.90", 1),
                    ("moutai_2025_q1", "total_assets", "312368697395.05", 2),
                    ("moutai_2025_half", "parent_net_profit", "45402962298.10", 5),
                    ("moutai_2025_half", "adjusted_parent_net_profit", "45390247623.82", 5),
                    ("moutai_2025_half", "total_assets", "292257789095.51", 5),
                    ("moutai_2025_half", "deducted_basic_eps", "36.17", 5)]
        self.assertEqual(len(self.facts), 40)
        self.assertTrue(all(not f["issues"] for f in self.facts))
        for doc, metric, value, page in expected:
            with self.subTest(doc=doc, metric=metric):
                f = next(f for f in self.facts if f["document_id"] == doc and f["metric"] == metric and f["period_year"] == 2025)
                self.assertEqual((f["value"], f["page"]), (value, page))

    def test_real_half_minus_q1_and_all_source_ids(self):
        analysis = analyze(self.facts, Run(self.root, "qoq-real", {}))
        q2 = next(r for r in analysis["qoq_rows"] if r["metric"] == "revenue" and r["period_end"] == "2025-06-30")
        self.assertEqual(q2["derivation"]["value"], "38788396531.06")
        self.assertEqual(q2["qoq"]["status"], "ok")
        self.assertAlmostEqual(float(q2["qoq"]["value"]), -23.34454145, places=5)
        self.assertEqual(len(q2["evidence_ids"]), 2)

    def test_real_pledge_and_prose_bid_with_estimate(self):
        pledge = extract_announcement((ROOT / "data/demo/real/fosun_2025_pledge.pdf").read_bytes(), "pledge")["events"]
        self.assertEqual(pledge[0]["fields"]["shares"]["normalized_value"], "49000000")
        self.assertEqual(pledge[0]["fields"]["start_date"]["normalized_value"], "2025-08-14")
        bid = extract_announcement((ROOT / "data/demo/real/suitang_2025_winning.pdf").read_bytes(), "winning_bid")["events"][0]
        self.assertEqual(bid["fields"]["amount"]["normalized_value"], "798000.00")
        self.assertEqual(bid["fields"]["amount"]["status"], "needs_review")
        self.assertEqual(bid["fields"]["project_name"]["normalized_value"], "市场经营AI云平台项目")

    def test_webui_comparison_payload_and_shared_trace(self):
        directory = ROOT / "data/demo/real"
        out = run_pipeline((directory / "moutai_2025_half.pdf").read_bytes(), "600519", "贵州茅台", 2025,
                           workspace=self.root / "work", root=self.root,
                           comparison_blobs=[(directory / "moutai_2025_q1.pdf").read_bytes()])
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["materials"]), 2)
        self.assertTrue(any(r["qoq"]["status"] == "ok" for r in out["qoq_rows"]))
        self.assertEqual(out["trace"][-1]["event"], "run_finished")
        self.assertTrue(all(e["page_image"] for e in out["evidence"]))
        import re
        pdf_links = re.findall(r"\[原始PDF\]\(\.\./([^)]*)\)", out["report_md"])
        self.assertEqual(len(pdf_links), 2)
        self.assertTrue(all((self.root / path).is_file() for path in pdf_links))


if __name__ == "__main__":
    unittest.main()
