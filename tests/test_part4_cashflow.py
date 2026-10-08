from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from backend.modules.cashflow.agent import _precompute_operating_after_capex, _reference_hints_for_amount, _submit
from backend.modules.cashflow.calculations import calculate


def _fact(fact_id: str, metric_key: str, value: str, period: str = "2024年度") -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "metric_key": metric_key,
        "original_label": metric_key,
        "value": value,
        "normalized_value": value,
        "unit": "元",
        "normalized_unit": "CNY_yuan",
        "currency": "CNY",
        "period": period,
        "period_kind": "period_amount",
        "scope": "合并",
        "calculable": True,
        "identity_status": "verified",
        "evidence_status": "numeric_quote_page_matched",
        "evidence": [{
            "page": 1,
            "quote": "合并现金流量表 2024年度 现金流净额 10元",
            "table_label": "合并现金流量表",
            "row_label": "现金流净额",
            "column_label": "2024年度",
            "page_valid": True,
            "quote_matches_page": True,
            "number_appears_in_quote": True,
            "row_label_matches_quote": True,
            "column_label_matches_page": True,
            "table_label_matches_page": True,
        }],
    }


def _section(key: str, fact_id: str = "f1") -> dict[str, Any]:
    return {
        "key": key,
        "title": key,
        "status": "analyzed",
        "summary": "现金流净额10元。",
        "detail": "2024年度合并口径，引用现金流净额10元。",
        "fact_ids": [fact_id],
        "calculation_ids": [],
        "source_pages": [1],
    }


def _args(sections: list[dict[str, Any]], overall_view: str = "") -> dict[str, Any]:
    return {
        "company": "测试公司",
        "report_year": "2024",
        "overall_view": overall_view,
        "overall_fact_ids": [],
        "overall_calculation_ids": [],
        "overall_source_pages": [],
        "sections": sections,
        "topics": [],
        "limitations": [],
        "follow_up_questions": [],
    }


class _Recorder:
    def record(self, *_: Any, **__: Any) -> None:
        pass


class _Context:
    report_id = "test-report"
    page_count = 1
    recorder = _Recorder()

    def save_artifact(self, *_: Any, **__: Any) -> None:
        pass

    def record_calculation(self, *_: Any, **__: Any) -> None:
        pass


