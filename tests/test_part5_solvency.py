from __future__ import annotations

import json
import unittest

from backend.modules.solvency.agent import _attach_section_evidence, _clean_final, _prepare_calculation_display
from backend.modules.solvency.calculations import calculate_metric
from backend.modules.solvency.facts import add_facts
from backend.modules.solvency.retrieval import select_initial_pages


def _make_fact(column_header: str, *, with_layout: bool = True):
    layout = ""
    if with_layout:
        table = {
            "bbox": [10, 20, 500, 80],
            "rows": [
                ["项目", "期末余额", "期初余额"],
                ["高级无抵押定息债券", "", "3,541,350,000.00"],
            ],
        }
        layout = "\n[PDF_TABLE_LAYOUT] page=197 table=2 " + json.dumps(table, ensure_ascii=False)
    source = (
        "报告年度2024年，比较期2023年。单位：元 币种：人民币 "
        "项目 期末余额 期初余额 高级无抵押定息债券 3,541,350,000.00"
        + layout
    )
    raw_fact = {
        "fact_key": "bonds_opening",
        "label": "应付债券期初余额",
        "value": "3541350000.00",
        "unit": "元",
        "currency": "人民币",
        "scope": "consolidated",
        "period_type": "instant",
        "as_of_date": "2023-12-31",
        "measurement_basis": "statement_carrying_amount",
        "liability_type": "financing",
        "evidence": [{
            "page": 197,
            "quote": "单位：元 币种：人民币 项目 期末余额 期初余额 高级无抵押定息债券 3,541,350,000.00",
        }],
        "source_context": {
            "kind": "table",
            "row_label": "高级无抵押定息债券",
            "column_header": column_header,
            "unit_label": "单位：元",
            "semantic_status": "confirmed",
            "review_note": "按结构化表格矩阵核对行名、列标题和金额，并确认合并范围与报告期。",
        },
    }
    result = add_facts(
        [raw_fact],
        facts={},
        report_id="sample-report",
        page_count=270,
        get_page=lambda page: source if page == 197 else None,
    )
    return result["facts"][0]


