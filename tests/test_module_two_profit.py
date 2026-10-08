"""Deterministic checks for module-two accounting math; these tests make no API calls."""

from decimal import Decimal
import unittest

from backend.profit_agent import (
    _build_profit_bridge,
    _calculate_profit_metrics,
    _calculate_profit_reconciliations,
    _normalize_lines,
    _parse_amount,
    _reconcile_deducted_profit,
)


def row(
    role: str,
    current: str,
    previous: str,
    *,
    scope: str = "合并报表",
    previous_scope: str | None = None,
    current_period: str = "2024年度",
    previous_period: str = "2023年度",
    unit: str = "元",
    currency: str = "人民币",
    label: str | None = None,
) -> dict:
    current_amount = Decimal(current)
    previous_amount = Decimal(previous)
    return {
        "profit_role": role,
        "label": label or role,
        "current_value": current,
        "previous_value": previous,
        "current_unit": unit,
        "previous_unit": unit,
        "current_period": current_period,
        "previous_period": previous_period,
        "reporting_scope": scope,
        "previous_reporting_scope": previous_scope or scope,
        "currency": currency,
        "previous_currency": currency,
        "current_yuan": str(current_amount),
        "previous_yuan": str(previous_amount),
        "change_yuan": str(current_amount - previous_amount),
        "value_status": "可计算",
        "numeric_evidence_verified": True,
        "quote_verified": True,
        "source_pages": [1],
        "evidence_quote": "年报原文已核对",
    }