class CashflowModuleTests(unittest.TestCase):
    def test_cashflow_sections_can_be_saved_and_retried_independently(self) -> None:
        fact = _fact("f1", "cash_flow_value", "10")
        state = {
            "facts": {"f1": fact},
            "calculations": {},
            "checkpoint": 0,
            "observed_pages": {1: "合并现金流量表 2024年度 现金流净额 10元"},
        }
        context = _Context()

        first = _submit(_args([_section("cash_overview")]), state, context)
        self.assertFalse(first["accepted"])
        self.assertEqual(set(state["accepted_sections"]), {"cash_overview"})

        rejected = _submit(_args([_section("profit_to_cash", "unknown")]), state, context)
        self.assertFalse(rejected["accepted"])
        self.assertIn("cash_overview", state["accepted_sections"])
        self.assertNotIn("profit_to_cash", state["accepted_sections"])

        remaining = [
            _section("profit_to_cash"),
            _section("operating_cash"),
            _section("investment_cash"),
            _section("financing_cash"),
        ]
        staged = _submit(_args(remaining), state, context)
        self.assertFalse(staged["accepted"])
        self.assertEqual(staged["pending_sections"], [])
        self.assertNotIn("last_rejected_submission", state)

        final = _submit(_args([], "五个板块已分别通过校验并汇总。"), state, context)
        self.assertTrue(final["accepted"])
        self.assertEqual(len(state["final_result"]["sections"]), 5)
        self.assertEqual(state["final_result"]["analysis_completeness"], "complete")


    def test_yoy_requires_the_same_metric_key(self) -> None:
        facts = {
            "sales_24": _fact("sales_24", "sales_cash_received", "100", "2024年度"),
            "cost_23": _fact("cost_23", "purchase_cash_paid", "90", "2023年度"),
        }
        result = calculate({"operation": "yoy", "name": "bad comparison", "fact_ids": ["sales_24", "cost_23"]}, facts)
        self.assertEqual(result["status"], "not_calculated")
        self.assertIn("metric_key", result["reason"])


    def test_cash_bridge_rejects_wrong_financial_roles(self) -> None:
        metric_keys = ["revenue", "expense", "debt", "tax", "cash"]
        facts = {f"f{i}": _fact(f"f{i}", key, str(i + 1)) for i, key in enumerate(metric_keys)}
        result = calculate({"operation": "cash_bridge", "name": "wrong rows", "fact_ids": list(facts)}, facts)
        self.assertEqual(result["status"], "not_calculated")
        self.assertIn("财务角色", result["reason"])


    def test_cash_bridge_accepts_the_expected_rows_and_reconciles(self) -> None:
        values = [
            ("op", "op_net_cash_cfs", "10"),
            ("inv", "inv_net_cash_cfs", "-2"),
            ("fin", "fin_net_cash_cfs", "-3"),
            ("fx", "fx_effect_cfs", "0"),
            ("change", "net_increase_cfs", "5"),
        ]
        facts = {fact_id: _fact(fact_id, key, value) for fact_id, key, value in values}
        result = calculate({"operation": "cash_bridge", "name": "cash bridge", "fact_ids": list(facts)}, facts)
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result["value"], "0")

    def test_cashflow_metric_keys_from_annual_report_are_accepted(self) -> None:
        cash_bridge_facts = {
            "op": _fact("op", "operating_net_cash", "10"),
            "inv": _fact("inv", "investing_net_cash", "-2"),
            "fin": _fact("fin", "financing_net_cash", "-3"),
            "fx": _fact("fx", "fx_effect", "0"),
            "change": _fact("change", "net_increase_cash", "5"),
        }
        cash_bridge = calculate(
            {"operation": "cash_bridge", "name": "cash bridge", "fact_ids": list(cash_bridge_facts)},
            cash_bridge_facts,
        )
        self.assertEqual(cash_bridge["status"], "calculated")
        self.assertEqual(cash_bridge["value"], "0")

        balance_facts = {
            "end": _fact("end", "ending_cash_equiv", "15", "2024年12月31日"),
            "begin": _fact("begin", "beginning_cash_equiv", "10", "2024年1月1日"),
            "change": _fact("change", "net_increase_cash", "5", "2024年度"),
        }
        balance_facts["end"]["period_kind"] = "instant_amount"
        balance_facts["begin"]["period_kind"] = "instant_amount"
        balance = calculate(
            {"operation": "cash_balance_bridge", "name": "cash balance", "fact_ids": list(balance_facts)},
            balance_facts,
        )
        self.assertEqual(balance["status"], "calculated")
        self.assertEqual(balance["value"], "0")

        reconciliation_facts = {
            "profit": _fact("profit", "recon_net_profit", "20"),
            "adjustment": _fact("adjustment", "recon_depreciation", "2"),
            "operating": _fact("operating", "operating_net_cash", "22"),
        }
        reconciliation = calculate(
            {"operation": "profit_to_cash", "name": "profit to cash", "fact_ids": list(reconciliation_facts)},
            reconciliation_facts,
        )
        self.assertEqual(reconciliation["status"], "calculated")
        self.assertEqual(reconciliation["value"], "0")

    def test_directional_net_requires_separate_inflow_and_outflow_lists(self) -> None:
        facts = {
            "in": _fact("in", "sales_receipts", "100"),
            "out": _fact("out", "purchase_payments", "40"),
        }
        result = calculate(
            {
                "operation": "net_by_direction",
                "name": "cash net",
                "inflow_fact_ids": ["in"],
                "outflow_fact_ids": ["out"],
            },
            facts,
        )
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result["value"], "60")

    def test_operating_after_capex_accepts_annual_report_metric_aliases(self) -> None:
        facts = {
            "operating": _fact("operating", "operating_net_cash", "21739740393.38"),
            "capex_a": _fact("capex_a", "capex_assets", "3978318320.96"),
            "capex_b": _fact("capex_b", "purchase_long_term_assets", "3978318320.96"),
        }
        facts["capex_a"]["original_label"] = "购建长期资产支出"
        facts["capex_b"]["original_label"] = "购建长期资产支出"
        facts["operating"]["original_label"] = "经营活动产生的现金流量净额"
        result = calculate(
            {"operation": "operating_after_capex", "name": "现金减资本开支", "fact_ids": ["operating", "capex_a"]},
            facts,
        )
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result["value"], "17761422072.42")

    def test_operating_after_capex_is_precomputed_once_from_unambiguous_facts(self) -> None:
        facts = {
            "operating": _fact("operating", "operating_net_cash", "21739740393.38"),
            "capex_a": _fact("capex_a", "capex_assets", "3978318320.96"),
            "capex_b": _fact("capex_b", "purchase_long_term_assets", "3978318320.96"),
        }
        for fact_id in ("capex_a", "capex_b"):
            facts[fact_id]["original_label"] = "购建长期资产支出"
        facts["operating"]["original_label"] = "经营活动产生的现金流量净额"
        state = {"facts": facts, "calculations": {}, "checkpoint": 0, "observed_pages": {1: "来源页"}}

        generated = _precompute_operating_after_capex(_Context(), state, {})

        self.assertEqual(generated, 1)
        calculation = next(iter(state["calculations"].values()))
        self.assertEqual(calculation["operation"], "operating_after_capex")
        self.assertEqual(calculation["value"], "17761422072.42")
        self.assertEqual(_precompute_operating_after_capex(_Context(), state, {}), 0)

    def test_rejected_amount_returns_candidate_source_ids_for_manual_selection(self) -> None:
        fact = _fact("f1", "sales_receipts", "100")
        state = {
            "facts": {"f1": fact},
            "calculations": {
                "calc1": {
                    "calculation_id": "calc1",
                    "status": "calculated",
                    "operation": "difference",
                    "unit": "CNY_yuan",
                    "value": "100",
                    "details": {},
                    "fact_ids": ["f1"],
                }
            },
        }
        hints = _reference_hints_for_amount("销售收现增加100元。", "100元", state)
        self.assertEqual(hints["candidate_fact_ids"], ["f1"])
        self.assertEqual(hints["candidate_calculation_ids"], ["calc1"])

    def test_rejected_submission_lists_the_exact_invalid_reference_ids(self) -> None:
        fact = _fact("f1", "cash_flow_value", "10")
        state = {
            "facts": {"f1": fact},
            "calculations": {},
            "checkpoint": 0,
            "observed_pages": {1: "合并现金流量表 2024年度 现金流净额 10元"},
        }
        section = _section("cash_overview", "missing-fact")
        section["calculation_ids"] = ["missing-calculation"]

        rejected = _submit(_args([section]), state, _Context())

        expected = {
            "sections": {
                "cash_overview": {
                    "fact_ids": ["missing-fact"],
                    "calculation_ids": ["missing-calculation"],
                },
            },
            "topics": [],
            "overall": {"fact_ids": [], "calculation_ids": []},
            "fact_evidence": {},
        }
        self.assertEqual(rejected["invalid_references"], expected)
        self.assertEqual(state["last_rejected_submission"]["invalid_references"], expected)

    def test_mismatched_row_identity_rejects_only_that_section(self) -> None:
        fact = _fact("f1", "cash_flow_value", "10")
        state = {
            "facts": {"f1": fact},
            "calculations": {},
            "checkpoint": 0,
            "observed_pages": {1: "合并现金流量表 2024年度 现金流净额 10元"},
        }
        context = _Context()
        first = _submit(_args([_section("cash_overview")]), state, context)
        self.assertFalse(first["accepted"])
        self.assertIn("cash_overview", state["accepted_sections"])

        fact["evidence"][0]["row_label_matches_quote"] = False
        retry = _submit(_args([_section("cash_overview")]), state, context)
        self.assertFalse(retry["accepted"])
        self.assertNotIn("cash_overview", state["accepted_sections"])
        self.assertTrue(retry["section_errors"]["cash_overview"])
        self.assertEqual(
            retry["invalid_references"]["fact_evidence"]["cash_overview"]["f1"],
            ["申报行名与引用摘录不匹配"],
        )

    def test_unmatched_column_or_table_heading_remains_a_review_flag(self) -> None:
        fact = _fact("f1", "cash_flow_value", "10")
        fact["evidence"][0]["column_label_matches_page"] = False
        fact["evidence"][0]["table_label_matches_page"] = False
        state = {
            "facts": {"f1": fact},
            "calculations": {},
            "checkpoint": 0,
            "observed_pages": {1: "合并现金流量表 2024年度 现金流净额 10元"},
        }
        result = _submit(_args([_section("cash_overview")]), state, _Context())
        self.assertFalse(result["accepted"])  # The other four boards are still pending.
        self.assertIn("cash_overview", state["accepted_sections"])
        self.assertTrue(any("列名未出现在来源页" in flag for flag in state["accepted_section_review_flags"]["cash_overview"]))

    def test_topic_with_unknown_fact_id_cannot_finalize_analysis(self) -> None:
        fact = _fact("f1", "cash_flow_value", "10")
        state = {
            "facts": {"f1": fact},
            "calculations": {},
            "checkpoint": 0,
            "observed_pages": {1: "合并现金流量表 2024年度 现金流净额 10元"},
        }
        context = _Context()
        sections = [_section(key) for key in (
            "cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash"
        )]
        staged = _submit(_args(sections), state, context)
        self.assertFalse(staged["accepted"])

        bad_topic_args = _args([], "五个板块齐备。")
        bad_topic_args["topics"] = [{
            "title": "异常专题", "summary": "已完成。", "detail": "", "fact_ids": ["unknown"],
            "calculation_ids": [], "source_pages": [],
        }]
        rejected = _submit(bad_topic_args, state, context)
        self.assertFalse(rejected["accepted"])
        self.assertTrue(rejected["topic_errors"])
        self.assertNotIn("final_result", state)

    def test_overall_unknown_reference_cannot_finalize_analysis(self) -> None:
        fact = _fact("f1", "cash_flow_value", "10")
        state = {
            "facts": {"f1": fact},
            "calculations": {},
            "checkpoint": 0,
            "observed_pages": {1: "合并现金流量表 2024年度 现金流净额 10元"},
        }
        context = _Context()
        sections = [_section(key) for key in (
            "cash_overview", "profit_to_cash", "operating_cash", "investment_cash", "financing_cash"
        )]
        _submit(_args(sections), state, context)

        bad_overall = _args([], "五个板块已完成。")
        bad_overall["overall_fact_ids"] = ["unknown"]
        rejected = _submit(bad_overall, state, context)
        self.assertFalse(rejected["accepted"])
        self.assertTrue(rejected["overall_errors"])
        self.assertNotIn("final_result", state)