class SolvencyLayoutAndOutputTests(unittest.TestCase):
    def test_layout_confirms_opening_column_with_blank_ending_cell(self):
        fact = _make_fact("期初余额")
        self.assertTrue(fact["validation"]["calculation_eligible"])
        self.assertEqual(fact["source_context"]["layout_alignment"], "confirmed")

    def test_layout_rejects_value_assigned_to_wrong_comparative_column(self):
        fact = _make_fact("期末余额")
        self.assertFalse(fact["validation"]["calculation_eligible"])
        self.assertEqual(fact["source_context"]["layout_alignment"], "mismatch")

    def test_layout_rejects_child_total_submitted_as_parent_total(self):
        table = {
            "bbox": [10, 20, 500, 80],
            "rows": [
                ["项目", "期末余额", "期初余额"],
                ["流动负债合计", "100", "90"],
                ["负债合计", "200", "180"],
            ],
        }
        quote = "单位：元 币种：人民币 项目 期末余额 期初余额 流动负债合计 100 90 负债合计 200 180"
        source = quote + "\n[PDF_TABLE_LAYOUT] page=1 table=1 " + json.dumps(table, ensure_ascii=False)
        fact = add_facts(
            [{
                "fact_key": "total_liabilities",
                "label": "负债合计",
                "value": "100",
                "unit": "元",
                "currency": "人民币",
                "scope": "consolidated",
                "period_type": "instant",
                "as_of_date": "2024-12-31",
                "measurement_basis": "statement_carrying_amount",
                "liability_type": "financing",
                "evidence": [{"page": 1, "quote": quote}],
                "source_context": {
                    "kind": "table",
                    "row_label": "负债合计",
                    "column_header": "期末余额",
                    "unit_label": "单位：元",
                    "semantic_status": "confirmed",
                    "review_note": "测试合计行与子项行的区别。",
                },
            }],
            facts={},
            report_id="row-label-review",
            page_count=1,
            get_page=lambda page: source if page == 1 else None,
        )["facts"][0]

        self.assertEqual(fact["source_context"]["layout_alignment"], "mismatch")
        self.assertFalse(fact["validation"]["calculation_eligible"])

    def test_flat_text_without_layout_keeps_single_amount_ambiguous(self):
        fact = _make_fact("期初余额", with_layout=False)
        self.assertFalse(fact["validation"]["calculation_eligible"])
        self.assertEqual(fact["source_context"]["semantic_status"], "ambiguous")

    def test_statement_continuation_uses_adjacent_page_header(self):
        header_layout = "\n[PDF_TABLE_LAYOUT] page=87 table=1 " + json.dumps({
            "bbox": [1, 1, 100, 50],
            "rows": [["项目", "附注", "2024 年12 月31 日", "2023 年12 月31 日"]],
        }, ensure_ascii=False)
        row_layout = "\n[PDF_TABLE_LAYOUT] page=88 table=1 " + json.dumps({
            "bbox": [1, 1, 100, 50],
            "rows": [["应付债券", "七（46）", "", "3,541,350,000.00"]],
        }, ensure_ascii=False)
        pages = {
            87: "单位：元 项目 附注 2024 年12 月31 日 2023 年12 月31 日" + header_layout,
            88: "应付债券 七（46） 3,541,350,000.00" + row_layout,
        }
        fact = add_facts(
            [{
                "fact_key": "continued_bonds",
                "label": "应付债券比较期余额",
                "value": "3541350000.00",
                "unit": "元",
                "currency": "人民币",
                "scope": "consolidated",
                "period_type": "instant",
                "as_of_date": "2023-12-31",
                "liability_type": "financing",
                "evidence": [
                    {"page": 87, "quote": "单位：元 项目 附注 2024 年12 月31 日 2023 年12 月31 日"},
                    {"page": 88, "quote": "应付债券 七（46） 3,541,350,000.00"},
                ],
                "source_context": {
                    "kind": "table",
                    "row_label": "应付债券",
                    "column_header": "2023 年12 月31 日",
                    "unit_label": "单位：元",
                    "semantic_status": "confirmed",
                    "review_note": "续表金额按相邻页表头映射到比较期列。",
                },
            }],
            facts={},
            report_id="sample-report",
            page_count=270,
            get_page=lambda page: pages.get(page),
        )["facts"][0]
        self.assertTrue(fact["validation"]["calculation_eligible"])
        self.assertEqual(fact["source_context"]["layout_alignment"], "confirmed")

    def test_layout_unavailable_does_not_make_table_fact_calculation_eligible(self):
        source = "单位：元 项目 期末余额 应付债券 100.00"
        fact = add_facts(
            [{
                "fact_key": "layout_unavailable_bond",
                "label": "应付债券期末余额",
                "value": "100.00",
                "unit": "元",
                "currency": "人民币",
                "scope": "consolidated",
                "period_type": "instant",
                "as_of_date": "2024-12-31",
                "liability_type": "financing",
                "evidence": [{"page": 1, "quote": source}],
                "source_context": {
                    "kind": "table", "row_label": "应付债券", "column_header": "期末余额",
                    "unit_label": "单位：元", "semantic_status": "confirmed",
                    "review_note": "测试纯文本无法代替保留的表格矩阵。",
                },
            }], facts={}, report_id="layout-unavailable", page_count=1,
            get_page=lambda page: source if page == 1 else None,
        )["facts"][0]

        self.assertEqual(fact["source_context"]["layout_alignment"], "unavailable")
        self.assertEqual(fact["source_context"]["semantic_status"], "ambiguous")
        self.assertFalse(fact["validation"]["calculation_eligible"])

    def test_header_from_an_unrelated_table_cannot_confirm_row(self):
        header_table = {
            "rows": [["项目", "期末余额", "期初余额"]],
        }
        row_table = {
            "rows": [["应付债券", "", "100.00"]],
        }
        source = (
            "单位：元 项目 期末余额 期初余额 应付债券 100.00"
            + "\n[PDF_TABLE_LAYOUT] page=1 table=1 " + json.dumps(header_table, ensure_ascii=False)
            + "\n[PDF_TABLE_LAYOUT] page=1 table=2 " + json.dumps(row_table, ensure_ascii=False)
        )
        fact = add_facts(
            [{
                "fact_key": "unrelated_table_header",
                "label": "应付债券期初余额",
                "value": "100.00",
                "unit": "元",
                "currency": "人民币",
                "scope": "consolidated",
                "period_type": "instant",
                "as_of_date": "2023-12-31",
                "liability_type": "financing",
                "evidence": [{"page": 1, "quote": "单位：元 项目 期末余额 期初余额 应付债券 100.00"}],
                "source_context": {
                    "kind": "table", "row_label": "应付债券", "column_header": "期初余额",
                    "unit_label": "单位：元", "semantic_status": "confirmed",
                    "review_note": "测试不相邻的另一张表不能提供本行列标题。",
                },
            }], facts={}, report_id="unrelated-layout", page_count=1,
            get_page=lambda page: source if page == 1 else None,
        )["facts"][0]

        self.assertEqual(fact["source_context"]["layout_alignment"], "mismatch")
        self.assertFalse(fact["validation"]["calculation_eligible"])

    def test_layout_uses_geometry_when_merged_amount_cell_shifts_column_index(self):
        table = {
            "bbox": [56.9, 195.4, 538.4, 246.9],
            "rows": [
                ["", "项目", "", "", "期末余额", "", "", "期初余额", ""],
                ["", "货币资金", "", "303,511,993", "", "", "264,306,515", "", ""],
            ],
            "cell_bboxes": [
                [
                    [56.9, 195.4, 62.3, 212.7], [62.3, 195.4, 212.1, 212.7],
                    [212.1, 195.4, 217.4, 212.7], [217.4, 195.4, 222.9, 212.7],
                    [222.9, 195.4, 372.7, 212.7], [372.7, 195.4, 378.0, 212.7],
                    [378.0, 195.4, 383.5, 212.7], [383.5, 195.4, 533.2, 212.7],
                    [533.2, 195.4, 538.4, 212.7],
                ],
                [
                    [56.9, 229.2, 62.3, 246.9], [62.3, 229.2, 212.1, 246.9],
                    [212.1, 229.2, 217.4, 246.9], [217.4, 229.2, 378.0, 246.9],
                    None, None, [378.0, 229.2, 538.4, 246.9], None, None,
                ],
            ],
        }
        layout = "\n[PDF_TABLE_LAYOUT] page=114 table=1 " + json.dumps(table, ensure_ascii=False)
        quote = "单位：千元 项目 期末余额 期初余额 货币资金 303,511,993 264,306,515"
        source = quote + layout

        def add(column_header: str, fact_key: str):
            return add_facts([{
                "fact_key": fact_key,
                "label": "合并货币资金",
                "value": "303511993",
                "unit": "千元",
                "currency": "人民币",
                "scope": "consolidated",
                "period_type": "instant",
                "as_of_date": "2024-12-31",
                "liability_type": "cash",
                "evidence": [{"page": 114, "quote": quote}],
                "source_context": {
                    "kind": "table", "row_label": "货币资金", "column_header": column_header,
                    "unit_label": "单位：千元", "semantic_status": "confirmed",
                    "review_note": "使用表格单元格位置确认金额属于声明的期别。",
                },
            }], facts={}, report_id="ningde-layout", page_count=229,
                get_page=lambda page: source if page == 114 else None)["facts"][0]

        correct = add("期末余额", "cash_closing")
        wrong = add("期初余额", "cash_wrong_period")
        self.assertEqual(correct["source_context"]["layout_alignment"], "confirmed")
        self.assertTrue(correct["validation"]["calculation_eligible"])
        self.assertEqual(wrong["source_context"]["layout_alignment"], "mismatch")
        self.assertFalse(wrong["validation"]["calculation_eligible"])

    def test_short_debt_share_accepts_reported_one_year_bucket(self):
        table_layout = "\n[PDF_TABLE_LAYOUT] page=79 table=1 " + json.dumps({
            "bbox": [1, 1, 100, 50],
            "rows": [
                ["有息债务类别", "已逾期", "1 年以内（含）", "超过1 年（不含）", "金额合计"],
                ["合计", "", "556.08", "49.50", "605.58"],
            ],
        }, ensure_ascii=False)
        quote = "单位：亿元 有息债务类别 已逾期 1 年以内（含） 超过1 年（不含） 金额合计 合计 556.08 49.50 605.58"
        source = quote + table_layout
        raw = [
            {
                "fact_key": "short_debt_1y",
                "label": "报告日后一年内到期融资债务",
                "value": "556.08",
                "unit": "亿元",
                "currency": "人民币",
                "scope": "consolidated",
                "period_type": "maturity_range",
                "period_start": "2024-12-31",
                "period_end": "2025-12-31",
                "measurement_basis": "statement_carrying_amount",
                "liability_type": "financing",
                "evidence": [{"page": 79, "quote": quote}],
                "source_context": {
                    "kind": "table", "row_label": "合计", "column_header": "1 年以内（含）",
                    "unit_label": "单位：亿元", "semantic_status": "confirmed",
                    "review_note": "公司披露原始一年期限段，起点为报告日，终点为一年后。",
                },
            },
            {
                "fact_key": "total_debt_reported",
                "label": "报告日公司有息债务合计",
                "value": "605.58",
                "unit": "亿元",
                "currency": "人民币",
                "scope": "consolidated",
                "period_type": "instant",
                "as_of_date": "2024-12-31",
                "measurement_basis": "statement_carrying_amount",
                "liability_type": "financing",
                "included_fact_keys": ["short_debt_1y"],
                "evidence": [{"page": 79, "quote": quote}],
                "source_context": {
                    "kind": "table", "row_label": "合计", "column_header": "金额合计",
                    "unit_label": "单位：亿元", "semantic_status": "confirmed",
                    "review_note": "公司披露合并有息债务合计，时点为报告日。",
                },
            },
        ]
        added = add_facts(
            raw, facts={}, report_id="sample-report", page_count=270,
            get_page=lambda page: source if page == 79 else None,
        )["facts"]
        fact_map = {item["fact_id"]: item for item in added}
        self.assertTrue(all(item["validation"]["calculation_eligible"] for item in added))
        result = calculate_metric(fact_map, {
            "metric": "short_debt_share",
            "short_debt_fact_ids": [added[0]["fact_id"]],
            "total_debt_fact_ids": [added[1]["fact_id"]],
        })
        self.assertEqual(result["status"], "calculated")
        self.assertAlmostEqual(float(result["value"]), 556.08 / 605.58, places=8)

    def test_short_debt_share_rejects_mixed_carrying_and_contractual_amounts(self):
        short = {
            "fact_id": "solv_short",
            "fact_key": "short",
            "validation": {"status": "quote_and_number_matched", "calculation_eligible": True},
            "normalized_value": "60000000000",
            "normalized_unit": "CNY",
            "currency": "人民币",
            "scope": "consolidated",
            "period_type": "maturity_range",
            "period_start": "2024-12-31",
            "period_end": "2025-12-31",
            "measurement_basis": "contractual_undiscounted_cash_flow",
            "liability_type": "financing",
            "included_fact_ids": [],
        }
        total = {
            "fact_id": "solv_total",
            "fact_key": "total",
            "validation": {"status": "quote_and_number_matched", "calculation_eligible": True},
            "normalized_value": "100000000000",
            "normalized_unit": "CNY",
            "currency": "人民币",
            "scope": "consolidated",
            "period_type": "instant",
            "as_of_date": "2024-12-31",
            "measurement_basis": "statement_carrying_amount",
            "liability_type": "financing",
            "included_fact_ids": ["solv_short"],
        }
        with self.assertRaisesRegex(ValueError, "金额口径"):
            calculate_metric({"solv_short": short, "solv_total": total}, {
                "metric": "short_debt_share",
                "short_debt_fact_ids": ["solv_short"],
                "total_debt_fact_ids": ["solv_total"],
            })
        total["measurement_basis"] = "undiscounted_contractual_cash_flows"
        aligned = calculate_metric({"solv_short": short, "solv_total": total}, {
            "metric": "short_debt_share",
            "short_debt_fact_ids": ["solv_short"],
            "total_debt_fact_ids": ["solv_total"],
        })
        self.assertEqual(aligned["status"], "calculated")

    def test_initial_selection_prefers_current_year_maturity_schedule(self):
        pages = [
            {"page": 236, "text": "流动性风险 未折现合同金额合计 项目 2024 年12 月31 日 1 年以内"},
            {"page": 237, "text": "流动性风险 未折现合同金额合计 项目 2023 年12 月31 日 1 年以内"},
            {"page": 100, "text": "合并现金流量表 经营活动产生的现金流量净额"},
        ]
        selected = select_initial_pages(pages)
        selected_numbers = {item["page"] for item in selected}
        self.assertIn(236, selected_numbers)
        self.assertNotIn(237, selected_numbers)

    def test_short_debt_share_rejects_range_longer_than_one_year(self):
        fact = {
            "fact_id": "solv_short",
            "fact_key": "short",
            "validation": {"status": "quote_and_number_matched", "calculation_eligible": True},
            "normalized_value": "50000000000",
            "normalized_unit": "CNY",
            "currency": "人民币",
            "scope": "consolidated",
            "period_type": "maturity_range",
            "period_start": "2024-12-31",
            "period_end": "2026-01-01",
            "measurement_basis": "statement_carrying_amount",
            "as_of_date": "",
            "liability_type": "financing",
            "included_fact_ids": [],
        }
        total = {
            "fact_id": "solv_total",
            "fact_key": "total",
            "validation": {"status": "quote_and_number_matched", "calculation_eligible": True},
            "normalized_value": "60000000000",
            "normalized_unit": "CNY",
            "currency": "人民币",
            "scope": "consolidated",
            "period_type": "instant",
            "as_of_date": "2024-12-31",
            "measurement_basis": "statement_carrying_amount",
            "liability_type": "financing",
            "included_fact_ids": ["solv_short"],
        }
        with self.assertRaisesRegex(ValueError, "一年内结束"):
            calculate_metric({"solv_short": fact, "solv_total": total}, {
                "metric": "short_debt_share",
                "short_debt_fact_ids": ["solv_short"],
                "total_debt_fact_ids": ["solv_total"],
            })

    def test_ratio_display_values_use_percent_and_keep_raw_value(self):
        calculations = {
            "calc_ratio": {
                "metric": "asset_liability_ratio",
                "status": "calculated",
                "value": "0.629141",
                "unit": "倍",
            },
            "calc_cash": {
                "metric": "net_debt",
                "status": "calculated",
                "value": "35153744441.4",
                "normalized_value": "35153744441.4",
                "normalized_unit": "CNY",
                "unit": "元",
            },
        }
        _prepare_calculation_display(calculations)
        self.assertEqual(calculations["calc_ratio"]["value"], "0.629141")
        self.assertEqual(calculations["calc_ratio"]["display_value"], "62.91")
        self.assertEqual(calculations["calc_ratio"]["display_unit"], "%")
        self.assertEqual(calculations["calc_cash"]["display_value"], "351.54")
        self.assertEqual(calculations["calc_cash"]["display_unit"], "亿元")

    def test_quantified_section_without_fact_or_calculation_citation_needs_review(self):
        result = {"obligations": {"summary": "期后融资券剩余300亿元。"}}
        problems = _attach_section_evidence(result, {}, {}, {249})
        self.assertTrue(problems)
        self.assertEqual(result["obligations"]["section_status"], "needs_review")

    def test_nested_section_citations_are_recognized_and_calculation_attached(self):
        facts = {"solv_a": {"validation": {"status": "quote_and_number_matched", "calculation_eligible": True}}}
        calculations = {"calc_a": {
            "status": "calculated", "display_value": "62.91", "display_unit": "%",
            "formula": "总负债 / 总资产", "note": "程序计算",
        }}
        result = {"support_metrics": {
            "summary": "资产负债率约62.91%。",
            "items": [
                {"label": "资产负债率", "display": "62.91%", "calculation_ids": ["calc_a"]},
                {"label": "债务余额", "amount_100m_cny": "50.00", "fact_ids": ["solv_a"]},
            ],
        }}
        problems = _attach_section_evidence(result, facts, calculations, {1})
        self.assertFalse(problems)
        section = result["support_metrics"]
        self.assertEqual(section["section_status"], "available")
        self.assertEqual(section["calculation_ids"], ["calc_a"])
        self.assertEqual(section["items"][0]["program_result"]["value"], "62.91")

    def test_unconfirmed_fact_keeps_section_in_review(self):
        result = {"debt_structure": {
            "items": [{"label": "债券余额", "amount_100m_cny": "35.41", "fact_ids": ["solv_bad"]}],
        }}
        facts = {"solv_bad": {"validation": {"status": "quote_and_number_matched", "calculation_eligible": False}}}
        _attach_section_evidence(result, facts, {}, {1})
        self.assertEqual(result["debt_structure"]["section_status"], "needs_review")

    def test_finding_title_is_filled_from_model_topic(self):
        result = _clean_final(
            {"findings": [{"topic": "期限结构与集中度", "statement": "债务期限集中在一年内。"}]},
            {}, {}, "sample-report", set(),
        )
        self.assertEqual(result["findings"][0]["title"], "期限结构与集中度")
        self.assertEqual(result["findings"][0]["claim"], "债务期限集中在一年内。")


if __name__ == "__main__":
    unittest.main()
