"""用户 2026-10-14 问题清单：年度、真实期间披露、单位及可比性边界。"""
from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pymupdf

from audit.grounding import resolve_period
from api.routes import ApiApplication
from finance import analyze
from interim import extract_interim
from llm_check import check_one_claim
from main import report_markdown
from materials import sha256
from test_upgrade import fact, protocol_item


class YearGroundingCases(unittest.TestCase):
    def test_amounts_and_identifiers_are_not_years(self):
        for sentence in ("贵州茅台2024年合并口径营业收入为1965.34亿元。",
                         "贵州茅台2024年收入2019万元，编号2023。",
                         "贵州茅台2024-12-31披露收入2025.00元。"):
            with self.subTest(sentence=sentence):
                bound = resolve_period(2024, sentence=sentence, quote=sentence)
                self.assertEqual(bound["year"], 2024)
                self.assertTrue(bound["located_in"])
                self.assertFalse(bound.get("ambiguous"))

    def test_comparison_years_keep_current_and_base_roles(self):
        for sentence in ("贵州茅台2024年营业收入较2023年上升。",
                         "较2023年，贵州茅台2024年营业收入上升。",
                         "贵州茅台2023年营业收入较2024年下降。"):
            current = 2023 if "2023年营业收入" in sentence else 2024
            with self.subTest(sentence=sentence):
                bound = resolve_period(current, sentence=sentence, quote=sentence)
                self.assertEqual(bound["year"], current)
                self.assertEqual(bound["comparison_year"], 2024 if current == 2023 else 2023)
                self.assertTrue(bound["located_in"])

    def test_unbound_or_conflicting_years_do_not_become_confirmed(self):
        for sentence in ("贵州茅台营业收入为1965.34亿元。", "2023年收入100元，2024年收入120元。",
                         "贵州茅台2024年及2023年收入上升。", "贵州茅台2024年收入与2022年和2023年比较。"):
            with self.subTest(sentence=sentence):
                self.assertFalse(resolve_period(2024, sentence=sentence, quote=sentence).get("located_in"))
        self.assertTrue(resolve_period(2023, sentence="贵州茅台2024年收入100元").get("conflict"))

    def test_c2_confirms_wrong_amount_and_c6_checks_direction(self):
        current = fact(value="170899000000", normalized_value="170899000000")
        previous = fact(evidence_id="prior", period_year=2023, period_start="2023-01-01",
                        period_end="2023-12-31", value="150000000000", normalized_value="150000000000")
        sentence = "贵州茅台2024年合并口径营业收入为1965.34亿元。"
        row = check_one_claim(protocol_item(quote=sentence, value="1965.34", unit="亿元"),
                              [current, previous], sentence, sentence, "C2")
        self.assertEqual(row["status"], "确认错误")
        sentence = "贵州茅台2024年合并口径营业收入较2023年上升。"
        row = check_one_claim(protocol_item(quote=sentence, claim_type="direction", direction="up",
                                           value=None, unit=None, operator="none"),
                              [current, previous], sentence, sentence, "C6")
        self.assertEqual(row["status"], "证据支持")
        # 明示比较的是更早年度时，不能偷换成上年数据。
        other = sentence.replace("2023年", "2022年")
        row = check_one_claim(protocol_item(quote=other, claim_type="direction", direction="up",
                                           value=None, unit=None, operator="none"),
                              [current, previous], other, other, "C6-other")
        self.assertNotEqual(row["status"], "证据支持")


class RealInterimCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.materials = [], []
        root = Path(__file__).resolve().parents[1]
        for name in ("moutai_2025_half.pdf", "moutai_2025_q1.pdf"):
            path = root / "data" / "demo" / "real" / name
            if not path.is_file():
                raise unittest.SkipTest("缺少真实期间样例")
            material = {"document_id": name, "company_code": "600519", "company_name": "贵州茅台",
                        "report_year": 2025, "local_file": str(path.relative_to(root)),
                        "sha256": sha256(path.read_bytes()), "source_url": "fixture://" + name,
                        "announcement_id": name, "disclosure_date_status": "fixture",
                        "version_policy": "fixed_fixture"}
            cls.materials.append(material)
            with pymupdf.open(path) as document:
                cls.facts.extend(extract_interim(document, material, Mock()))
        cls.analysis = analyze(cls.facts, Mock())

    def row(self, metric, end):
        return next(r for r in self.analysis["rows"] if r["metric"] == metric and r["period_end"] == end)

    def test_revenue_disclosure_values_and_positions_are_retained(self):
        for end, value, page in (("2025-06-30", "9.10", 5), ("2025-03-31", "10.54", 1)):
            with self.subTest(end=end):
                row = self.row("revenue", end)
                self.assertEqual(row["reported_yoy"]["value"], value)
                self.assertEqual(row["reported_yoy"]["page"], page)
                self.assertEqual(len(row["reported_yoy"]["bbox"]), 4)
                self.assertEqual(row["reported_yoy_check"]["status"], "match")

    def test_roe_uses_percentage_points_and_eps_keeps_per_share_unit(self):
        for end, difference in (("2025-06-30", "0.26"), ("2025-03-31", "0.35")):
            with self.subTest(end=end):
                row = self.row("weighted_roe", end)
                self.assertEqual(row["change"]["value"], difference)
                self.assertEqual(row["change"]["unit"], "个百分点")
                self.assertEqual(row["reported_yoy_check"]["status"], "match")
                self.assertEqual(row["normalized_unit"], "%")
                self.assertEqual(self.row("basic_eps", end)["normalized_unit"], "元/股")

    def test_balance_changes_use_year_end_without_fabricating_yoy(self):
        for end, value in (("2025-06-30", "-2.24"), ("2025-03-31", "4.49")):
            with self.subTest(end=end):
                row = self.row("total_assets", end)
                self.assertEqual(row["comparison_kind"], "year_end")
                self.assertEqual(row["previous_period_end"], "2024-12-31")
                self.assertEqual(row["reported_change"]["value"], value)
                self.assertEqual(row["reported_change_check"]["status"], "match")
                self.assertIsNone(row["reported_yoy"])
                self.assertNotEqual(row["yoy"]["status"], "ok")

    def test_blocked_second_quarter_and_signal_periods_reach_report(self):
        blocked = self.analysis["blocked_derivations"]
        self.assertEqual(len(blocked), 4)
        self.assertTrue(all(r["period_start"] == "2025-04-01" and r["period_end"] == "2025-06-30" for r in blocked))
        report = report_markdown(self.analysis, self.materials)
        self.assertIn("未获可比性确认", report)
        self.assertIn("2025-04-01", report)
        for signal in self.analysis["signals"]:
            self.assertTrue(signal.get("period_start"))
            self.assertTrue(signal.get("period_end"))
            self.assertEqual(signal["company_name"], "贵州茅台")
            self.assertTrue(signal.get("source_pages"))

    def test_failed_draft_audit_is_partial_and_keeps_financial_results(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            app = ApiApplication(Path(directory))
            try:
                with patch("webui_pipeline._check_draft", return_value={"status":"failed", "reason":"模型认证失败", "checks":[]}):
                    created = app.create_run("a" * 32, "financial", {"code":"600519", "name":"贵州茅台", "year":2025, "draft":"待核查草稿"},
                                             (root / "data/demo/real/moutai_2025_half.pdf").read_bytes())
                    app.manager.wait(created["run_id"], timeout=10)
                run = app.get_run("a" * 32, created["run_id"], include_result=True)
                self.assertEqual(run["status"], "partial_failed")
                self.assertFalse(run["result"]["ok"])
                self.assertTrue(run["result"]["rows"])
                self.assertIn("模型认证失败", run["result"]["report_md"])
                self.assertIn("provenance", run["result"])
                from scripts.check_docs import check_file
                context = app.manager.get_run(created["run_id"], session_id="a" * 32)
                self.assertEqual(check_file(context.output("webui_report.md"), app.root), [])
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
