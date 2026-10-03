"""统一测试入口：python -B test.py；临时数据使用系统临时目录。"""
import io
import copy
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile
from types import SimpleNamespace
from unittest.mock import patch, Mock

sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    raise SystemExit("本项目测试需要 Python 3.11+。")

from materials import ROOT, TZ, Run, load_materials, read_json, register, validate_pdf, write_json
from materials import query_reports, select_report, validate_url
from finance import (analyze, check_claim, compare_amount, compare_number, convert, decimal,
                     evidence_yoy, percentage_points, ratio, select_previous, yoy)
from extract import header_for, metric_for, parse_table
from main import evaluate_gold, extract_selected, report_markdown
from llm_check import (LLMClient, LLMError, check_one_claim, check_payload, check_text,
                       render_report, schema, split_draft, validate_schema)

def evidence(year, value="100", **changes):
    return {
        "evidence_id": str(year), "company_code": "600519", "metric": "revenue",
        "currency": "CNY", "scope": "consolidated", "period_kind": "annual",
        "duration_months": 12, "comparison_group": "same-report-table",
        "period_year": year, "value": value, "unit": "元",
        "adjustment": "as_reported", **changes,
    }


class DecimalRulesTests(unittest.TestCase):
    def test_zero_is_not_missing(self):
        self.assertEqual(decimal("0"), Decimal("0"))
        self.assertIsNone(decimal(None))
        self.assertIsNone(decimal("—"))

    def test_parentheses_negative(self):
        self.assertEqual(decimal("（1,250.50）"), Decimal("-1250.50"))

    def test_float_and_nonfinite_rejected(self):
        for value in [0.1, True, "NaN", "Infinity"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decimal(value)

    def test_unit_conversion_exact(self):
        self.assertEqual(convert("1.23", "亿元", "万元"), Decimal("12300.00"))
        self.assertEqual(convert("1", "万元"), Decimal("10000"))

    def test_unknown_unit_not_assumed(self):
        with self.assertRaises(ValueError):
            convert("10", None)
        self.assertEqual(compare_amount("10", None, "10", "元")["status"], "needs_review")

    def test_positive_yoy(self):
        self.assertEqual(yoy("120", "100")["value"], "20.0")

    def test_current_zero_yoy(self):
        self.assertEqual(Decimal(yoy("0", "100")["value"]), Decimal("-100"))

    def test_missing_yoy(self):
        self.assertEqual(yoy(None, "100")["status"], "missing")

    def test_zero_base(self):
        for current in ["0", "100"]:
            with self.subTest(current=current):
                self.assertEqual(yoy(current, "0")["status"], "zero_base")
                self.assertIsNone(yoy(current, "0")["value"])

    def test_negative_base_default_review(self):
        for current, label in [("50", "扭亏为盈"), ("0", "亏损归零"), ("-50", "减亏"),
                               ("-150", "亏损扩大"), ("-100", "亏损持平")]:
            with self.subTest(current=current):
                answer = yoy(current, "-100")
                self.assertEqual(answer["status"], "negative_base")
                self.assertEqual(answer["change"], label)
                self.assertIsNone(answer["value"])

    def test_absolute_base_requires_explicit_policy(self):
        self.assertEqual(Decimal(yoy("50", "-100", "absolute")["value"]), Decimal("150"))
        with self.assertRaises(ValueError):
            yoy("50", "-100", "guessed")

    def test_rounded_amount_matches(self):
        self.assertEqual(compare_amount("170899152276.34", "元", "1708.99", "亿元")["status"], "match")

    def test_wrong_unit_detected(self):
        self.assertEqual(compare_amount("170899152276.34", "元", "1708.99", "万元")["status"], "mismatch")

    def test_half_rounding_boundaries(self):
        cases = [("1.0049", "1.00", "match"), ("1.005", "1.01", "match"),
                 ("1.005", "1.00", "mismatch"), ("-1.005", "-1.01", "match")]
        for actual, claim, expected in cases:
            with self.subTest(actual=actual, claim=claim):
                self.assertEqual(compare_number(actual, claim)["status"], expected)

    def test_ratio_zero_and_missing(self):
        self.assertEqual(ratio("1", "0")["status"], "zero_base")
        self.assertEqual(ratio(None, "2")["status"], "missing")
        self.assertEqual(ratio("1", "4", True)["value"], "25.00")

    def test_percentage_points_are_difference(self):
        self.assertEqual(Decimal(percentage_points("15", "10")["value"]), Decimal("5"))
        self.assertEqual(Decimal(yoy("15", "10")["value"]), Decimal("50"))

    def test_comparable_evidence_with_unit_conversion(self):
        answer = evidence_yoy(evidence(2024, "120", unit="万元"),
                              evidence(2023, "1000000", unit="元"))
        self.assertEqual(Decimal(answer["value"]), Decimal("20"))

    def test_context_conflicts_are_not_compared(self):
        for field, value in [("company_code", "600887"), ("scope", "parent_company"),
                             ("scope", "unknown"), ("currency", "USD"),
                             ("currency", None), ("period_kind", "quarter"),
                             ("duration_months", 3), ("comparison_group", "another-report"),
                             ("period_year", 2022), ("adjustment", "before")]:
            with self.subTest(field=field):
                previous = evidence(2023)
                previous[field] = value
                self.assertEqual(evidence_yoy(evidence(2024), previous)["status"], "not_comparable")

    def test_restatement_after_is_usable(self):
        self.assertEqual(evidence_yoy(evidence(2024), evidence(2023, adjustment="after"))["status"], "ok")

    def test_missing_prior_not_replaced_with_other_year(self):
        self.assertEqual(evidence_yoy(evidence(2024), None)["status"], "missing")


class OperatorCompareTests(unittest.TestCase):
    """P1：约数/不等式运算符由代码裁决，容差默认 2%、上限 5%。"""

    def test_eq_still_uses_disclosed_precision(self):
        self.assertEqual(compare_number("100.00", "100")["status"], "match")
        # 陈述写成 100.0（一位小数）时，100.4 不得过
        self.assertEqual(compare_number("100.4", "100.0")["status"], "mismatch")

    def test_approx_within_default_tolerance(self):
        # 100 vs 101 = 1% 相对误差 → 默认 2% 内
        self.assertEqual(compare_number("100", "101", operator="approx")["status"], "match")
        self.assertEqual(compare_number("100", "103", operator="approx")["status"], "mismatch")

    def test_approx_custom_tolerance_and_cap(self):
        self.assertEqual(compare_number("100", "103", operator="approx", tolerance_pct=3)["status"], "match")
        capped = compare_number("100", "104", operator="approx", tolerance_pct=20)
        self.assertEqual(capped["status"], "match")  # 上限 5%：104 vs 100 = 4%
        self.assertIn("上限", capped.get("warning") or "")
        self.assertEqual(capped["tolerance_pct"], "5")

    def test_inequalities(self):
        self.assertEqual(compare_number("950", "900", operator="exceed")["status"], "match")
        self.assertEqual(compare_number("850", "900", operator="exceed")["status"], "mismatch")
        self.assertEqual(compare_number("900", "900", operator="at_least")["status"], "match")
        self.assertEqual(compare_number("899", "900", operator="at_least")["status"], "mismatch")
        self.assertEqual(compare_number("899", "900", operator="below")["status"], "match")
        self.assertEqual(compare_number("901", "900", operator="at_most")["status"], "mismatch")

    def test_amount_operator_respects_unit(self):
        # 860 亿 vs 证据 862.28 亿：approx 默认 2% 应通过
        answer = compare_amount("86228000000", "元", "860", "亿元", operator="approx")
        self.assertEqual(answer["status"], "match")
        self.assertEqual(compare_amount("86228000000", "元", "860", "亿元")["status"], "mismatch")

    def test_bad_operator_and_negative_tolerance(self):
        self.assertEqual(compare_number("1", "1", operator="fuzzy")["status"], "needs_review")
        self.assertEqual(compare_number("1", "1", operator="approx", tolerance_pct=-1)["status"], "needs_review")


def announcement(ident, title, day=3):
    return {"announcementId": str(ident), "announcementTitle": title,
            "announcementTime": int(datetime(2025, 4, day, tzinfo=TZ).timestamp() * 1000),
            "secCode": "600519", "adjunctUrl": f"finalpage/2025-04-03/{ident}.PDF"}


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def post(self, path, params):
        page = params["pageNum"]
        self.calls.append(page)
        return self.pages[min(page - 1, len(self.pages) - 1)]


class DownloadRulesTests(unittest.TestCase):
    def test_pagination_reaches_second_page(self):
        a, b = announcement(1, "2024年年度报告"), announcement(2, "2024年年度报告（修订版）")
        client = FakeClient([{"announcements": [a], "totalAnnouncement": 2, "hasMore": True},
                             {"announcements": [b], "totalAnnouncement": 2, "hasMore": False}])
        self.assertEqual(len(query_reports(client, "600519", "org", 2024, date(2026, 9, 28))), 2)
        self.assertEqual(client.calls, [1, 2])

    def test_repeated_page_fails(self):
        client = FakeClient([{"announcements": [announcement(1, "2024年年度报告")],
                              "totalAnnouncement": 3, "hasMore": True}])
        with self.assertRaisesRegex(ValueError, "重复"):
            query_reports(client, "600519", "org", 2024, date(2026, 9, 28))

    def test_premature_empty_page_fails(self):
        client = FakeClient([{"announcements": [], "totalAnnouncement": 2, "hasMore": True}])
        with self.assertRaises(ValueError):
            query_reports(client, "600519", "org", 2024, date(2026, 9, 28))

    def test_inconsistent_pagination_metadata(self):
        client = FakeClient([{"announcements": [], "totalAnnouncement": 2, "hasMore": False}])
        with self.assertRaisesRegex(ValueError, "冲突"):
            query_reports(client, "600519", "org", 2024, date(2026, 9, 28))

    def test_other_company_rejected(self):
        item = announcement(1, "2024年年度报告")
        item["secCode"] = "600887"
        client = FakeClient([{"announcements": [item], "totalAnnouncement": 1}])
        with self.assertRaisesRegex(ValueError, "其他公司"):
            query_reports(client, "600519", "org", 2024, date(2026, 9, 28))

    def test_first_latest_and_correction_notice(self):
        first = announcement(1, "2024年年度报告")
        later = announcement(2, "2024年年度报告（修订版）", 4)
        notice = announcement(3, "关于2024年年度报告的更正公告", 4)
        summary = announcement(4, "2024年年度报告摘要", 5)
        entries = [summary, notice, later, first]
        self.assertEqual(select_report(entries, 2024, "first", date(2026, 9, 28)), (first, True))
        self.assertEqual(select_report(entries, 2024, "latest", date(2026, 9, 28)), (later, True))

    def test_as_of_cutoff(self):
        first = announcement(1, "2024年年度报告")
        later = announcement(2, "2024年年度报告（修订版）", 4)
        self.assertEqual(select_report([later, first], 2024, "latest", date(2025, 4, 3)), (first, False))

    def test_notice_is_not_full_report(self):
        with self.assertRaises(ValueError):
            select_report([announcement(1, "关于2024年年度报告的更正公告")],
                          2024, "latest", date(2026, 9, 28))

    def test_non_allowlisted_url_rejected(self):
        for url in ["http://static.cninfo.com.cn/a.PDF", "https://example.org/a.PDF",
                    "https://static.cninfo.com.cn.example.org/a.PDF"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        validate_url("https://static.cninfo.com.cn/a.PDF")


class GeometryTests(unittest.TestCase):
    def test_year_and_adjustment_use_geometry(self):
        headers = [{"text": "2022年", "bbox": [100, 0, 300, 10]},
                   {"text": "调整前", "bbox": [100, 10, 200, 20]},
                   {"text": "调整后", "bbox": [200, 10, 300, 20]}]
        cell = {"text": "123", "bbox": [200, 20, 300, 40]}
        self.assertEqual(header_for(cell, headers, r"20\d{2}年")["text"], "2022年")
        self.assertEqual(header_for(cell, headers, r"调整前|调整后")["text"], "调整后")

    def test_total_revenue_is_not_revenue(self):
        # 营业总收入是自己的指标，绝不能被当成营业收入（茅台 2024 两者差 32.4 亿）
        self.assertEqual(metric_for("营业总收入"), "total_revenue")
        self.assertNotEqual(metric_for("营业总收入"), "revenue")
        self.assertEqual(metric_for("营业收入（万元）"), "revenue")

    def test_adjusted_comparative_preferred(self):
        current = {"metric": "parent_net_profit", "comparison_group": "A", "period_year": 2023}
        before = {**current, "period_year": 2022, "adjustment": "before", "value": "90"}
        after = {**before, "adjustment": "after", "value": "100"}
        self.assertIs(select_previous([before, after], current), after)
        self.assertIsNone(select_previous([before], current))
        self.assertIsNone(select_previous([after, dict(after)], current))

    def test_missing_current_value_preserved(self):
        boxes = [[(0, 0, 100, 20), (100, 0, 200, 20), (200, 0, 300, 20)],
                 [(0, 20, 100, 40), (100, 20, 200, 40), (200, 20, 300, 40)]]
        table = SimpleNamespace(rows=[SimpleNamespace(cells=row) for row in boxes],
                                bbox=(0, 0, 300, 40),
                                extract=lambda: [["主要会计数据", "2024年", "2023年"],
                                                 ["营业收入（元）", "—", "100.00"]])
        page = SimpleNamespace(number=0, rect=SimpleNamespace(x1=400),
                               get_text=lambda **kwargs: "单位：元 币种：人民币",
                               get_label=lambda: "1")
        material = {"document_id": "test", "company_code": "123456", "company_name": "测试",
                    "report_year": 2024, "local_file": "data/test.pdf",
                    "sha256": "test-hash", "announcement_id": "1", "source_url": "test"}
        facts = parse_table(page, table, 0, material, None)
        current = next(f for f in facts if f["period_year"] == 2024)
        self.assertIsNone(current["value"])
        self.assertIn("value_missing", current["issues"])

    def test_statement_title_normalized(self):
        from extract import _statement_title
        self.assertEqual(_statement_title("2024 年度合并及公司利润表"), "合并利润表")
        self.assertEqual(_statement_title("2024 年度合并及公司利润表(续)"), "合并利润表")
        self.assertEqual(_statement_title("合并利润表"), "合并利润表")
        self.assertEqual(_statement_title("一、合并现金流量表"), "合并现金流量表")
        self.assertIsNone(_statement_title("在合并利润表中单列项目反映"))

    def test_dedupe_backfills_unit_and_recomputes_normalized(self):
        from extract import _dedupe
        grid = {"metric": "revenue", "period_year": 2024, "adjustment": "as_reported",
                "extraction_method": "grid_cells_and_geometric_headers",
                "value": "407149600", "unit": None, "normalized_value": "407149600",
                "issues": ["unit_unknown"], "reported_yoy": None, "adjustment_header": None}
        words = {**grid, "extraction_method": "words_geometry", "unit": "千元",
                 "normalized_value": "407149600000", "issues": []}
        out = _dedupe([grid, words])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["unit"], "千元")
        self.assertEqual(out[0]["normalized_value"], "407149600000")
        self.assertNotIn("unit_unknown", out[0]["issues"])


class MaterialTests(unittest.TestCase):
    def test_error_page_rejected(self):
        with self.assertRaises(ValueError):
            validate_pdf(b"<html>Access denied</html>")

    @classmethod
    def setUpClass(cls):
        materials = load_materials(ROOT)
        if not materials:
            raise unittest.SkipTest("需要已登记的真实PDF")
        cls.material = next(m for m in materials if m["company_code"] == "600519" and m["report_year"] == 2024)
        cls.blob = (ROOT / cls.material["local_file"]).read_bytes()

    def test_identity_mismatch(self):
        with self.assertRaisesRegex(ValueError, "公司身份"):
            validate_pdf(self.blob, "123456", "不存在的公司", 2024)
        with self.assertRaisesRegex(ValueError, "报告年度"):
            validate_pdf(self.blob, "600519", "贵州茅台", 2099)

    def test_duplicate_version_and_tamper(self):
        with tempfile.TemporaryDirectory(prefix="materials_test_") as directory:
            root = Path(directory).resolve()
            self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            run = Run(root, "material-test", {})
            first = register(root, self.blob, self.material, run)
            second = register(root, self.blob, self.material, run)
            self.assertEqual(first["local_file"], second["local_file"])
            self.assertEqual(first["first_ingested_at"], second["first_ingested_at"])
            another = register(root, self.blob, {**self.material, "announcement_id": "99999999"}, run)
            self.assertNotEqual(first["local_file"], another["local_file"])
            self.assertEqual(len(load_materials(root)), 2)
            path = root / first["local_file"]
            path.write_bytes(b"tampered fixture")
            with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                register(root, self.blob, self.material, run)
            self.assertEqual(path.read_bytes(), b"tampered fixture")

    def test_verified_metadata_not_downgraded_by_legacy_import(self):
        with tempfile.TemporaryDirectory(prefix="ledger_test_") as directory:
            root = Path(directory).resolve()
            self.assertTrue(root.is_relative_to(Path(tempfile.gettempdir()).resolve()))
            (root / "data" / "agent").mkdir(parents=True)
            verified = {**self.material, "disclosure_date_status": "api_timestamp_Asia_Shanghai"}
            older = {**self.material, "disclosure_date_status": "legacy_unverified"}
            ledger = root / "data" / "agent" / "materials.jsonl"
            ledger.write_text(json.dumps(verified) + "\n" + json.dumps(older) + "\n", encoding="utf-8")
            self.assertEqual(load_materials(root)[0]["disclosure_date_status"], "api_timestamp_Asia_Shanghai")


class RealReportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not load_materials(ROOT):
            raise unittest.SkipTest("先运行 import-existing")
        cls.temp = tempfile.TemporaryDirectory(prefix="integration_")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.scratch = Path(cls.temp.name).resolve()
        assert cls.scratch.is_relative_to(Path(tempfile.gettempdir()).resolve())
        cls.audit_run = Run(cls.scratch, "integration-test", {})
        # 固定材料集 = 茅台/五粮液/伊利 × 2023–2025（与 124 条证据、36 项同比的验收口径一致）
        cls.materials, cls.facts, cls.failures = extract_selected(
            ROOT, cls.audit_run,
            code={"600519", "000858", "600887"}, year={2023, 2024, 2025})
        cls.analysis = analyze(cls.facts, cls.audit_run)

    def test_all_nine_reports_have_current_four_metrics(self):
        self.assertEqual(len(self.materials), 9)
        self.assertEqual(self.failures, [])
        # 四个核心指标在 9 份年报本年列都要齐，且金额单位为元
        core = ("revenue", "parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow")
        current = [f for f in self.facts
                   if f["period_year"] == f["report_year"] and f["adjustment"] in {"as_reported", "after"}]
        for mat in self.materials:
            for metric in core:
                hit = [f for f in current
                       if f["metric"] == metric and f["company_code"] == mat["company_code"]
                       and f["report_year"] == mat["report_year"]]
                self.assertTrue(hit, f"{mat['company_code']} {mat['report_year']} 缺 {metric}")
                self.assertTrue(all(f["value"] is not None and f["unit"] == "元" for f in hit))

    def test_fixed_visual_reference(self):
        evaluation = evaluate_gold(self.facts, read_json(ROOT / "data/agent/samples.json")["reference_gold"])
        self.assertEqual(evaluation["checked"], 24)
        failures = [r for r in evaluation["results"] if r["status"] == "FAIL"]
        self.assertEqual(failures, [])

    def test_all_disclosed_yoy_match_precision(self):
        """凡年报披露了同比的行，按披露精度算完必须一致；核心 4 指标 × 9 份至少 36 行。"""
        rows = self.analysis["rows"]
        checked = [r for r in rows if r["reported_yoy_check"] is not None]
        self.assertGreaterEqual(len(checked), 36)
        for row in rows:
            with self.subTest(company=row["company_code"], year=row["report_year"], metric=row["metric"]):
                if row["reported_yoy_check"] is not None:
                    self.assertEqual(row["yoy"]["status"], "ok")
                    self.assertEqual(row["reported_yoy_check"]["status"], "match")

    def test_evidence_coordinates_and_source_references(self):
        self.assertEqual(len({f["evidence_id"] for f in self.facts}), len(self.facts))
        for fact in self.facts:
            with self.subTest(evidence=fact["evidence_id"]):
                x0, y0, x1, y1 = fact["value_bbox"]
                self.assertLess(x0, x1)
                self.assertLess(y0, y1)
                self.assertEqual(len(fact["source_sha256"]), 64)
                self.assertTrue(fact["year_header"]["text"].startswith(str(fact["period_year"])))

    def test_eight_structured_claims(self):
        claims = read_json(ROOT / "data/agent/samples.json")["claims"]
        expected = read_json(ROOT / "data/agent/samples.json")["expected_results"]
        actual = {claim["id"]: check_claim(claim, self.facts)["status"] for claim in claims}
        self.assertEqual(actual, expected)

    def test_report_exposes_all_disclosure_checks(self):
        report = report_markdown(self.analysis, self.materials)
        matched = sum(1 for r in self.analysis["rows"]
                      if r["reported_yoy_check"] and r["reported_yoy_check"]["status"] == "match")
        self.assertEqual(report.count("| 一致 |"), matched)
        self.assertGreaterEqual(matched, 36)
        self.assertIn("年报披露同比", report)
        self.assertIn("尚待团队人工复签", report)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.row = {"company_name": "测试公司", "report_year": 2024, "metric_name": "营业收入",
                    "current": "115.234567", "previous": "100", "previous_adjustment": "as_reported",
                    "yoy": {"status": "ok", "value": "15.234567"}, "page": 5,
                    "reported_yoy": {"value": "15.23"}, "reported_yoy_check": {
                        "status": "match", "calculated_rounded": "15.23", "reported": "15.23"}}

    def report(self):
        return report_markdown({"basis": "测试", "limits": "测试", "signals": [], "rows": [self.row]}, [])

    def test_comparison_uses_disclosed_precision(self):
        report = self.report()
        self.assertIn("| 15.23% | 15.23% | 一致 |", report)
        self.assertNotIn("15.2346%", report)

    def test_disagreement_visible_in_markdown(self):
        self.row["reported_yoy_check"] = {
            "status": "mismatch", "calculated_rounded": "15.23", "reported": "20.00"}
        self.assertIn("| 15.23% | 20.00% | 不一致，需复核 |", self.report())

    def test_missing_and_uncomputable_are_not_passes(self):
        self.row.update(reported_yoy=None, reported_yoy_check=None)
        self.assertIn("未核对（缺少披露值）", self.report())
        self.assertNotIn("| 一致 |", self.report())
        self.row.update(yoy={"status": "zero_base", "value": None}, reported_yoy={"value": "20.00"})
        self.assertIn("| 20.00% | 未核对（zero_base） |", self.report())
        self.assertNotIn("| 一致 |", self.report())


class LLMFlowTests(unittest.TestCase):
    """协议、接线和失败分支测试；不使用真实模型，不构成模型测评集。"""

    def setUp(self):
        self.sentence = "2024年，贵州茅台营业收入为0.80亿元。"
        self.item = {
            "claim_id": "C1", "sentence_id": 1, "quote": self.sentence, "context_quote": None,
            "claim_type": "amount", "company_name": "贵州茅台", "period_year": 2024,
            "period_kind": "annual", "metric_text": "营业收入", "metric": "revenue",
            "scope": "consolidated", "kind": "amount",
            "value": "0.80", "unit": "亿元", "operator": "eq", "tolerance_pct": None,
            "direction": "unknown", "currency": "CNY", "qualifier": "exact",
            "plain_claim": "2024年营收0.80亿元", "is_forecast": False,
            "ambiguity": "none", "verification_action": "compare_amount",
        }
        self.fact = {**evidence(2024, "80000000"), "report_year": 2024, "metric_name": "营业收入",
                     "normalized_value": "80000000", "issues": [], "source_file": "data/test.pdf",
                     "source_sha256": "a" * 64, "page": 5, "value_bbox": [0, 0, 1, 1]}

    def check(self, item):
        return check_one_claim(item, [self.fact], item["quote"], item["quote"], "C1")

    def test_schema_refuses_extra_fields_and_float_values(self):
        payload = {"items": [self.item], "unclaimed_sentences": []}
        validate_schema(payload, schema())
        for changes in ({"value": 0.8}, {"suggestion": "模型编造的修改建议"}, {"period_year": True}):
            invalid = copy.deepcopy(payload)
            invalid["items"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(LLMError):
                validate_schema(invalid, schema())

    def test_company_and_metric_failures_are_explicit(self):
        wrong_company = {**self.item, "company_name": "贵洲茅台", "quote": self.sentence.replace("贵州", "贵洲")}
        self.assertEqual(self.check(wrong_company)["reason_code"], "unresolved_company")
        unsupported = {**self.item, "metric_text": "毛利率", "metric": "unsupported",
                       "quote": "2024年，贵州茅台毛利率为40.00%。", "value": "40.00", "unit": "%"}
        result = self.check(unsupported)
        self.assertEqual((result["status"], result["reason_code"]), ("证据不足", "unsupported_metric"))

    def test_company_from_draft_intro_is_resolved(self):
        # 公司名只写在草稿开头、本句引文没带 —— 不再吞成 unresolved_company
        bare = "2024年营业收入为0.80亿元。"
        item = {**self.item, "quote": bare, "company_name": "贵州茅台"}
        draft = "贵州茅台2024年报显示如下。\n" + bare
        result = check_one_claim(item, [self.fact], bare, draft, "C1")
        self.assertNotEqual(result["reason_code"], "unresolved_company")
        self.assertEqual(result["normalized_claim"]["company_code"], "600519")

    def test_company_absent_from_draft_still_stops(self):
        bare = "2024年营业收入为0.80亿元。"
        item = {**self.item, "quote": bare, "company_name": "贵州茅台"}
        other_draft = "五粮液2024年报显示如下。" + bare
        result = check_one_claim(item, [self.fact], bare, other_draft, "C1")
        self.assertEqual(result["reason_code"], "unresolved_company")

    def test_whitelist_covers_scope_companies(self):
        from llm_check import load_companies
        companies = load_companies()
        for code, name in (("600519", "贵州茅台"), ("000333", "美的集团"), ("002415", "海康威视")):
            self.assertIn(name, companies[code])

    def test_fuzzy_and_unspecified_profit_require_review(self):
        fuzzy = {**self.item, "quote": self.sentence.replace("为", "约为")}
        self.assertEqual(self.check(fuzzy)["reason_code"], "non_exact_claim")
        vague = {**self.item, "metric_text": "净利润", "metric": "parent_net_profit",
                 "quote": self.sentence.replace("营业收入", "净利润")}
        self.assertEqual(self.check(vague)["reason_code"], "ambiguous_profit_scope")

    def test_fabricated_numeric_value_is_not_used(self):
        result = self.check({**self.item, "value": "999.99"})
        self.assertEqual(result["reason_code"], "ungrounded_value")
        self.assertEqual(result["evidence_ids"], [])

    def test_model_cannot_drop_unit_multipliers(self):
        result = self.check({**self.item, "unit": "元"})
        self.assertEqual(result["reason_code"], "ungrounded_unit")
        self.assertIsNone(result["calculation"])

    def test_explicit_scope_and_forecasts_are_not_overridden(self):
        parent = {**self.item, "quote": self.sentence.replace("营业收入", "母公司营业收入")}
        self.assertEqual(self.check(parent)["reason_code"], "model_parse_conflict")
        forecast = {**self.item, "quote": self.sentence.replace("为", "预计为"),
                    "claim_type": "forecast", "is_forecast": True}
        answer = self.check(forecast)
        self.assertEqual(answer["reason_code"], "forecast_marked_only")
        self.assertEqual((answer["status"], answer["track"]), ("模型判断", "model"))
        self.assertNotEqual(answer["status"], "确认错误")

    def test_approx_operator_is_judged_not_rejected(self):
        # 「约为 0.82 亿元」+ operator=approx：0.80 vs 0.82 = 2.5% 相对误差，默认容差 2% 内
        fuzzy = {**self.item, "quote": self.sentence.replace("为", "约为").replace("0.80", "0.81"),
                 "value": "0.81", "operator": "approx", "tolerance_pct": 2.0,
                 "qualifier": "approximate", "claim_type": "amount"}
        answer = self.check(fuzzy)
        self.assertEqual(answer["status"], "证据支持")
        self.assertEqual(answer["track"], "deterministic")
        too_far = {**fuzzy, "quote": fuzzy["quote"].replace("0.81", "0.90"), "value": "0.90"}
        self.assertEqual(self.check(too_far)["status"], "确认错误")

    def test_fuzzy_without_operator_still_requires_review(self):
        fuzzy = {**self.item, "quote": self.sentence.replace("为", "约为"),
                 "qualifier": "approximate", "operator": "eq"}
        self.assertEqual(self.check(fuzzy)["reason_code"], "non_exact_claim")

    def test_model_track_never_claims_confirmed_error(self):
        forecast = {**self.item, "claim_type": "forecast", "is_forecast": True,
                    "quote": "2024年，贵州茅台营业收入预计达到0.80亿元。"}
        answer = self.check(forecast)
        self.assertEqual(answer["track"], "model")
        self.assertNotIn(answer["status"], {"确认错误", "证据支持"})

    def test_dual_track_report_sections(self):
        ok = self.check(self.item)
        forecast = {**self.item, "claim_type": "forecast", "is_forecast": True,
                    "quote": "2024年，贵州茅台营业收入预计达到0.80亿元。"}
        marked = self.check(forecast)
        report = render_report([ok, marked], model="unit-test", run_id="R1")
        self.assertIn("## A. 确定结论", report)
        self.assertIn("## B. 模型判断", report)
        self.assertIn("## C. 需人工", report)
        self.assertEqual(marked["reason_code"], "forecast_marked_only")

    def test_program_handles_decline_and_expected_value(self):
        previous = {**self.fact, "period_year": 2023, "value": "100000000", "evidence_id": "2023"}
        item = {**self.item, "quote": "2024年，贵州茅台营业收入同比下降20.00%。",
                "kind": "yoy", "value": "20.00", "unit": "%"}
        answer = check_one_claim(item, [self.fact, previous], item["quote"], item["quote"], "C1")
        self.assertEqual(answer["status"], "证据支持")
        self.assertEqual(answer["normalized_claim"]["value"], "-20.00")
        wrong = {**self.item, "value": "1.50", "quote": self.sentence.replace("0.80", "1.50")}
        answer = self.check(wrong)
        self.assertEqual((answer["status"], answer["expected"]), ("确认错误", "0.80"))
        self.assertIn("0.80亿元", answer["suggestion"])

    def test_missing_sentence_is_not_silently_accepted(self):
        draft = self.sentence + "另一句没有拆出核查项。"
        sentences = split_draft(draft)
        with self.assertRaises(LLMError):
            check_payload({"items": [self.item], "unclaimed_sentences": []}, sentences, draft, [self.fact])
        result = check_payload({"items": [self.item], "unclaimed_sentences": [2]}, sentences, draft, [self.fact])
        self.assertEqual(result[-1]["reason_code"], "no_claim_extracted")

    def test_transport_to_report_without_storing_credentials(self):
        token = "unit-test-credential"
        payload = {"items": [self.item], "unclaimed_sentences": []}
        reply = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        response = io.BytesIO(json.dumps(reply).encode())
        opener = SimpleNamespace(open=Mock(return_value=response))
        with tempfile.TemporaryDirectory(prefix="finline_llm_") as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text(self.sentence, encoding="utf-8")
            audit = Run(root, "check-text-test", {})
            client = LLMClient("https://example.invalid/v1", "test-model", token)
            with patch("llm_check.urllib.request.build_opener", return_value=opener):
                bundle = check_text(path, [self.fact], audit, client)
            self.assertEqual(bundle["checks"][0]["status"], "证据支持")
            self.assertIn("PDF第5页", (root / "results/text_report.md").read_text(encoding="utf-8"))
            self.assertTrue(all(p.is_file() for p in (root / "results").iterdir()))
            self.assertTrue(all(token.encode() not in p.read_bytes() for p in (root / "results").iterdir()))
            request = opener.open.call_args.args[0]
            sent = json.loads(request.data)
            self.assertEqual(sent["response_format"]["json_schema"]["strict"], True)
            self.assertEqual(request.get_header("Authorization"), "Bearer " + token)
            self.assertNotIn(token, json.dumps(sent))

    def test_http_error_never_echoes_authentication_body(self):
        token = "unit-test-credential"
        failure = urllib.error.HTTPError("https://example.invalid/v1", 401, "secret=" + token, {}, io.BytesIO(token.encode()))
        opener = SimpleNamespace(open=Mock(side_effect=failure))
        client = LLMClient("https://example.invalid/v1", "test-model", token)
        with patch("llm_check.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(LLMError) as raised:
                client.extract(split_draft(self.sentence), SimpleNamespace(event=lambda *a, **k: None))
        self.assertIn("401", str(raised.exception))
        self.assertNotIn(token, str(raised.exception))

    def test_json_mode_does_not_parse_markdown_fences(self):
        reply = {"choices": [{"finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}]}
        opener = SimpleNamespace(open=Mock(return_value=io.BytesIO(json.dumps(reply).encode())))
        client = LLMClient("https://example.invalid/v1", "test-model", "unit-test-credential", "json_object")
        with patch("llm_check.urllib.request.build_opener", return_value=opener), self.assertRaises(LLMError):
            client.extract(split_draft(self.sentence), SimpleNamespace(event=lambda *a, **k: None))


class FlatOutputTests(unittest.TestCase):
    def test_history_keeps_recent_complete_runs(self):
        with tempfile.TemporaryDirectory(prefix="finline_rotation_") as directory, patch("materials.HISTORY_RUNS", 2):
            runs = []
            for number in range(4):
                run = Run(Path(directory), "history-test", {"number": number})
                # 故意令字典序与实际运行顺序相反，覆盖同一秒多次运行。
                run.id = f"20260929_120000_{9 - number:08d}"
                run.output("answer.txt").write_text(str(number), encoding="utf-8")
                run.finish(status="ok")
                runs.append(run)
            with ZipFile(runs[-1].folder / "history.zip") as archive:
                self.assertEqual({name.split("__")[0] for name in archive.namelist()}, {r.id for r in runs[-2:]})
                for number, run in enumerate(runs[-2:], 2):
                    self.assertEqual(archive.read(run.id + "__answer.txt"), str(number).encode())
                    self.assertIn(run.id + "__summary.json", archive.namelist())
                    self.assertIn(run.id + "__source__finance.py", archive.namelist())

    def test_oversized_latest_is_complete_and_older_history_removed(self):
        with tempfile.TemporaryDirectory(prefix="finline_size_") as directory, patch("materials.HISTORY_BYTES", 1):
            first = Run(Path(directory), "first", {})
            first.finish(status="ok")
            latest = Run(Path(directory), "latest", {})
            latest.output("answer.txt").write_text("keep complete", encoding="utf-8")
            latest.finish(status="ok")
            with ZipFile(latest.folder / "history.zip") as archive:
                self.assertEqual({name.split("__")[0] for name in archive.namelist()}, {latest.id})
                self.assertEqual(archive.read(latest.id + "__answer.txt"), b"keep complete")
                self.assertIn(latest.id + "__source__main.py", archive.namelist())

    def test_archive_write_failure_preserves_previous_zip(self):
        with tempfile.TemporaryDirectory(prefix="finline_atomic_") as directory:
            first = Run(Path(directory), "first", {})
            first.finish(status="ok")
            history = first.folder / "history.zip"
            before = history.read_bytes()
            second = Run(Path(directory), "second", {})
            with patch("materials.ZipFile.writestr", side_effect=OSError("simulated disk failure")):
                with self.assertRaises(OSError):
                    second.finish(status="ok")
            self.assertEqual(history.read_bytes(), before)
            self.assertFalse((first.folder / "history.tmp").exists())

    def test_history_preserves_outputs_without_nested_folders(self):
        with tempfile.TemporaryDirectory(prefix="finline_history_") as directory:
            root = Path(directory)
            first = Run(root, "first", {})
            first.output("answer.txt").write_text("first result", encoding="utf-8")
            first.finish(status="ok")
            second = Run(root, "second", {})
            second.output("answer.txt").write_text("second result", encoding="utf-8")
            second.finish(status="ok")
            self.assertEqual([p.name for p in root.iterdir() if p.is_dir()], ["results"])
            self.assertTrue(all(p.is_file() for p in second.folder.iterdir()))
            with ZipFile(second.folder / "history.zip") as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read(first.id + "__answer.txt"), b"first result")
                self.assertEqual(archive.read(second.id + "__answer.txt"), b"second result")
                self.assertIn(first.id + "__source__finance.py", archive.namelist())
            entries = [json.loads(line) for line in
                       (second.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual({row["run_id"] for row in entries}, {second.id})
            with ZipFile(second.folder / "history.zip") as archive:
                old_events = archive.read(first.id + "__events.jsonl").decode("utf-8")
                self.assertTrue(all(json.loads(line)["run_id"] == first.id for line in old_events.splitlines()))

    def test_nested_output_paths_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="finline_paths_") as directory:
            run = Run(Path(directory), "paths", {})
            for name in ("../escape.txt", "pages/image.png", "pages\\image.png", "C:escape", ".."):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    run.output(name)


if __name__ == "__main__":
    class TestLog(io.StringIO):
        def write(self, value):
            sys.stdout.write(value)
            sys.stdout.flush()
            return super().write(value)

    log = TestLog()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    audit = Run(ROOT, "test", {})
    audit.output("test_results.txt").write_text(log.getvalue(), encoding="utf-8")
    summary = {"tests": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skipped": len(result.skipped)}
    write_json(audit.output("test_summary.json"), summary)
    audit.finish(status="ok" if result.wasSuccessful() else "failed", **summary)
    raise SystemExit(0 if result.wasSuccessful() else 1)
