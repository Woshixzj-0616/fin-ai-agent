"""手工测试返修：年度绑定、真实期间披露、派生拒算与公告字段。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from announcements import extract_announcement
from audit.claim_check import check_one_claim
from audit.grounding import resolve_period
from audit.tools.compute import compute_yoy
from finance import analyze, select_previous
from interim import disclosed_change, extract_interim
from main import report_markdown
from materials import sha256, validate_pdf
from periods import growth
from qa import answer
from test_upgrade import fact, protocol_item
from webui_pipeline import guess_identity, run_announcement_pipeline, run_pipeline

ROOT = Path(__file__).resolve().parents[1]


class ReadOnlyRun:
    def event(self, *args, **kwargs):
        pass


class YearBindingRepairTests(unittest.TestCase):
    def setUp(self):
        self.current = fact(value="170899152276.34", normalized_value="170899152276.34")
        self.previous = fact(evidence_id="base", period_year=2023, period_start="2023-01-01",
                             period_end="2023-12-31", value="147693604994.14", normalized_value="147693604994.14")

    def test_c2_wrong_amount_reaches_deterministic_verdict(self):
        sentence = "贵州茅台2024年合并口径营业收入为1965.34亿元。"
        row = check_one_claim(protocol_item(quote=sentence, value="1965.34", unit="亿元"),
                              [self.current, self.previous], sentence, sentence, "C2")
        self.assertEqual(row["status"], "确认错误")
        self.assertEqual(row["grounding"]["period"]["year"], 2024)
        self.assertEqual(row["expected"], "1708.99")

    def test_c6_uses_explicit_baseline(self):
        sentence = "贵州茅台2024年合并口径营业收入较2023年上升。"
        item = protocol_item(quote=sentence, claim_type="direction", operator="none", value=None,
                             unit=None, direction="up")
        row = check_one_claim(item, [self.current, self.previous], sentence, sentence, "C6")
        self.assertEqual(row["status"], "证据支持")
        self.assertEqual(row["grounding"]["period"]["baseline_year"], 2023)
        self.assertEqual(set(row["evidence_ids"]), {"e1", "base"})

    def test_partial_quote_does_not_mistake_baseline_for_current_year(self):
        sentence = "贵州茅台2024年合并口径营业收入较2023年上升。"
        item = protocol_item(quote="营业收入较2023年上升", claim_type="direction", operator="none", value=None,
                             unit=None, direction="up")
        row = check_one_claim(item, [self.current, self.previous], sentence, sentence, "C6")
        self.assertEqual(row["status"], "证据支持")

    def test_reverse_comparison_keeps_roles(self):
        for sentence in ("2023年营业收入较2024年下降。", "与2024年相比，2023年营业收入下降。"):
            binding = resolve_period(2023, sentence=sentence)
            self.assertEqual((binding["year"], binding["baseline_year"]), (2023, 2024))
            self.assertFalse(binding["conflict"])

    def test_amounts_and_identifiers_cannot_supply_year(self):
        for sentence in ("贵州茅台营业收入为1965.34亿元。", "贵州茅台营业收入为2024.50亿元。",
                         "编号2024001，贵州茅台营业收入为100元。"):
            self.assertIsNone(resolve_period(2024, sentence=sentence)["located_in"])
        self.assertIsNone(resolve_period(2024, preceding="营业收入为2024.50亿元。")["located_in"])

    def test_explicit_dates_and_year_conflicts(self):
        self.assertEqual(resolve_period(2024, sentence="截至2024-12-31的金额1965.34元。")["year"], 2024)
        self.assertTrue(resolve_period(2023, sentence="2024年金额1965.34亿元。")["conflict"])

    def test_unrelated_multiple_years_remain_ambiguous(self):
        self.assertTrue(resolve_period(2024, sentence="2024年收入和2023年利润均较高。")["ambiguous"])
        self.assertTrue(resolve_period(2024, sentence="2024年收入上升；2023年利润下降。")["ambiguous"])

    def test_explicit_baseline_cannot_be_replaced_by_available_year(self):
        sentence = "贵州茅台2024年营业收入较2022年上升。"
        row = check_one_claim(protocol_item(quote=sentence, claim_type="direction", operator="none",
                                             value=None, unit=None, direction="up"),
                              [self.current, self.previous], sentence, sentence, "C1")
        self.assertEqual(row["reason_code"], "direction_base_missing")

    def test_numeric_comparison_never_substitutes_a_different_baseline(self):
        for year, expected in ((2022, "证据不足"), (2023, "证据支持"), (2024, "证据不足")):
            sentence = f"贵州茅台2024年营业收入较{year}年增长15.71%。"
            row = check_one_claim(protocol_item(quote=sentence, claim_type="yoy", kind="yoy", value="15.71", unit="%"),
                                  [self.current, self.previous], sentence, sentence, "C1")
            self.assertEqual(row["status"], expected)

    def test_same_year_direction_preserves_explicit_baseline(self):
        sentence = "贵州茅台2024年营业收入较2024年上升。"
        row = check_one_claim(protocol_item(quote=sentence, claim_type="direction", operator="none",
                                             value=None, unit=None, direction="up"),
                              [self.current, self.previous], sentence, sentence, "C1")
        self.assertEqual(row["grounding"]["period"]["baseline_year"], 2024)
        self.assertEqual(row["status"], "证据不足")

    def test_cross_company_cannot_reuse_current_year_for_other_side(self):
        sentence = "贵州茅台2024年营业收入较2023年五粮液营业收入更高。"
        other = fact(company_code="000858", company_name="五粮液", evidence_id="other", value="80", normalized_value="80")
        row = check_one_claim(protocol_item(quote=sentence, claim_type="comparison", company_b="五粮液",
                                             comparison_operator="gt", value=None, unit=None),
                              [self.current, other], sentence, sentence, "C1")
        self.assertEqual(row["reason_code"], "unsupported_comparison_period")

    def test_mismatched_half_and_quarter_cannot_pass(self):
        sentence = "贵州茅台2024年上半年营业收入较2023年第一季度上升。"
        h1 = fact(period_kind="half", duration_months=6, period_end="2024-06-30")
        previous = fact(evidence_id="h0", period_year=2023, period_start="2023-01-01",
                        period_end="2023-06-30", period_kind="half", duration_months=6)
        row = check_one_claim(protocol_item(quote=sentence, claim_type="direction", period_kind="half",
                                             operator="none", value=None, unit=None, direction="up"),
                              [h1, previous], sentence, sentence, "C1")
        self.assertEqual(row["reason_code"], "direction_not_comparable")


class RealPeriodRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = []
        for name in ("moutai_2025_half", "moutai_2025_q1"):
            path = ROOT / "data/demo/real" / (name + ".pdf")
            metadata = dict(document_id=name, company_code="600519", company_name="贵州茅台", report_year=2025,
                            local_file=str(path), sha256=sha256(path.read_bytes()), source_url="local", announcement_id=None)
            with pymupdf.open(path) as doc:
                cls.facts.extend(extract_interim(doc, metadata, ReadOnlyRun()))
        cls.analysis = analyze(cls.facts, ReadOnlyRun())

    def row(self, doc, metric):
        return next(r for r in self.analysis["rows"] if r["document_id"] == doc and r["metric"] == metric)

    def test_twenty_disclosures_have_matching_type_unit_and_precision(self):
        self.assertEqual(len(self.facts), 40)
        self.assertEqual(len(self.analysis["rows"]), 20)
        for row in self.analysis["rows"]:
            with self.subTest(doc=row["document_id"], metric=row["metric"]):
                self.assertEqual(row["reported_change_check"]["status"], "match")
                self.assertTrue(row["reported_change"]["bbox"])
                self.assertEqual(row["reported_change"]["comparison_kind"], row["comparison_kind"])
        self.assertEqual(self.row("moutai_2025_half", "revenue")["reported_yoy"]["value"], "9.10")
        self.assertEqual(self.row("moutai_2025_q1", "revenue")["reported_yoy"]["value"], "10.54")

    def test_roe_is_a_percentage_point_difference(self):
        for doc, expected in (("moutai_2025_half", "0.26"), ("moutai_2025_q1", "0.35")):
            row = self.row(doc, "weighted_roe")
            self.assertEqual((row["change"]["value"], row["change"]["unit"]), (expected, "百分点"))
            self.assertEqual(row["value_unit"], "%")
        self.assertEqual(self.row("moutai_2025_half", "basic_eps")["value_unit"], "元/股")

    def test_year_end_uses_same_report_end_balance_and_continued_header(self):
        row = self.row("moutai_2025_q1", "total_assets")
        self.assertEqual(row["previous"], "298944579918.70")
        self.assertEqual(row["comparison_kind"], "year_end")
        self.assertEqual(row["reported_change_check"]["source_page"], 2)
        self.assertEqual(row["reported_change"]["header"]["page"], 1)

    def test_missing_or_conflicting_basis_is_not_relaxed(self):
        current = next(f for f in self.facts if f["document_id"] == "moutai_2025_half" and f["metric"] == "total_assets" and f["period_year"] == 2025)
        previous = select_previous(self.facts, current, "year_end")
        self.assertEqual(growth(current, {**previous, "currency": "USD"}, "year_end")["status"], "not_comparable")
        wrong_date = {**previous, "period_start": "2024-06-30", "period_end": "2024-06-30"}
        self.assertEqual(growth(current, wrong_date, "year_end")["status"], "not_comparable")

    def test_yoy_tool_never_substitutes_year_end_or_percentage_point_operation(self):
        h1 = [f for f in self.facts if f["document_id"] == "moutai_2025_half"]
        stock = compute_yoy(h1, company_name_or_code="600519", metric="total_assets", year=2025)
        self.assertEqual(stock["status"], "missing")
        roe = compute_yoy(h1, company_name_or_code="600519", metric="weighted_roe", year=2025)
        self.assertEqual(roe["yoy"]["unit"], "%")
        self.assertTrue(roe["yoy"]["value"].startswith("1.4747"))

    def test_qa_names_the_actual_comparison_and_unit(self):
        roe = answer("贵州茅台2025年上半年加权平均净资产收益率同比变化多少？", self.facts, use_llm=False)
        self.assertEqual(roe["status"], "ok")
        self.assertIn("0.26百分点", roe["answer"])
        relative = answer("贵州茅台2025年上半年加权平均净资产收益率同比相对增长率是多少？", self.facts, use_llm=False)
        self.assertIn("相对增幅 1.47%", relative["answer"])
        stock = answer("贵州茅台2025年上半年总资产较上年末变化多少？", self.facts, use_llm=False)
        self.assertEqual(stock["status"], "ok")
        self.assertIn("较上年末变化 -2.24%", stock["answer"])
        missing = answer("贵州茅台2025年上半年总资产同比增长多少？", self.facts, use_llm=False)
        self.assertEqual(missing["status"], "insufficient_evidence")

    def test_blocked_q2_keeps_both_sources_without_valid_quarter(self):
        blocked = self.analysis["blocked_derivations"]
        self.assertEqual(len(blocked), 4)
        for row in blocked:
            self.assertEqual(row["status"], "not_comparable")
            self.assertEqual(row["period_start"], "2025-04-01")
            self.assertEqual(len(row["sources"]), 2)
        self.assertFalse(any(r["period_end"] == "2025-06-30" for r in self.analysis["qoq_rows"]))
        self.assertTrue(all("2024-10-01" in r["qoq"]["reason"] for r in self.analysis["qoq_rows"]))
        self.assertTrue(all(r["evidence_ids"] for r in self.analysis["qoq_rows"]))

    def test_signals_and_markdown_preserve_periods_and_refusal_details(self):
        for signal in self.analysis["signals"]:
            self.assertIn(signal["period_end"], {"2025-06-30", "2025-03-31"})
            self.assertTrue(signal["sources"])
        report = report_markdown(self.analysis, [])
        for marker in ("0.26百分点", "较上年末变化", "累计拆季受阻记录", "未获可比性确认", "会计政策版本", "2024-10-01", "来源与待复核事项"):
            self.assertIn(marker, report)

    def test_absent_unparsed_and_negative_disclosures_are_distinct(self):
        header = {"text": "本报告期比上年同期增减(%)"}
        self.assertEqual(disclosed_change("—", header, None, 1)[1], "not_disclosed")
        self.assertEqual(disclosed_change("约增长", header, None, 1)[1], "unresolved")
        change, status = disclosed_change("减少0.26个百分点", header, [0, 0, 1, 1], 1)
        self.assertEqual((change["value"], change["unit"], status), ("-0.26", "百分点", "extracted"))


class AnnouncementRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = (ROOT / "data/demo/real/fosun_2025_pledge.pdf").read_bytes()

    def test_announcement_identity_is_not_a_financial_year(self):
        identity = guess_identity(self.blob)
        self.assertEqual((identity["material_type"], identity["suggested_task"]), ("announcement", "pledge"))
        self.assertNotIn("year", identity)
        self.assertEqual(identity["announcement_date"], "2025-08-15")
        with self.assertRaisesRegex(ValueError, "未核实报告年度"):
            validate_pdf(self.blob, "600196", "复星医药", 2025)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "公告字段"):
                run_pipeline(self.blob, "600196", "复星医药", 2025, root=Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_correct_pledge_row_supplies_purpose_and_total_company_ratio(self):
        events = extract_announcement(self.blob, "pledge")["events"]
        self.assertEqual(len(events), 1)
        fields = events[0]["fields"]
        for key, value in (("shares", "49000000"), ("start_date", "2025-08-14"),
                           ("end_date", "2027-08-18"), ("purpose", "偿还债务"), ("ratio_percent", "1.83")):
            self.assertEqual(fields[key]["normalized_value"], value)
            self.assertEqual(fields[key]["evidence"][0]["page"], 2)
        self.assertIn("注", fields["ratio_percent"]["evidence"][0]["header"]["text"])
        self.assertTrue(fields["ratio_percent"]["evidence"][0]["header"]["bbox"])

    def test_announcement_pipeline_needs_no_report_year(self):
        with tempfile.TemporaryDirectory() as directory:
            out = run_announcement_pipeline(self.blob, "pledge", root=Path(directory), publish_latest=False)
            self.assertTrue(out["ok"])
            self.assertEqual(out["announcement"]["events"][0]["company_code"], "600196")
            self.assertEqual(out["counts"]["required_missing"], 0)
            self.assertEqual(len(out["evidence"]), 7)


if __name__ == "__main__":
    unittest.main()
