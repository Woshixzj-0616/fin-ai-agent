"""Focused regression checks for Module 2 evidence and scope selection."""

from decimal import Decimal
import unittest

from modules.part2_profit.engine import (
    _build_profit_trajectory,
    _calculate_profit_metrics,
    _build_analysis_blocks,
    _infer_profit_role,
    _normalize_lines,
    _normalize_segments,
    _quote_contains_amount,
    _reconcile_deducted_profit,
    _unique_line,
)
from modules.part2_profit.evidence import _header_has_currency, normalize_quote, verify_evidence


class ProfitV2EvidenceTests(unittest.TestCase):
    def test_analysis_blocks_carry_their_own_section_data(self):
        blocks = _build_analysis_blocks(
            "盈利总览",
            [{"profit_role": "parent_profit", "numeric_evidence_verified": True, "label": "归母净利润", "current_yuan": "10", "previous_yuan": "9", "reporting_scope": "合并报表", "previous_reporting_scope": "合并报表", "current_period": "2024年度", "previous_period": "2023年度", "currency": "人民币", "previous_currency": "人民币", "value_status": "可计算"}],
            [{"name": "营业毛利率", "current_value": "30.00", "previous_value": "25.00"}],
            {"target_change_yuan": "1", "residual_yuan": "0.00"},
            [{"segment_name": "产品A", "dimension": "产品", "current_gross_margin": "30.00", "verified_pages": [4]}],
            [{"profit_role": "nonrecurring_parent_net", "verified_pages": [5]}],
            [{"title": "净利润变化", "verified_pages": [6]}],
            [{"name": "归母与扣非核对", "status": "一致"}],
            [{"key": "nonrecurring", "status": "supported"}],
            ["上期业务金额未披露"],
        )
        by_id = {block["id"]: block for block in blocks}
        self.assertIn("metrics", by_id["profit_summary"]["data"])
        self.assertIn("profit_trajectory", by_id["profit_summary"]["data"])
        self.assertTrue(by_id["profit_layers"]["data"]["rows"])
        self.assertTrue(by_id["business_gross_profit"]["data"]["segments"])
        self.assertTrue(by_id["profit_findings"]["data"]["findings"])
        self.assertIn("reconciliation", by_id["nonrecurring"]["data"])
        self.assertEqual(by_id["profit_limits"]["data"]["uncertainties"], ["上期业务金额未披露"])

    def test_cat_report_reconciles_profit_growth_while_revenue_declines(self):
        pages = {
            119: (
                "三、合并利润表\n单位：千元\n项目\n2024年度\n2023年度\n"
                "一、营业总收入\n362,012,554\n400,917,045\n"
                "其中：营业收入\n362,012,554\n400,917,045"
            ),
            120: (
                "资产减值损失\n-8,423,325\n-5,853,927\n"
                "1.归属于母公司股东的净利润\n50,744,682\n44,121,248"
            ),
        }
        raw_lines = [
            {
                "profit_role": "operating_revenue", "label": "其中：营业收入",
                "current_value": "362,012,554", "previous_value": "400,917,045", "unit": "千元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "reporting_scope": "合并报表", "currency": "人民币", "source_pages": [119],
                "evidence_quote": "其中：营业收入 362,012,554 400,917,045",
            },
            {
                "profit_role": "parent_profit", "label": "归属于母公司股东的净利润",
                "current_value": "50,744,682", "previous_value": "44,121,248", "unit": "千元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "reporting_scope": "合并报表", "currency": "人民币", "source_pages": [120],
                "evidence_quote": "归属于母公司股东的净利润 50,744,682 44,121,248",
            },
        ]
        rows = _normalize_lines(
            raw_lines, key_field="profit_role", allowed_pages={119, 120}, page_text=pages.get, limit=10
        )
        revenue = next(item for item in rows if item["profit_role"] == "operating_revenue")
        parent = next(item for item in rows if item["profit_role"] == "parent_profit")

        self.assertEqual(revenue["value_status"], "可计算")
        self.assertEqual(revenue["change_percent"], "-9.70")
        self.assertEqual(parent["value_status"], "可计算")
        self.assertEqual(parent["change_percent"], "15.01")
        self.assertEqual(parent["row_context_verification"]["currency_evidence_mode"], "domestic_unit_default")
        self.assertEqual(_build_profit_trajectory(rows)["direction"], "利润增长")
        self.assertEqual(_build_profit_trajectory(rows)["change_yuan"], "6623434000")

    def test_longi_crosses_from_profit_to_loss_without_presenting_ordinary_yoy(self):
        pages = {
            124: (
                "合并利润表\n2024年1—12月\n单位：元\n币种：人民币\n"
                "项目\n附注\n2024年度\n2023年度\n营业收入\n82,582,273,118.72\n129,497,674,192.20"
            ),
            125: (
                "资产减值损失（损失以“－”号填列）\n-8,700,743,502.63\n-7,024,764,420.39\n"
                "归属于母公司股东的净利润（净亏损以“－”号填列）\n"
                "-8,617,528,506.44\n10,751,425,556.38"
            ),
        }
        rows = _normalize_lines(
            [{
                "profit_role": "parent_profit", "label": "归属于母公司股东的净利润",
                "current_value": "-8,617,528,506.44", "previous_value": "10,751,425,556.38", "unit": "元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "reporting_scope": "合并报表", "currency": "人民币", "source_pages": [125],
                "evidence_quote": "归属于母公司股东的净利润（净亏损以“－”号填列） -8,617,528,506.44 10,751,425,556.38",
            }],
            key_field="profit_role", allowed_pages={124, 125}, page_text=pages.get, limit=10,
        )
        parent = rows[0]
        trajectory = _build_profit_trajectory(rows)

        self.assertEqual(parent["value_status"], "可计算")
        self.assertIsNone(parent["change_percent"])
        self.assertIn("由盈转亏", parent["change_percent_status"])
        self.assertEqual(trajectory["status"], "可复核")
        self.assertEqual(trajectory["direction"], "由盈转亏")
        self.assertTrue(trajectory["crosses_zero"])
        self.assertEqual(trajectory["change_yuan"], "-19368954062.82")
        self.assertIn("不把年报列示的同比百分比当作普通增减幅度", trajectory["note"])

    def test_currency_default_uses_domestic_statement_units_but_respects_explicit_foreign_currency(self):
        self.assertTrue(_header_has_currency(normalize_quote("合并利润表 单位：千元"), "人民币"))
        self.assertFalse(_header_has_currency(normalize_quote("合并利润表 单位：美元 币种：美元"), "人民币"))

    def test_primary_statement_row_wins_over_note_level_main_business_revenue(self):
        statement = {
            "profit_role": "operating_revenue", "label": "其中：营业收入",
            "current_yuan": "115", "previous_yuan": "126", "reporting_scope": "合并报表",
            "previous_reporting_scope": "合并报表", "currency": "人民币", "previous_currency": "人民币",
            "current_period": "2024年度", "previous_period": "2023年度", "value_status": "可计算",
        }
        note = {**statement, "label": "营业收入——主营业务收入（合并，附注61）", "current_yuan": "114", "previous_yuan": "124"}
        selected = _unique_line([note, statement], "operating_revenue")
        self.assertEqual(selected["current_yuan"], "115")

    def test_primary_profit_statement_rows_win_over_note_detail_conflicts(self):
        base = {
            "profit_role": "investment_income", "current_yuan": "241", "previous_yuan": "-31",
            "reporting_scope": "合并报表", "previous_reporting_scope": "合并报表",
            "currency": "人民币", "previous_currency": "人民币",
            "current_period": "2024年度", "previous_period": "2023年度", "value_status": "可计算",
        }
        statement = {**base, "label": "投资收益（损失以－号填列）"}
        detail = {**base, "label": "处置长期股权投资产生的投资收益", "current_yuan": "258", "previous_yuan": "0.3"}
        self.assertEqual(_unique_line([detail, statement], "investment_income")["current_yuan"], "241")

    def test_quote_verification_handles_equivalent_chinese_quotes_and_spacing(self):
        evidence = verify_evidence(
            {"source_pages": [2], "evidence_quote": '少数股东损益（净亏损以"-"号填列） 10 -20'},
            {2},
            lambda _: "少数股东损益（净亏损以“－”号填列）\n10\n-20",
        )
        self.assertTrue(evidence["quote_verified"])
        self.assertEqual(evidence["verified_pages"], [2])

    def test_common_statement_labels_map_to_calculation_roles(self):
        cases = {
            "一、营业总收入": "total_operating_revenue",
            "其中：营业收入": "operating_revenue",
            "利息收入": "interest_income",
            "其中：利息收入": "unmapped",
            "利息收入（营业总收入项下）": "interest_income",
            "其中：利息费用（财务费用项下）": "unmapped",
            "其中：利息收入（财务费用项下）": "unmapped",
            "财务费用其中：利息收入": "unmapped",
            "资产减值损失其中：商誉减值损失": "unmapped",
            "其中：营业成本": "operating_cost",
            "合计（归属于母公司股东的非经常性损益净额）": "nonrecurring_parent_net",
            "减：所得税影响额": "nonrecurring_tax_effect",
            "少数股东权益影响额（税后）": "nonrecurring_minority_effect",
            "二、营业总成本": "total_operating_cost",
            "财务费用（损失以‘－’号填列）": "financial_expense",
            "三、营业利润（亏损以‘－’号填列）": "operating_profit",
            "四、利润总额（亏损总额以‘－’号填列）": "profit_before_tax",
            "归属于母公司股东的净利润": "parent_profit",
            "扣除非经常性损益后归属于母公司股东的净利润": "deducted_parent_profit",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(_infer_profit_role(label), expected)

    def test_amount_can_be_verified_across_separate_page_fragments(self):
        page_text = {
            3: "合并利润表\n单位：元\n币种：人民币\n项目\n2024年度\n营业收入\n100",
            4: "合并利润表\n单位：元\n币种：人民币\n项目\n2023年度\n营业收入\n80",
        }
        raw = [{
            "profit_role": "operating_revenue", "label": "营业收入",
            "current_value": "100", "current_unit": "元", "previous_value": "80", "previous_unit": "元",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "合并报表",
            "current_currency": "人民币", "previous_currency": "人民币",
            "evidence_segments": [
                {"source_pages": [3], "quote": "2024年度 营业收入 100"},
                {"source_pages": [4], "quote": "2023年度 营业收入 80"},
            ],
        }]
        rows = _normalize_lines(raw, key_field="profit_role", allowed_pages={3, 4}, page_text=page_text.get, limit=10)
        self.assertTrue(rows[0]["numeric_evidence_verified"])
        self.assertEqual(rows[0]["verified_pages"], [3, 4])
        self.assertEqual(rows[0]["value_status"], "可计算")

    def test_swapped_period_amounts_are_blocked_even_when_both_numbers_are_quoted(self):
        page = "合并利润表\n单位：元\n币种：人民币\n项目\n附注\n2024年度\n2023年度\n营业收入\n100\n80"
        rows = _normalize_lines([{
            "label": "营业收入", "current_value": "80", "current_unit": "元",
            "previous_value": "100", "previous_unit": "元",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "合并报表",
            "current_currency": "人民币", "previous_currency": "人民币",
            "source_pages": [1], "evidence_quote": "营业收入 100 80",
        }], key_field="profit_role", allowed_pages={1}, page_text=lambda _: page, limit=10)
        self.assertTrue(rows[0]["quote_verified"])
        self.assertFalse(rows[0]["numeric_evidence_verified"])
        self.assertIsNone(rows[0]["change_yuan"])

    def test_wrong_unit_and_wrong_row_label_are_blocked(self):
        page = "合并利润表\n单位：元\n币种：人民币\n项目\n附注\n2024年度\n2023年度\n营业收入\n100\n80"
        base = {
            "current_value": "100", "previous_value": "80",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "合并报表",
            "current_currency": "人民币", "previous_currency": "人民币",
            "source_pages": [1], "evidence_quote": "营业收入 100 80",
        }
        wrong_unit = _normalize_lines([{
            **base, "label": "营业收入", "current_unit": "亿元", "previous_unit": "亿元",
        }], key_field="profit_role", allowed_pages={1}, page_text=lambda _: page, limit=10)[0]
        wrong_label = _normalize_lines([{
            **base, "label": "净利润", "current_unit": "元", "previous_unit": "元",
        }], key_field="profit_role", allowed_pages={1}, page_text=lambda _: page, limit=10)[0]
        self.assertFalse(wrong_unit["numeric_evidence_verified"])
        self.assertFalse(wrong_label["numeric_evidence_verified"])

    def test_continuation_page_inherits_its_matching_nonrecurring_table_header(self):
        page_text = {
            10: "十、非经常性损益项目和金额\n单位：元\n币种：人民币\n非经常性损益项目\n2024年金额\n附注\n2023年金额",
            11: "非流动性资产处置损益\n100\n80",
        }
        row = _normalize_lines([{
            "profit_role": "nonrecurring_item", "label": "非流动性资产处置损益",
            "current_value": "100", "current_unit": "元", "previous_value": "80", "previous_unit": "元",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "归属于母公司股东口径", "previous_scope": "归属于母公司股东口径",
            "current_currency": "人民币", "previous_currency": "人民币",
            "source_pages": [11], "evidence_quote": "非流动性资产处置损益 100 80",
        }], key_field="profit_role", allowed_pages={10, 11}, page_text=page_text.get, limit=10)[0]
        self.assertTrue(row["numeric_evidence_verified"])
        self.assertTrue(row["row_context_verification"]["scope_verified"])
        self.assertEqual(row["row_context_verification"]["verified_context_pages"], [11, 10])

    def test_unrelated_shareholder_text_on_page_does_not_verify_another_table_scope(self):
        fillers = "\n".join(f"其他披露说明第{i}行" for i in range(24))
        page = (
            "归属于上市公司股东的扣除非经常性损益的净利润\n" + fillers +
            "\n合并利润表\n单位：元\n币种：人民币\n项目\n2024年度\n2023年度\n合计\n100\n80"
        )
        row = _normalize_lines([{
            "profit_role": "nonrecurring_parent_net", "label": "合计",
            "current_value": "100", "current_unit": "元", "previous_value": "80", "previous_unit": "元",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "归属于母公司股东口径", "previous_scope": "归属于母公司股东口径",
            "current_currency": "人民币", "previous_currency": "人民币",
            "source_pages": [1], "evidence_quote": "合计 100 80",
        }], key_field="profit_role", allowed_pages={1}, page_text=lambda _: page, limit=10)[0]
        self.assertFalse(row["row_context_verification"]["scope_verified"])
        self.assertFalse(row["numeric_evidence_verified"])

    def test_signed_amount_matching_does_not_drop_the_minus_sign(self):
        page = "合并利润表\n单位：元\n币种：人民币\n项目\n附注\n2024年度\n2023年度\n财务费用\n-10\n-5"
        raw = {
            "label": "财务费用", "current_value": "10", "current_unit": "元",
            "previous_value": "-5", "previous_unit": "元",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "合并报表",
            "current_currency": "人民币", "previous_currency": "人民币",
            "source_pages": [1], "evidence_quote": "财务费用 -10 -5",
        }
        row = _normalize_lines([raw], key_field="profit_role", allowed_pages={1}, page_text=lambda _: page, limit=10)[0]
        self.assertFalse(row["numeric_evidence_verified"])

    def test_consolidated_row_is_selected_independently_of_parent_company_row(self):
        rows = [
            {"profit_role": "parent_profit", "value_status": "可计算", "current_yuan": "10", "previous_yuan": "9", "reporting_scope": "母公司报表"},
            {"profit_role": "parent_profit", "value_status": "可计算", "current_yuan": "20", "previous_yuan": "18", "reporting_scope": "合并报表"},
        ]
        chosen = _unique_line(rows, "parent_profit")
        self.assertEqual(chosen["current_yuan"], "20")
        self.assertEqual(_unique_line(rows, "parent_profit", scope="parent")["current_yuan"], "10")

    def test_missing_amount_is_never_marked_as_numerically_verified(self):
        self.assertFalse(_quote_contains_amount("项目未披露", None))

    def test_parenthesized_negative_amount_matches_signed_number(self):
        self.assertTrue(_quote_contains_amount("财务费用（1,234.50）", Decimal("-1234.50")))

    def test_label_context_overrides_wrong_model_role_and_excludes_note_detail(self):
        text = "营业总收入 100 90 利息收入（营业总收入项下） 10 9 其中：利息收入（财务费用项下） 40 30"
        raw = [
            {"profit_role": "total_operating_revenue", "label": "利息收入（营业总收入项下）", "current_value": "10", "previous_value": "9", "current_unit": "元", "previous_unit": "元", "current_period": "2024年度", "previous_period": "2023年度", "current_scope": "合并报表", "previous_scope": "合并报表", "current_currency": "人民币", "previous_currency": "人民币", "source_pages": [1], "evidence_quote": "利息收入（营业总收入项下） 10 9"},
            {"profit_role": "financial_expense", "label": "其中：利息收入（财务费用项下）", "current_value": "40", "previous_value": "30", "current_unit": "元", "previous_unit": "元", "current_period": "2024年度", "previous_period": "2023年度", "current_scope": "合并报表", "previous_scope": "合并报表", "current_currency": "人民币", "previous_currency": "人民币", "source_pages": [1], "evidence_quote": "其中：利息收入（财务费用项下） 40 30"},
        ]
        rows = _normalize_lines(raw, key_field="profit_role", allowed_pages={1}, page_text=lambda _: text, limit=10)
        self.assertEqual(rows[0]["profit_role"], "interest_income")
        self.assertEqual(rows[1]["profit_role"], "unmapped")

    def test_business_margin_is_calculated_only_from_verified_revenue_and_cost(self):
        page_text = {
            7: "合并财务报表项目注释\n2024年度\n单位：元\n币种：人民币\n液态奶\n100\n70",
            8: "合并财务报表项目注释\n2023年度\n单位：元\n币种：人民币\n液态奶\n90\n63",
        }
        rows = _normalize_segments([{
            "segment_name": "液态奶", "dimension": "产品",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "合并报表", "currency": "人民币",
            "current_revenue": "100", "revenue_unit": "元", "previous_revenue": "90", "previous_revenue_unit": "元",
            "current_cost": "70", "cost_unit": "元", "previous_cost": "63", "previous_cost_unit": "元",
            "evidence_segments": [
                {"source_pages": [7], "quote": "液态奶 100 70"},
                {"source_pages": [8], "quote": "液态奶 90 63"},
            ],
        }], allowed_pages={7, 8}, page_text=page_text.get)
        self.assertEqual(rows[0]["current_gross_margin"], "30.00")
        self.assertEqual(rows[0]["previous_gross_margin"], "30.00")
        self.assertEqual(rows[0]["status"], "两期收入成本可复算")

    def test_business_margin_delta_is_suppressed_when_source_scope_disagrees_with_model(self):
        page_text = {
            7: "合并财务报表项目注释\n2024年度\n单位：元\n币种：人民币\n液态奶\n100\n70",
            8: "母公司财务报表项目注释\n2023年度\n单位：元\n币种：人民币\n液态奶\n90\n63",
        }
        rows = _normalize_segments([{
            "segment_name": "液态奶", "dimension": "产品",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "合并报表", "currency": "人民币",
            "current_revenue": "100", "revenue_unit": "元", "previous_revenue": "90", "previous_revenue_unit": "元",
            "current_cost": "70", "cost_unit": "元", "previous_cost": "63", "previous_cost_unit": "元",
            "evidence_segments": [
                {"source_pages": [7], "quote": "液态奶 100 70"},
                {"source_pages": [8], "quote": "液态奶 90 63"},
            ],
        }], allowed_pages={7, 8}, page_text=page_text.get)
        self.assertEqual(rows[0]["current_gross_margin"], "30.00")
        self.assertEqual(rows[0]["previous_gross_margin"], "30.00")
        self.assertIsNone(rows[0]["gross_margin_change_percentage_points"])
        self.assertIn("不可比", rows[0]["status"])

    def test_business_margin_delta_is_suppressed_when_declared_scopes_differ(self):
        page_text = {
            7: "合并财务报表项目注释\n2024年度\n单位：元\n币种：人民币\n液态奶\n100\n70",
            8: "母公司财务报表项目注释\n2023年度\n单位：元\n币种：人民币\n液态奶\n90\n63",
        }
        row = _normalize_segments([{
            "segment_name": "液态奶", "dimension": "产品",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "母公司报表", "currency": "人民币",
            "current_revenue": "100", "revenue_unit": "元", "previous_revenue": "90", "previous_revenue_unit": "元",
            "current_cost": "70", "cost_unit": "元", "previous_cost": "63", "previous_cost_unit": "元",
            "evidence_segments": [
                {"source_pages": [7], "quote": "液态奶 100 70"},
                {"source_pages": [8], "quote": "液态奶 90 63"},
            ],
        }], allowed_pages={7, 8}, page_text=page_text.get)[0]
        self.assertEqual(row["current_gross_margin"], "30.00")
        self.assertEqual(row["previous_gross_margin"], "30.00")
        self.assertIsNone(row["gross_margin_change_percentage_points"])

    def test_business_margin_can_compare_two_periods_from_the_same_table_row(self):
        page = (
            "主营业务分产品情况\n单位：元\n币种：人民币\n项目\n2024年度\n2023年度\n"
            "液态奶\n100\n90\n70\n72"
        )
        row = _normalize_segments([{
            "segment_name": "液态奶", "dimension": "产品",
            "current_period": "2024年度", "previous_period": "2023年度",
            "current_scope": "合并报表", "previous_scope": "合并报表", "currency": "人民币",
            "current_revenue": "100", "revenue_unit": "元", "previous_revenue": "90", "previous_revenue_unit": "元",
            "current_cost": "70", "cost_unit": "元", "previous_cost": "72", "previous_cost_unit": "元",
            "evidence_segments": [{"source_pages": [7], "quote": "液态奶 100 90 70 72"}],
        }], allowed_pages={7}, page_text=lambda _: page)[0]
        self.assertEqual(row["current_gross_margin"], "30.00")
        self.assertEqual(row["previous_gross_margin"], "20.00")
        self.assertEqual(row["gross_margin_change_percentage_points"], "10.00")
        self.assertIn("同一表内", row["comparability_note"])
        self.assertIn("整体范围未单独确认", row["comparability_note"])

    def test_parent_attributed_nonrecurring_total_reconciles_with_consolidated_parent_profit(self):
        special = {
            "profit_role": "nonrecurring_parent_net", "current_yuan": "10", "previous_yuan": "5",
            "current_value": "10", "previous_value": "5", "current_unit": "元", "previous_unit": "元",
            "current_period": "2024年度", "previous_period": "2023年度",
            "reporting_scope": "归属于母公司股东口径", "previous_reporting_scope": "归属于母公司股东口径",
            "currency": "人民币", "previous_currency": "人民币", "value_status": "可计算",
        }
        parent = {
            "profit_role": "parent_profit", "current_yuan": "80", "previous_yuan": "65",
            "current_value": "80", "previous_value": "65", "current_unit": "元", "previous_unit": "元",
            "current_period": "2024年度", "previous_period": "2023年度",
            "reporting_scope": "合并报表", "previous_reporting_scope": "合并报表",
            "currency": "人民币", "previous_currency": "人民币", "value_status": "可计算",
        }
        deducted = {**parent, "profit_role": "deducted_parent_profit", "current_yuan": "70", "previous_yuan": "60", "current_value": "70", "previous_value": "60"}
        result = _reconcile_deducted_profit(
            [parent, deducted],
            [special],
        )
        self.assertIn("本期在披露精度范围内", result["status"])
        self.assertIn("上期在披露精度范围内", result["status"])


if __name__ == "__main__":
    unittest.main()
