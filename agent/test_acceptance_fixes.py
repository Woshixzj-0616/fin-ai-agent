"""独立验收返修回归：原文约束、原子对齐、期间门控、质量与恢复。"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.claim_check import check_one_claim
from audit.service import check_text
from audit.tools.compare import compare_companies
from audit.tools.compute import compute_trend
from eval_harness import ReplayClient, run_full_flow, score_atomic
from backend_runtime import load_config
from materials import Run
from periods import comparability
from test_upgrade import fact, protocol_item

BASIS = {"accounting_policy_version": "v1", "consolidation_basis": "consolidated",
         "restatement_version": "none"}


class AtomicAlignmentTests(unittest.TestCase):
    sentence = "营业收入为100元，归母净利润为50元。"

    def rows(self):
        return [
            {"claim_id": "a", "sentence_id": 1, "original_sentence": self.sentence,
             "quote": "营业收入为100元", "status": "证据支持", "track": "deterministic"},
            {"claim_id": "b", "sentence_id": 1, "original_sentence": self.sentence,
             "quote": "归母净利润为50元", "status": "确认错误", "track": "deterministic"},
        ]

    def gold(self, second_error=True):
        return [
            {"id": "a", "sentence_id": 1, "quote": "营业收入为100元",
             "expect": {"kind": "judge", "error": False}},
            {"id": "b", "sentence_id": 1, "quote": "归母净利润为50元",
             "expect": {"kind": "judge", "error": second_error}},
        ]

    def score(self, rows, gold=None):
        return score_atomic({"status": "completed", "checks": rows}, gold or self.gold())

    def test_each_atom_matches_its_own_row(self):
        out = self.score(self.rows())
        self.assertEqual((out["summary"]["correct"], out["summary"]["wrong"]), (2, 0))
        self.assertEqual([r["status"] for r in out["rows"]], ["证据支持", "确认错误"])

    def test_omitted_second_atom_cannot_reuse_first_row(self):
        out = self.score(self.rows()[:1], self.gold(False))
        self.assertEqual((out["summary"]["correct"], out["summary"]["omitted"]), (1, 1))

    def test_swapping_result_order_does_not_change_score(self):
        self.assertEqual(self.score(self.rows()[::-1])["summary"]["correct"], 2)

    def test_unrelated_same_sentence_result_is_not_a_match(self):
        row = {**self.rows()[0], "quote": "加权净资产收益率为10%"}
        self.assertEqual(self.score([row])["summary"]["omitted"], 2)

    def test_duplicate_and_extra_predictions_are_visible(self):
        duplicate = {**self.rows()[0], "claim_id": "duplicate"}
        out = self.score([*self.rows(), duplicate])
        self.assertGreaterEqual(out["summary"]["extra_predictions"], 1)
        self.assertLessEqual(out["summary"]["correct"], 2)

    def test_full_sentence_quote_cannot_prove_two_atoms(self):
        row = {**self.rows()[0], "quote": self.sentence}
        out = self.score([row], self.gold(False))
        self.assertEqual(out["summary"]["omitted"], 2)

    def test_error_detection_metrics_use_confusion_counts(self):
        rows = self.rows()
        rows[1]["status"] = "证据支持"
        out = self.score(rows)
        summary = out["summary"]
        self.assertEqual((summary["tp"], summary["fp"], summary["tn"], summary["fn"]), (0, 0, 1, 1))
        self.assertEqual(summary["accuracy"], 0.5)
        self.assertIsNone(summary["precision"])
        self.assertEqual(summary["recall"], 0)

    def test_same_span_duplicates_require_unique_alignment(self):
        out = self.score([self.rows()[0], copy.deepcopy(self.rows()[0])], self.gold(False)[:1])
        self.assertEqual(out["summary"]["omitted"], 1)
        self.assertEqual(out["summary"]["extra_predictions"], 2)

    def test_field_scoring_counts_extra_prediction(self):
        from scripts.eval.run_llm_eval import field_rows, gold_items_for
        sentence = {"id": "f1", "text": "2024年贵州茅台营业收入为100元。",
                    "company_name": "贵州茅台", "period_year": 2024, "metric": "revenue",
                    "metric_text": "营业收入", "value": "100", "unit": "元",
                    "operator": "eq", "claim_type": "amount", "kind": "amount", "gold": "should_judge"}
        item = gold_items_for(sentence, 1)[0]
        extra = {**item, "claim_id": "extra", "quote": "利润为999元", "value": "999"}
        out = field_rows({"parsed": {"items": [item, extra]}}, {"sentences": [sentence]})
        self.assertEqual(out["extra_predictions"], 1)
        self.assertLess(out["field_avg"], 1)

    def test_field_scoring_supports_all_gold_atoms(self):
        from scripts.eval.run_llm_eval import field_rows, gold_items_for
        sentence = {"id": "f2", "text": self.sentence, "atoms": [
            {"id": "a", "text": "营业收入为100元", "company_name": "贵州茅台",
             "period_year": 2024, "metric": "revenue", "metric_text": "营业收入",
             "value": "100", "unit": "元", "operator": "eq", "claim_type": "amount", "kind": "amount"},
            {"id": "b", "text": "归母净利润为50元", "company_name": "贵州茅台",
             "period_year": 2024, "metric": "parent_net_profit", "metric_text": "归母净利润",
             "value": "50", "unit": "元", "operator": "eq", "claim_type": "amount", "kind": "amount"}]}
        items = gold_items_for(sentence, 1)
        self.assertEqual(len(items), 2)
        out = field_rows({"parsed": {"items": items[::-1]}}, {"sentences": [sentence]})
        self.assertEqual((out["exact_match"], out["total"]), (2, 2))


class GroundingConflictTests(unittest.TestCase):
    def setUp(self):
        self.mt = fact(evidence_id="mt", value="100", normalized_value="100")
        self.wly = fact(company_code="000858", company_name="五粮液", evidence_id="wly",
                        document_id="wly-doc", value="80", normalized_value="80")

    def test_quote_company_wins_over_other_company_in_sentence(self):
        sentence = "2024年贵州茅台营业收入为80元，五粮液营业收入为80元。"
        item = protocol_item(quote="贵州茅台营业收入为80元", company_name="五粮液", value="80")
        row = check_one_claim(item, [self.mt, self.wly], sentence, sentence, "g1")
        self.assertEqual(row["grounding"]["company"]["code"], "600519")
        self.assertEqual(row["status"], "确认错误")
        self.assertEqual(row["evidence_ids"], ["mt"])

    def test_multi_company_full_quote_cannot_choose_model_name_alone(self):
        sentence = "2024年贵州茅台营业收入为80元，五粮液营业收入为80元。"
        row = check_one_claim(protocol_item(quote=sentence, company_name="五粮液", value="80"),
                              [self.mt, self.wly], sentence, sentence, "g2")
        self.assertEqual(row["reason_code"], "ambiguous_company_in_sentence")

    def test_explicit_year_cannot_be_overridden_by_context(self):
        sentence = "2024年贵州茅台营业收入为80元。"
        context = "2023年贵州茅台营业收入为80元。"
        old = fact(evidence_id="old", period_year=2023, report_year=2023,
                   period_start="2023-01-01", period_end="2023-12-31", value="80")
        row = check_one_claim(protocol_item(quote=sentence, period_year=2023,
                                             context_quote=context, value="80"),
                              [self.mt, old], sentence, context + "\n" + sentence, "g3")
        self.assertEqual(row["reason_code"], "model_parse_conflict")
        self.assertEqual(row["grounding"]["period"]["year"], 2024)

    def test_quote_year_precedes_other_year_in_same_sentence(self):
        sentence = "2023年营业收入为80元，2024年贵州茅台营业收入为100元。"
        row = check_one_claim(protocol_item(quote="2024年贵州茅台营业收入为100元"),
                              [self.mt], sentence, sentence, "g4")
        self.assertEqual(row["status"], "证据支持")
        self.assertEqual(row["grounding"]["period"]["year"], 2024)

    def test_comparison_year_must_be_grounded(self):
        sentence = "2024年贵州茅台营业收入高于五粮液。"
        row = check_one_claim(protocol_item(quote=sentence, claim_type="comparison",
                                             company_b="五粮液", comparison_operator="exceed",
                                             period_year=2023),
                              [self.mt, self.wly], sentence, sentence, "g5")
        self.assertNotEqual(row["status"], "证据支持")
        self.assertEqual(row["reason_code"], "model_parse_conflict")


class DirectionAndPeriodTests(unittest.TestCase):
    def setUp(self):
        self.mt = fact(evidence_id="mt", value="100", normalized_value="100")
        self.wly = fact(company_code="000858", company_name="五粮液", evidence_id="wly",
                        document_id="wly-doc", value="80", normalized_value="80")

    def test_model_direction_cannot_override_decline(self):
        sentence = "2024年贵州茅台营业收入下降。"
        current = {**self.mt, "value": "120"}
        previous = {**self.mt, "evidence_id": "previous", "period_year": 2023,
                    "period_start": "2023-01-01", "period_end": "2023-12-31", "value": "100"}
        row = check_one_claim(protocol_item(quote=sentence, claim_type="direction",
                                             direction="up", value=None, unit=None, operator="none"),
                              [current, previous], sentence, sentence, "d1")
        self.assertEqual(row["status"], "确认错误")
        self.assertEqual(row["expected"], "增长")

    def test_negated_direction_never_affirms_bare_direction(self):
        sentence = "2024年贵州茅台营业收入未增长。"
        previous = {**self.mt, "evidence_id": "previous", "period_year": 2023,
                    "period_start": "2023-01-01", "period_end": "2023-12-31", "value": "80"}
        row = check_one_claim(protocol_item(quote=sentence, claim_type="direction",
                                             direction="up", value=None, unit=None, operator="none"),
                              [self.mt, previous], sentence, sentence, "d2")
        self.assertNotEqual(row["status"], "证据支持")

    def test_comparison_operator_must_agree_with_quote(self):
        sentence = "2024年贵州茅台营业收入低于五粮液。"
        row = check_one_claim(protocol_item(quote=sentence, claim_type="comparison",
                                             company_b="五粮液", comparison_operator="exceed"),
                              [self.mt, self.wly], sentence, sentence, "d3")
        self.assertEqual(row["reason_code"], "model_parse_conflict")

    def test_half_year_conflict_cannot_fall_back_to_annual(self):
        sentence = "2024年上半年贵州茅台营业收入高于五粮液。"
        half_mt = {**self.mt, "evidence_id": "mt-h1", "period_kind": "half",
                   "period_end": "2024-06-30", "duration_months": 6, "value": "50"}
        half_wly = {**self.wly, "evidence_id": "wly-h1", "period_kind": "half",
                    "period_end": "2024-06-30", "duration_months": 6, "value": "100"}
        row = check_one_claim(protocol_item(quote=sentence, claim_type="comparison",
                                             company_b="五粮液", comparison_operator="exceed"),
                              [self.mt, self.wly, half_mt, half_wly], sentence, sentence, "d4")
        self.assertEqual(row["reason_code"], "model_parse_conflict")

    def test_single_quarter_and_cumulative_are_not_comparable(self):
        a = {**self.mt, "period_kind": "quarter", "period_start": "2024-07-01",
             "period_end": "2024-09-30", "duration_months": 3}
        b = {**self.wly, "period_kind": "quarter", "period_start": "2024-01-01",
             "period_end": "2024-09-30", "duration_months": 9}
        out = compare_companies([a, b], company_a="600519", company_b="000858",
                                metric="revenue", period_year=2024, operator="exceed")
        self.assertEqual(out["reason_code"], "period_mismatch")
        self.assertEqual(out["verdict"], "needs_review")

    def test_comparison_refuses_explicit_review_restriction(self):
        b = {**self.wly, "auto_usable": False}
        out = compare_companies([self.mt, b], company_a="600519", company_b="000858",
                                metric="revenue", period_year=2024, operator="exceed")
        self.assertEqual(out["verdict"], "needs_review")

    def test_correct_half_year_comparison_is_preserved(self):
        facts = [{**f, "period_kind": "half", "period_end": "2024-06-30", "duration_months": 6}
                 for f in (self.mt, self.wly)]
        sentence = "2024年上半年贵州茅台营业收入高于五粮液。"
        row = check_one_claim(protocol_item(quote=sentence, period_kind="half",
                                             claim_type="comparison", company_b="五粮液",
                                             comparison_operator="exceed"),
                              facts, sentence, sentence, "d5")
        self.assertEqual(row["status"], "证据支持")


class TrendGateTests(unittest.TestCase):
    def series(self, declared=False):
        facts = []
        for year, value in ((2022, "100"), (2023, "120"), (2024, "150")):
            facts.append(fact(evidence_id=str(year), period_year=year, report_year=year,
                              document_id=f"doc-{year}", comparison_group=f"table-{year}",
                              source_sha256=str(year) * 16, period_start=f"{year}-01-01",
                              period_end=f"{year}-12-31", value=value, normalized_value=value,
                              **(BASIS if declared else {})))
        return facts

    def trend(self, facts, **kw):
        return compute_trend(facts, company_name_or_code="600519", metric="revenue",
                             start_year=2022, end_year=2024, **kw)

    def test_requested_half_cannot_return_annual(self):
        out = self.trend(self.series(True), report_kind="half")
        self.assertNotEqual(out["status"], "ok")

    def test_missing_material_cannot_fall_back(self):
        out = self.trend(self.series(True), document_id="missing")
        self.assertNotEqual(out["status"], "ok")

    def test_unknown_cross_report_basis_blocks_trend(self):
        out = self.trend(self.series())
        self.assertEqual(out["status"], "not_comparable")
        self.assertTrue(out["blocked_comparisons"])
        self.assertIsNone(out.get("cagr_pct"))
        self.assertEqual(len(out["series"]), 3)

    def test_review_restriction_blocks_trend(self):
        facts = self.series(True)
        facts[1]["auto_usable"] = False
        out = self.trend(facts)
        self.assertEqual(out["status"], "not_comparable")

    def test_basis_conflict_blocks_trend(self):
        facts = self.series(True)
        facts[1]["accounting_policy_version"] = "v2"
        self.assertEqual(self.trend(facts)["status"], "not_comparable")

    def test_declared_basis_preserves_calculation(self):
        out = self.trend(self.series(True))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["cagr_pct"], "22.47")
        self.assertTrue(out["monotonic_up"])

    def test_shared_table_label_cannot_hide_distinct_documents(self):
        facts = self.series()
        a, b = facts[:2]
        b["comparison_group"] = a["comparison_group"]
        self.assertEqual(comparability(a, b)["status"], "unknown")

    def test_negative_base_uses_existing_review_policy(self):
        facts = self.series(True)
        facts[0]["value"] = "-100"
        out = self.trend(facts)
        self.assertNotEqual(out["status"], "ok")


class ToolFailureRecoveryTests(unittest.TestCase):
    def run_case(self, root, *, same_sentence=False):
        mt = fact(evidence_id="mt")
        wly = fact(company_code="000858", company_name="五粮液", evidence_id="wly",
                    document_id="doc-wly", value="80", normalized_value="80")
        first = "2024年贵州茅台营业收入为100元。"
        second = "2024年贵州茅台营业收入高于五粮液。"
        draft = first + ("\n" if not same_sentence else "") + second
        items = [protocol_item(claim_id="good", sentence_id=1, quote=first),
                 protocol_item(claim_id="bad", sentence_id=2, quote=second,
                               claim_type="comparison", company_b="五粮液",
                               comparison_operator="exceed")]
        run = Run(root, "repair-recovery", {})
        config = copy.deepcopy(load_config())
        config["document"]["chunk_sentences"] = 1 if not same_sentence else 10
        client = ReplayClient(items=items)
        with patch("audit.tools.compare.compare_companies", side_effect=TypeError("injected failure")):
            bundle = run_full_flow(draft, [mt, wly], run, client, use_loop=False, config=config)
        return bundle, run, client, [mt, wly], config

    def test_tool_error_marks_partial_and_retains_completed_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, run, _client, _facts, _config = self.run_case(Path(temp))
            self.assertEqual(bundle["status"], "partial")
            self.assertEqual(bundle["unfinished_sentence_ids"], [2])
            self.assertEqual(bundle["checks"][0]["status"], "证据支持")
            self.assertEqual(bundle["failed_chunks"][0]["error_type"], "TypeError")
            self.assertTrue(run.output("text_checks.json").is_file())

    def test_resume_retries_failed_item_and_reuses_completed_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, run, client, facts, config = self.run_case(Path(temp))
            calls = len(client.calls)
            retried = check_text(run.output("eval_draft.txt"), facts, run, client,
                                 use_loop=False, resume=True, config=config)
            self.assertEqual(len(client.calls) - calls, 1)
            self.assertEqual(retried["status"], "completed")
            self.assertTrue(all(r["status"] == "证据支持" for r in retried["checks"]))

    def test_successful_rows_survive_failure_in_same_chunk(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, _run, _client, _facts, _config = self.run_case(Path(temp), same_sentence=True)
            self.assertEqual(bundle["status"], "partial")
            self.assertEqual(bundle["checks"][0]["status"], "证据支持")
            self.assertEqual(bundle["unfinished_sentence_ids"], [2])

    def test_eval_reports_tool_failure_not_abstention(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, _run, _client, _facts, _config = self.run_case(Path(temp))
            out = score_atomic(bundle, [
                {"id": "good", "sentence_id": 1, "quote": bundle["sentences"][0]["text"],
                 "expect": {"kind": "judge", "error": False}},
                {"id": "bad", "sentence_id": 2, "quote": bundle["sentences"][1]["text"],
                 "expect": {"kind": "judge", "error": False}}])
            self.assertEqual((out["summary"]["correct"], out["summary"]["failed"],
                              out["summary"]["abstain"]), (1, 1, 0))



class RecoveryBoundaryTests(unittest.TestCase):
    def build(self, root):
        sentence = "2024年贵州茅台营业收入为100元，贵州茅台营业收入高于五粮液。"
        quotes = ["2024年贵州茅台营业收入为100元", "贵州茅台营业收入高于五粮液"]
        items = [protocol_item(claim_id="good", sentence_id=1, quote=quotes[0]),
                 protocol_item(claim_id="bad", sentence_id=1, quote=quotes[1],
                               claim_type="comparison", company_b="五粮液", comparison_operator="exceed")]
        facts = [fact(evidence_id="mt"), fact(company_code="000858", company_name="五粮液",
                  evidence_id="wly", document_id="doc-wly", value="80", normalized_value="80")]
        run, client = Run(root, "same-sentence-retry", {}), ReplayClient(items=items)
        config = copy.deepcopy(load_config())
        with patch("audit.tools.compare.compare_companies", side_effect=TypeError("injected")):
            bundle = run_full_flow(sentence, facts, run, client, use_loop=False, config=config)
        return bundle, run, client, facts, config, quotes

    def test_true_same_sentence_failure_keeps_success_in_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, _run, _client, _facts, _config, quotes = self.build(Path(temp))
            self.assertEqual(bundle["unfinished_sentence_ids"], [1])
            scored = score_atomic(bundle, [
                {"id": str(i), "sentence_id": 1, "quote": quote,
                 "expect": {"kind": "judge", "error": False}} for i, quote in enumerate(quotes)])
            self.assertEqual((scored["summary"]["correct"], scored["summary"]["failed"]), (1, 1))

    def test_same_sentence_resume_only_executes_failed_atom(self):
        from audit.claim_check import check_one_claim as original
        with tempfile.TemporaryDirectory() as temp:
            bundle, run, client, facts, config, quotes = self.build(Path(temp))
            good = copy.deepcopy(bundle["checks"][0])
            visited = []
            def checker(item, *args):
                visited.append(item["quote"])
                if item["quote"] == quotes[0]:
                    raise RuntimeError("completed atom must not run again")
                return original(item, *args)
            with patch("audit.claim_check.check_one_claim", side_effect=checker):
                result = check_text(run.output("eval_draft.txt"), facts, run, client,
                                    use_loop=False, resume=True, config=config)
            self.assertEqual(visited, quotes[1:])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["checks"][0], good)
            self.assertEqual(len({r["claim_id"] for r in result["checks"]}), len(result["checks"]))
            self.assertEqual(len(result["parsed"]["items"]), 2)

    def test_failed_retry_retains_original_success(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, run, client, facts, config, _quotes = self.build(Path(temp))
            client.error_at = len(client.calls) + 1
            result = check_text(run.output("eval_draft.txt"), facts, run, client,
                                use_loop=False, resume=True, config=config)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["checks"][0], bundle["checks"][0])
            self.assertEqual(result["failed_chunks"][0]["error_type"], "LLMError")

    def test_same_chunk_resume_requests_only_failed_sentence(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle, run, client, facts, config = ToolFailureRecoveryTests().run_case(
                Path(temp), same_sentence=True)
            result = check_text(run.output("eval_draft.txt"), facts, run, client,
                                use_loop=False, resume=True, config=config)
            self.assertEqual(client.calls[-1]["sentence_ids"], [2])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["checks"][0], bundle["checks"][0])

    def test_unexpected_item_exception_does_not_erase_other_atom(self):
        from audit.claim_check import check_one_claim as original
        with tempfile.TemporaryDirectory() as temp:
            bundle, run, client, facts, config, quotes = self.build(Path(temp))
            def checker(item, *args):
                if item["quote"] == quotes[1]:
                    raise RuntimeError("unexpected")
                return original(item, *args)
            with patch("audit.claim_check.check_one_claim", side_effect=checker):
                result = run_full_flow(bundle["sentences"][0]["text"], facts, run,
                                       client, use_loop=False, config=config)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["checks"][0]["status"], "证据支持")
            self.assertEqual(result["failed_chunks"][0]["error_type"], "RuntimeError")

    def test_missing_evidence_is_completed_abstention(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Run(Path(temp), "normal-missing", {})
            sentence = "2024年贵州茅台营业收入高于五粮液。"
            item = protocol_item(quote=sentence, claim_type="comparison",
                                 company_b="五粮液", comparison_operator="exceed")
            bundle = run_full_flow(sentence, [fact()], run, ReplayClient(items=[item]), use_loop=False)
            self.assertEqual(bundle["status"], "completed")
            self.assertFalse(bundle["failed_chunks"])
            scored = score_atomic(bundle, [{"sentence_id": 1, "quote": sentence,
                                           "expect": {"kind": "judge", "error": False}}])
            self.assertEqual((scored["summary"]["abstain"], scored["summary"]["failed"]), (1, 0))


class FurtherPeriodAndEvalTests(unittest.TestCase):
    def test_arabic_quarter_number_keeps_actual_start(self):
        from audit.claim_check import _precise_period
        period = _precise_period(protocol_item(period_kind="quarter"), "2024年第1季度")
        self.assertEqual((period["period_start"], period["period_end"]),
                         ("2024-01-01", "2024-03-31"))

    def test_second_half_cannot_select_first_half(self):
        from audit.claim_check import _precise_period
        period = _precise_period(protocol_item(period_kind="half"), "2024年下半年")
        self.assertEqual((period["period_start"], period["period_end"]),
                         ("2024-07-01", "2024-12-31"))

    def test_annual_yoy_shared_label_cannot_hide_different_documents(self):
        from finance import evidence_yoy
        current = fact(evidence_id="cur", document_id="current", comparison_group="same-label")
        previous = fact(evidence_id="prev", document_id="previous", comparison_group="same-label",
                        period_year=2023, period_start="2023-01-01", period_end="2023-12-31",
                        value="80", normalized_value="80")
        self.assertEqual(evidence_yoy(current, previous)["status"], "not_comparable")

    def test_timeout_placeholder_is_scored_without_an_atom_quote(self):
        bundle = {"status": "partial", "unfinished_sentence_ids": [1], "checks": [
            {"sentence_id": 1, "original_sentence": "复合句", "reason_code": "execution_incomplete",
             "reason": "ExecutionLimit: 整篇时限"}]}
        gold = [{"sentence_id": 1, "quote": quote, "expect": {"kind": "judge", "error": False}}
                for quote in ("第一个原子", "第二个原子")]
        summary = score_atomic(bundle, gold)["summary"]
        self.assertEqual((summary["timeout"], summary["omitted"]), (2, 0))

    def test_report_file_filter_rejects_missing_sample(self):
        from scripts.eval.run_report_eval import select_samples
        with self.assertRaises(ValueError):
            select_samples([{"file": "known.md"}], "missing.md")

    def test_report_eval_explicit_output_does_not_touch_history(self):
        from scripts.eval.run_report_eval import main
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            with patch("scripts.eval.run_report_eval.load_answers", return_value={"samples": []}), \
                 patch("scripts.eval.run_report_eval.extract_selected", return_value=(None, [], None)):
                self.assertEqual(main(["--output-dir", str(out)]), 0)
            self.assertTrue((out / "report_eval.json").is_file())
            self.assertEqual(json.loads((out / "report_eval.json").read_text("utf-8"))["summary"]["total"], 0)


    def test_repeated_report_quote_is_not_assigned_to_first_sentence(self):
        from scripts.eval.run_report_eval import align_quotes
        sample = {"claims": [{"item": {"claim_id": "g", "quote": "相同的主张"}}]}
        aligned = align_quotes(sample, [{"sentence_id": 1, "text": "相同的主张。"},
                                         {"sentence_id": 2, "text": "相同的主张。"}])
        self.assertIsNone(aligned["g"][0])


if __name__ == "__main__":
    unittest.main()