class ProfitCalculationTests(unittest.TestCase):
    def test_parse_amount_keeps_reported_negative_sign(self):
        self.assertEqual(_parse_amount("(1,234.50)"), Decimal("-1234.50"))
        self.assertEqual(_parse_amount("-2,000"), Decimal("-2000"))
        self.assertIsNone(_parse_amount("—"))

    def test_gross_margin_uses_operating_revenue_not_total_operating_revenue(self):
        metrics, _ = _calculate_profit_metrics([
            row("total_operating_revenue", "130", "120"),
            row("operating_revenue", "100", "80"),
            row("operating_cost", "60", "50"),
        ])
        gross_margin = next(item for item in metrics if item["name"] == "营业毛利率")
        self.assertEqual(gross_margin["current_value"], "40.00")
        self.assertEqual(gross_margin["previous_value"], "37.50")
        self.assertEqual(gross_margin["change_percentage_points"], "2.50")

        metrics_without_operating_revenue, _ = _calculate_profit_metrics([
            row("total_operating_revenue", "130", "120"),
            row("operating_cost", "60", "50"),
        ])
        self.assertFalse(any(item["name"] == "营业毛利率" for item in metrics_without_operating_revenue))

    def test_negative_financial_expense_and_signed_impairment_keep_correct_effect(self):
        bridge = _build_profit_bridge([
            row("parent_profit", "110", "100"),
            row("financial_expense", "-5", "-2"),
            row("credit_impairment_loss", "-10", "-4"),
        ])
        effects = {item["role"]: Decimal(item["profit_effect_yuan"]) for item in bridge["contributions"]}
        self.assertEqual(effects["financial_expense"], Decimal("3"))
        self.assertEqual(effects["credit_impairment_loss"], Decimal("-6"))
        self.assertEqual(Decimal(bridge["identified_contribution_yuan"]), Decimal("-3"))

    def test_revenue_and_cost_subtotals_are_not_added_twice(self):
        bridge = _build_profit_bridge([
            row("parent_profit", "110", "100"),
            row("total_operating_revenue", "160", "150"),
            row("operating_revenue", "100", "90"),
            row("interest_income", "50", "45"),
            row("other_operating_revenue", "10", "15"),
            row("total_operating_cost", "70", "60"),
            row("operating_cost", "50", "40"),
            row("selling_expense", "5", "5"),
            row("administrative_expense", "5", "5"),
            row("financial_expense", "10", "10"),
        ])
        included = {item["role"] for item in bridge["contributions"]}
        details = {item["role"]: item for item in bridge["detail_rows"]}
        self.assertNotIn("total_operating_revenue", included)
        self.assertNotIn("total_operating_cost", included)
        self.assertTrue({"operating_revenue", "interest_income", "other_operating_revenue"} <= included)
        self.assertTrue({"operating_cost", "selling_expense", "administrative_expense", "financial_expense"} <= included)
        self.assertFalse(details["total_operating_revenue"]["included_in_total"])
        self.assertFalse(details["total_operating_cost"]["included_in_total"])

    def test_unreconciled_subtotal_uses_parent_and_shows_children_as_detail(self):
        bridge = _build_profit_bridge([
            row("parent_profit", "110", "100"),
            row("total_operating_revenue", "160", "150"),
            row("operating_revenue", "100", "90"),
            row("interest_income", "50", "45"),
            row("total_operating_cost", "70", "60"),
            row("operating_cost", "50", "40"),
            row("selling_expense", "5", "4"),
        ])
        included = {item["role"] for item in bridge["contributions"]}
        details = {item["role"]: item for item in bridge["detail_rows"]}
        self.assertIn("total_operating_revenue", included)
        self.assertIn("total_operating_cost", included)
        self.assertNotIn("operating_revenue", included)
        self.assertFalse(details["operating_revenue"]["included_in_total"])

    def test_bridge_excludes_mixed_scope_and_different_year_rows(self):
        consolidated_target = row("parent_profit", "110", "100")
        mixed_scope = row("investment_income", "20", "10", scope="母公司报表")
        old_period = row("selling_expense", "20", "10", current_period="2023年度", previous_period="2022年度")
        bridge = _build_profit_bridge([consolidated_target, mixed_scope, old_period])
        self.assertEqual(bridge["contributions"], [])
        self.assertEqual(bridge["residual_yuan"], "10")

    def test_normalizer_rejects_unmatched_quote_and_scope_change(self):
        raw = [
            {
                "profit_role": "operating_revenue", "label": "营业收入",
                "current_value": "100", "current_unit": "万元", "previous_value": "80", "previous_unit": "万元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "current_scope": "合并报表", "previous_scope": "母公司报表",
                "current_currency": "人民币", "previous_currency": "人民币",
                "source_pages": [1], "evidence_quote": "营业收入 100 80",
            },
            {
                "profit_role": "operating_cost", "label": "营业成本",
                "current_value": "60", "current_unit": "万元", "previous_value": "50", "previous_unit": "万元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "current_scope": "合并报表", "previous_scope": "合并报表",
                "current_currency": "人民币", "previous_currency": "人民币",
                "source_pages": [1], "evidence_quote": "营业成本 60 50",
            },
            {
                "profit_role": "financial_expense", "label": "财务费用",
                "current_value": "-7", "current_unit": "万元", "previous_value": "-5", "previous_unit": "万元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "current_scope": "合并报表", "previous_scope": "合并报表",
                "source_pages": [1], "evidence_quote": "财务费用 -7 -5",
            },
            {
                "profit_role": "investment_income", "label": "投资收益",
                "current_value": "9", "current_unit": "万元", "previous_value": "8", "previous_unit": "万元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "current_scope": "不确定", "previous_scope": "不确定",
                "current_currency": "人民币", "previous_currency": "人民币",
                "source_pages": [1], "evidence_quote": "投资收益 9 8",
            },
            {
                "profit_role": "asset_disposal_income", "label": "资产处置收益",
                "current_value": "4", "current_unit": "万元", "previous_value": "3", "previous_unit": "万元",
                "current_period": "2024年度", "previous_period": "2023年度",
                "current_scope": "合并报表", "previous_scope": "合并报表",
                "current_currency": "未确认", "previous_currency": "未确认",
                "source_pages": [1], "evidence_quote": "资产处置收益 4 3",
            },
        ]
        text = "年报项目：营业收入 100 80；营业成本 60 50；投资收益 9 8；资产处置收益 4 3"
        normalized = _normalize_lines(raw, key_field="profit_role", allowed_pages={1}, page_text=lambda _: text, limit=10)
        self.assertEqual(normalized[0]["value_status"], "口径或期间不可比")
        self.assertEqual(normalized[1]["value_status"], "可计算")
        self.assertEqual(normalized[2]["value_status"], "待核对出处或数值")
        self.assertEqual(normalized[3]["value_status"], "口径或期间不可比")
        self.assertEqual(normalized[4]["value_status"], "口径或期间不可比")

    def test_profit_layer_reconciliations_use_explicit_signed_values(self):
        rows = [
            row("total_operating_revenue", "160", "150"),
            row("total_operating_cost", "70", "80"),
            row("other_income", "5", "3"),
            row("credit_impairment_loss", "0", "2"),
            row("operating_profit", "95", "75"),
            row("non_operating_income", "8", "10"),
            row("non_operating_expense", "3", "5"),
            row("profit_before_tax", "100", "80"),
            row("income_tax_expense", "15", "10"),
            row("net_profit", "85", "70"),
            row("minority_profit", "5", "5"),
            row("parent_profit", "80", "65"),
        ]
        checks = _calculate_profit_reconciliations(rows)
        self.assertTrue(all("在披露精度范围内" in check["status"] for check in checks))
        self.assertEqual(checks[0]["current_calculated_yuan"], "95")
        self.assertEqual(checks[0]["previous_calculated_yuan"], "75")
        self.assertEqual(checks[1]["current_calculated_yuan"], "100")
        self.assertEqual(checks[2]["previous_calculated_yuan"], "70")
        self.assertEqual(checks[3]["current_calculated_yuan"], "80")

    def test_missing_reconciliation_inputs_are_not_assumed_zero(self):
        checks = _calculate_profit_reconciliations([row("net_profit", "10", "8")])
        net_check = next(item for item in checks if item["name"] == "合并净利润")
        self.assertIn("未复算：缺少可核验项目", net_check["status"])

    def test_deducted_profit_reconciles_only_to_reported_parent_nonrecurring_net(self):
        check = _reconcile_deducted_profit(
            [row("parent_profit", "80", "65"), row("deducted_parent_profit", "70", "60")],
            [row("nonrecurring_parent_net", "10", "5")],
        )
        self.assertIn("本期在披露精度范围内", check["status"])
        self.assertIn("上期在披露精度范围内", check["status"])


if __name__ == "__main__":
    unittest.main()
