import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.deepseek_agent import (
    _business_adjacent_annual_periods,
    _business_revenue_metric_family,
    _normalize_business_analysis,
    analyze_report,
)


class ModuleOneV2Tests(unittest.TestCase):
    def setUp(self):
        self.pages = {
            1: "公司主营业务为产品生产和销售。营业收入合计 100 万元，上期 80 万元。"
            "主营业务收入合计 80 万元，上期 60 万元。",
            2: "产品甲收入 40 万元，上期 30 万元。地区甲营业收入 50 万元，上期 40 万元。"
            + " ".join(f"分项{i}收入 1 万元，上期 1 万元。" for i in range(20))
            + " 母公司分部收入 40 万元。",
        }

    def make_payload(self, *, segment_scope="合并报表", comparable=True):
        segments = [
            {
                "name": "产品甲",
                "basis": "产品",
                "metric_name": "主营业务收入（分产品）",
                "current_value": "40",
                "current_unit": "万元",
                "current_period": "2024年度",
                "previous_value": "30",
                "previous_unit": "万元",
                "previous_period": "2023年度",
                "reporting_scope": segment_scope,
                "currency": "人民币",
                "reported_yoy": "同比增长 33.3%",
                "cross_period_comparable": comparable,
                "source_pages": [2],
                "evidence_quote": "产品甲收入 40 万元，上期 30 万元。",
            },
            {
                "name": "地区甲",
                "basis": "地区",
                "metric_name": "营业收入（分地区）",
                "current_value": "50",
                "current_unit": "万元",
                "current_period": "2024年度",
                "previous_value": "40",
                "previous_unit": "万元",
                "previous_period": "2023年度",
                "reporting_scope": "合并报表",
                "currency": "人民币",
                "source_pages": [2],
                "evidence_quote": "地区甲营业收入 50 万元，上期 40 万元。",
            },
        ]
        for index in range(20):
            segments.append(
                {
                    "name": f"分项{index}",
                    "basis": "产品",
                    "metric_name": "主营业务收入（分产品）",
                    "current_value": "1",
                    "current_unit": "万元",
                    "current_period": "2024年度",
                    "previous_value": "1",
                    "previous_unit": "万元",
                    "previous_period": "2023年度",
                    "reporting_scope": "合并报表",
                    "currency": "人民币",
                    "source_pages": [2],
                    "evidence_quote": f"分项{index}收入 1 万元，上期 1 万元。",
                }
            )
        return {
            "company": "测试公司",
            "period": "2024年度",
            "reporting_scope": "合并报表",
            "currency": "人民币",
            "business_summary": "公司主营业务为产品生产和销售。",
            "business_summary_source_pages": [1],
            "business_summary_evidence_quote": "公司主营业务为产品生产和销售。",
            "revenue_total": {
                "metric_name": "营业收入",
                "current_value": "100",
                "current_unit": "万元",
                "current_period": "2024年度",
                "previous_value": "80",
                "previous_unit": "万元",
                "previous_period": "2023年度",
                "reporting_scope": "合并报表",
                "currency": "人民币",
                "source_pages": [1],
                "evidence_quote": "营业收入合计 100 万元，上期 80 万元。",
            },
            "revenue_totals": [
                {
                    "metric_name": "主营业务收入",
                    "current_value": "80",
                    "current_unit": "万元",
                    "current_period": "2024年度",
                    "previous_value": "60",
                    "previous_unit": "万元",
                    "previous_period": "2023年度",
                    "reporting_scope": "合并报表",
                    "currency": "人民币",
                    "source_pages": [1],
                    "evidence_quote": "主营业务收入合计 80 万元，上期 60 万元。",
                }
            ],
            "revenue_segments": segments,
            "uncertainties": [],
        }

    def normalize(self, payload, retrieval=None):
        return _normalize_business_analysis(
            json.dumps(payload, ensure_ascii=False),
            {1, 2},
            lambda page: self.pages.get(page),
            [1, 2],
            retrieval,
        )

    def test_income_metrics_keep_distinct_denominators_across_pages(self):
        result = self.normalize(self.make_payload())
        by_name = {item["name"]: item for item in result["revenue_segments"]}

        self.assertEqual(by_name["产品甲"]["current_share_percent"], "50")
        self.assertEqual(by_name["产品甲"]["previous_share_percent"], "50")
        self.assertEqual(by_name["地区甲"]["current_share_percent"], "50")
        self.assertEqual(by_name["地区甲"]["previous_share_percent"], "50")
        self.assertEqual(
            by_name["产品甲"]["current_share_denominator"]["metric_name"],
            "主营业务收入",
        )
        self.assertNotEqual(
            by_name["产品甲"]["current_share_denominator"]["metric_name"],
            result["revenue_total"]["metric_name"],
        )
        self.assertTrue(by_name["产品甲"]["quote_verified"])

    def test_scope_mismatch_blocks_share(self):
        result = self.normalize(self.make_payload(segment_scope="母公司"))
        product = next(item for item in result["revenue_segments"] if item["name"] == "产品甲")
        self.assertIsNone(product["current_share_percent"])
        self.assertIn("口径不一致", product["current_share_note"])

    def test_conflicting_same_metric_totals_block_share(self):
        self.pages[1] += " 另一张表主营业务收入合计 90 万元。"
        payload = self.make_payload()
        payload["revenue_totals"].append(
            {
                "metric_name": "主营业务收入",
                "current_value": "90",
                "current_unit": "万元",
                "current_period": "2024年度",
                "previous_value": "60",
                "previous_unit": "万元",
                "previous_period": "2023年度",
                "reporting_scope": "合并报表",
                "currency": "人民币",
                "source_pages": [1],
                "evidence_quote": "另一张表主营业务收入合计 90 万元。",
            }
        )
        result = self.normalize(payload)
        product = next(item for item in result["revenue_segments"] if item["name"] == "产品甲")
        self.assertIsNone(product["current_share_percent"])
        self.assertIn("口径存在冲突", product["current_share_note"])

    def test_more_than_twelve_segments_are_preserved(self):
        result = self.normalize(self.make_payload())
        self.assertEqual(len(result["revenue_segments"]), 22)

    def test_year_over_year_requires_adjacent_annual_periods(self):
        self.assertTrue(_business_adjacent_annual_periods("2024年度", "2023年度"))
        self.assertFalse(_business_adjacent_annual_periods("2024年度", "2022年度"))
        self.assertFalse(_business_adjacent_annual_periods("2024年1-3月", "2023年1-3月"))

    def test_revenue_metric_families_do_not_collapse_distinct_totals(self):
        self.assertEqual(_business_revenue_metric_family("主营业务收入（分产品）"), "主营业务收入")
        self.assertEqual(_business_revenue_metric_family("营业收入（分地区）"), "营业收入")
        self.assertNotEqual(
            _business_revenue_metric_family("主营业务收入"),
            _business_revenue_metric_family("营业收入"),
        )

    def test_business_workflow_calls_fact_and_analysis_stages_separately(self):
        extraction = json.dumps(self.make_payload(), ensure_ascii=False)
        interpretation = json.dumps(
            {
                "growth_drivers": [],
                "industry_context_analysis": [],
                "strategy_analysis": [],
                "follow_up_checks": [
                    {
                        "question": "主营业务收入增长是否同步带来利润增长？",
                        "reason": "主营业务收入分部增长，需要继续核对利润表现。",
                        "next_module": "盈利来源与变化",
                        "source_pages": [1],
                        "evidence_quote": "主营业务收入合计 80 万元，上期 60 万元。",
                    }
                ],
                "summary": "公司披露主营业务和营业收入两个口径，分项应按各自合计核对。",
                "summary_source_pages": [1],
                "summary_evidence_quote": "主营业务收入合计 80 万元，上期 60 万元。",
                "uncertainties": [],
            },
            ensure_ascii=False,
        )
        fake_response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
            choices=[SimpleNamespace(message=SimpleNamespace(content=interpretation))],
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: fake_response)
            )
        )
        stage_names = []

        def fake_tool_loop(**kwargs):
            self.assertIn("事实提取阶段", kwargs["messages"][0]["content"])
            return extraction, [{"tool": "extract", "pages": [1]}], {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "_tool_pages": [2],
            }

        with patch("backend.deepseek_agent._run_tool_loop", side_effect=fake_tool_loop), patch(
            "backend.deepseek_agent._client", return_value=fake_client
        ):
            result = analyze_report(
                file_name="test.pdf",
                page_count=2,
                initial_pages=[{"page": 1, "text": self.pages[1]}],
                get_page=lambda page: self.pages.get(page),
                search_pages=lambda query: [{"page": 2, "score": 5, "text": self.pages[2]}],
                on_stage=lambda name, status, detail: stage_names.append((name, status)),
                analysis_module="business",
            )

        self.assertIn("DeepSeek提取年报事实", [name for name, _ in stage_names])
        self.assertIn("DeepSeek分析经营变化和交接问题", [name for name, _ in stage_names])
        self.assertEqual(result["result"]["follow_up_checks"][0]["next_module"], "盈利来源与变化")
        self.assertEqual(result["result"]["usage"]["prompt_tokens"], 120)
        self.assertIn(2, result["read_pages"])
        self.assertTrue(any(item["tool"] == "module1_interpretation_stage" for item in result["trace"]))


if __name__ == "__main__":
    unittest.main()
