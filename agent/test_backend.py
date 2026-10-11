"""后端接入回归：跨入口证据限制、来源真实性及失败后的完整性。

HTTP由本地注入传输模拟，不使用真实凭证、答案或付费请求。
"""
from __future__ import annotations

import copy
import gzip
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from backend_runtime import (DEFAULTS, ExecutionBudget, ExecutionLimit, digest, load_config,
                             plan_chunks, sentence_rows, validate_config)
from extract_cache import cache_key
from finance import analyze, check_claim, compare_number, evidence_issues
from llm_check import LLMClient, LLMError, check_text
from llm_http import LLMHttp, TransportError, retry_after_seconds
from materials import Client, ROOT, Run, SafeRedirect, fetch, query_reports, register
from mcp_server import Session, call_tool
from periods import derive_quarter, growth
from qa import answer
from test import FakeClient, announcement
from test_competition import fact, text_pdf
from tools import compare_claim, find_evidence

KEY = "unit-test-credential"


def annual(**changes):
    return fact(period_kind="annual", duration_months=12, period_end="2025-12-31", **changes)


def claim_arguments():
    return dict(company_name_or_code="600519", metric="revenue", period_year=2025,
                source_report_year=2025, kind="amount", claimed_value="100", claimed_unit="元",
                operator="eq", scope="consolidated")


def attach_mock(client, handler):
    client._http.close()
    client._http._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def model_response(request, model="actual-model", tokens=13):
    payload = json.loads(request.content)
    submitted = json.loads(payload["messages"][-1]["content"])
    identifiers = [s["sentence_id"] for s in submitted["sentences"]]
    body = {"model": model, "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"items": [], "unclaimed_sentences": identifiers})}}],
        "usage": {"total_tokens": tokens}}
    return httpx.Response(200, json=body)


class EvidenceAccessTests(unittest.TestCase):
    def test_review_flags_block_calculation_qa_and_mcp(self):
        for flag in ({"auto_usable": False}, {"review_reasons": ["列位置待核实"]},
                     {"second_path_check": {"status": "mismatch"}}):
            with self.subTest(flag=flag):
                f = annual(**flag)
                output = compare_claim([f], **claim_arguments())
                self.assertNotEqual(output.get("verdict"), "evidence_supported")
                self.assertNotEqual(answer("贵州茅台2025年营业收入是多少？", [f]).get("status"), "ok")
                session = Session()
                session.facts = [f]
                self.assertNotEqual(call_tool(session, "compare_claim", claim_arguments()).get("verdict"),
                                    "evidence_supported")
                self.assertTrue(evidence_issues(f))

    def test_same_value_duplicate_cannot_remove_review_flag(self):
        clean = annual()
        restricted = {**clean, "evidence_id": "restricted", "auto_usable": False}
        out = find_evidence([clean, restricted], company_name_or_code="600519",
                            metric="revenue", period_year=2025)
        self.assertEqual(out["status"], "ambiguous")

    def test_qa_overview_and_issues_keep_extended_review_flags(self):
        clean = annual()
        restricted = annual(metric="operating_cash_flow", evidence_id="restricted",
                            auto_usable=False, value="999", normalized_value="999")
        restricted.pop("issues", None)
        overview = answer("指标概览", [clean, restricted], use_llm=False)
        self.assertEqual(overview["evidence_ids"], [clean["evidence_id"]])
        self.assertEqual(overview["flagged_count"], 1)
        self.assertNotIn("999", overview["answer"])
        self.assertEqual(answer("指标概览", [restricted], use_llm=False)["status"],
                         "insufficient_evidence")
        issues = answer("有哪些问题？", [restricted], use_llm=False)
        self.assertEqual(issues["flagged_count"], 1)
        self.assertIn("auto_usable_false", issues["answer"])

    def test_analysis_does_not_use_restricted_profit_for_nonrecurring_signal(self):
        profit = annual(metric="parent_net_profit", evidence_id="profit", auto_usable=False)
        adjusted = annual(metric="adjusted_parent_net_profit", evidence_id="adjusted", value="80",
                          normalized_value="80")
        out = analyze([profit, adjusted], Mock(event=lambda *a, **kw: None))
        self.assertFalse(any(s["type"] in {"nonrecurring_profit_share", "profit_minus_adjusted_profit"}
                             for s in out["signals"]))

    def test_explicit_policy_conflict_rejects_cross_report_growth(self):
        current = fact(accounting_policy_version="policy-2")
        previous = fact(period_year=2024, report_year=2024, period_start="2024-01-01",
                        period_end="2024-03-31", accounting_policy_version="policy-1")
        self.assertEqual(growth(current, previous)["status"], "not_comparable")

    def test_extended_balances_and_undefined_fields_are_not_subtracted(self):
        for metric in ("inventory", "accounts_receivable", "total_liabilities", "custom_metric"):
            with self.subTest(metric=metric):
                self.assertEqual(derive_quarter(fact(metric=metric))["status"], "not_comparable")
        self.assertEqual(derive_quarter(fact(period_attribute="instant"))["status"], "not_comparable")

    def test_fin_tolerance_remains_two_percent(self):
        self.assertEqual(compare_number("100", "103", operator="approx")["status"], "mismatch")
        self.assertEqual(compare_number("100", "101.9", operator="approx")["status"], "match")


class SourceControlTests(unittest.TestCase):
    def test_local_upload_does_not_invent_official_id_or_disclosure_date(self):
        blob = text_pdf("600519 贵州茅台 2024年年度报告\n财务报告示例")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = Run(root, "local-register", {})
            metadata = dict(company_code="600519", company_name="贵州茅台", report_year=2024,
                            announcement_id="987654", disclosed_at="2026-10-09",
                            disclosure_date_status="onsite_unverified", source_url="webui://example")
            first = register(root, blob, metadata, run)
            second = register(root, blob, metadata, run)
            self.assertIsNone(first["announcement_id"])
            self.assertIsNone(first["disclosed_at"])
            self.assertEqual(first["disclosure_precision"], "unknown")
            self.assertTrue(first["local_import_id"].startswith("LOCAL-"))
            self.assertEqual(first["document_id"], second["document_id"])
            self.assertEqual(first["first_ingested_at"], second["first_ingested_at"])

    def test_fetch_preserves_date_precision_and_selection_receipt(self):
        blob = text_pdf("600519 贵州茅台 2024年年度报告\n财务报告示例")
        selected = announcement(123, "2024年年度报告")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = Run(root, "source-fetch-test", {})
            with patch("materials.Client") as client, patch("materials.company", return_value={
                    "orgId": "org", "zwjc": "贵州茅台"}), patch("materials.query_reports", return_value=[selected]):
                client.return_value.request.return_value = blob
                record = fetch(root, run, "600519", 2024, "latest", date(2026, 10, 9))
            self.assertEqual(record["disclosure_precision"], "date")
            self.assertEqual(record["disclosed_at"], "2025-04-03")
            self.assertIn("retrieved_at", record)
            self.assertFalse(record["version_selection"]["effective_version_confirmed"])
            self.assertTrue((root / "results/version_selection.json").is_file())

    def test_duplicate_announcement_with_changed_metadata_is_not_hidden(self):
        first = announcement(1, "2024年年度报告")
        other = {**first, "adjunctUrl": "finalpage/changed.pdf"}
        client = FakeClient([{"announcements": [first], "totalAnnouncement": 2, "hasMore": True},
                             {"announcements": [other], "totalAnnouncement": 2, "hasMore": False}])
        with self.assertRaisesRegex(ValueError, "同一公告ID"):
            query_reports(client, "600519", "org", 2024, date(2026, 10, 9))

    def test_cache_invalidates_when_only_source_version_metadata_changes(self):
        item = {"sha256": "abc", "document_id": "doc", "disclosed_at": "2025-04-03"}
        self.assertNotEqual(cache_key(item), cache_key({**item, "disclosed_at": "2025-04-04"}))
        self.assertNotEqual(cache_key(item), cache_key({**item, "needs_version_review": True}))

    def test_source_401_does_not_retry_or_save_error_body(self):
        run = Mock()
        config = copy.deepcopy(DEFAULTS)
        config["sources"]["min_interval_milliseconds"] = 0
        client = Client(run, config=config)
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError(
            "https://www.cninfo.com.cn/test", 401, "unauthorized", {}, io.BytesIO(KEY.encode()))
        with self.assertRaisesRegex(ValueError, "401"):
            client.request("https://www.cninfo.com.cn/test")
        self.assertEqual(client.opener.open.call_count, 1)
        self.assertNotIn(KEY, str(run.mock_calls))

    def test_gzip_is_decoded_and_retry_after_is_bounded(self):
        class Response(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}
            status = 200
            def geturl(self):
                return "https://www.cninfo.com.cn/test"

        config = copy.deepcopy(DEFAULTS)
        config["sources"]["min_interval_milliseconds"] = 0
        client = Client(Mock(), config=config)
        response = Response(gzip.compress(b'{"ok":true}'))
        error = urllib.error.HTTPError("https://www.cninfo.com.cn/test", 429, "limit",
                                      {"Retry-After": "99999"}, io.BytesIO())
        client.opener = Mock()
        client.opener.open.side_effect = [error, response]
        with patch("materials.time.sleep") as sleep:
            self.assertEqual(client.request("https://www.cninfo.com.cn/test"), b'{"ok":true}')
        self.assertEqual(sleep.call_args.args[0], 30)
        self.assertEqual(client._physical, 2)

    def test_gzip_expansion_is_limited(self):
        class Response(io.BytesIO):
            headers = {"Content-Encoding": "gzip"}
            status = 200
            def geturl(self):
                return "https://www.cninfo.com.cn/test"
        config = copy.deepcopy(DEFAULTS)
        config["sources"]["max_bytes"] = 1024
        client = Client(Mock(), config=config)
        client.opener = Mock()
        client.opener.open.return_value = Response(gzip.compress(b"x" * 100000))
        with self.assertRaises(ValueError):
            client.request("https://www.cninfo.com.cn/test")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_unapproved_redirect_is_rejected_before_next_request(self):
        before = Mock()
        handler = SafeRedirect(before)
        request = urllib.request.Request("https://www.cninfo.com.cn/test")
        with self.assertRaises(ValueError):
            handler.redirect_request(request, None, 302, "Found", {}, "https://example.invalid/secret")
        before.assert_not_called()


class ExecutionControlTests(unittest.TestCase):
    def test_config_rejects_unknown_fields_and_boolean_budgets(self):
        config = copy.deepcopy(DEFAULTS)
        config["tools"]["max_calls"] = True
        with self.assertRaises(ValueError):
            validate_config(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"document":{"imaginary":1}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_repeated_sentences_have_distinct_offsets_and_global_ids(self):
        config = copy.deepcopy(DEFAULTS)
        config["document"]["chunk_sentences"] = 1
        text = "一、收入\n营业收入100元。\n营业收入100元。"
        rows = sentence_rows(text, config)
        blocks = plan_chunks(text, rows, config)
        self.assertEqual([r["sentence_id"] for r in rows], [1, 2, 3])
        self.assertNotEqual(rows[1]["start"], rows[2]["start"])
        self.assertEqual([s["sentence_id"] for b in blocks for s in b["sentences"]], [1, 2, 3])
        self.assertEqual(blocks[-1]["heading"], "一、收入")

    def test_failure_then_resume_only_reexecutes_failed_block(self):
        config = copy.deepcopy(DEFAULTS)
        config["http"]["max_retries"] = 0
        config["document"]["chunk_sentences"] = 2
        calls = []
        def handler(request):
            submitted = json.loads(json.loads(request.content)["messages"][-1]["content"])
            ids = [s["sentence_id"] for s in submitted["sentences"]]
            calls.append(ids)
            if ids == [3, 4] and calls.count([3, 4]) == 1:
                return httpx.Response(503)
            return model_response(request)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "draft.txt"
            source.write_text("贵州茅台2025年营业收入100元。" * 4, encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY, config=config), handler)
            first = check_text(source, [annual()], Run(root, "first", {}), client, use_loop=False)
            self.assertEqual(first["status"], "partial")
            self.assertEqual(first["unfinished_sentence_ids"], [3, 4])
            restored = check_text(source, [annual()], Run(root, "resume", {}), client, use_loop=False, resume=True)
            self.assertEqual(restored["status"], "completed")
            self.assertEqual(calls, [[1, 2], [3, 4], [3, 4]])
            self.assertEqual(len(restored["execution"]["attempts"]), 3)
            self.assertEqual(restored["execution"]["known_tokens"], 26)
            self.assertEqual(restored["execution"]["unknown_attempts"], 1)
            self.assertEqual(len(restored["checks"]), 4)
            self.assertEqual([s["sentence_id"] for s in restored["sentences"]], [1, 2, 3, 4])
            saved = json.loads((root / "results" / first["checkpoint_file"]).read_text(encoding="utf-8"))
            self.assertEqual(len(saved["history"]), 1)
            self.assertTrue(all(KEY.encode() not in p.read_bytes() for p in (root / "results").iterdir()))
            client._http.close()

    def test_auth_failure_never_triggers_protocol_fallback_or_later_block_requests(self):
        config = copy.deepcopy(DEFAULTS)
        config["document"]["chunk_sentences"] = 1
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(401, text=KEY)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "draft.txt"
            source.write_text("贵州茅台2025年收入100元。" * 3, encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY, config=config), handler)
            result = check_text(source, [annual()], Run(root, "auth", {}), client)
            self.assertEqual(len(requests), 1)
            self.assertEqual(result["unfinished_sentence_ids"], [1, 2, 3])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result["checks"]), 3)
            self.assertTrue(all(KEY.encode() not in p.read_bytes() for p in (root / "results").iterdir()))
            client._http.close()

    def test_attempt_budget_preserves_all_unfinished_sentences(self):
        config = copy.deepcopy(DEFAULTS)
        config["document"].update(chunk_sentences=1, max_http_attempts=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("贵州茅台2025年收入100元。" * 3, encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY, config=config), model_response)
            out = check_text(path, [annual()], Run(root, "budget", {}), client, use_loop=False)
            self.assertEqual(len(out["execution"]["attempts"]), 1)
            self.assertEqual(out["unfinished_sentence_ids"], [2, 3])
            self.assertEqual(len(out["checks"]), 3)
            client._http.close()

    def test_draft_over_twelve_thousand_characters_and_forty_sentences_is_not_truncated(self):
        text = "贵州茅台2025年营业收入100元，应核对合并口径。" * 600
        self.assertGreater(len(text), 12000)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text(text, encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY), model_response)
            out = check_text(path, [annual()], Run(root, "long-draft", {}), client, use_loop=False)
            self.assertEqual(out["status"], "completed")
            self.assertEqual(len(out["sentences"]), 600)
            self.assertEqual(len(out["checks"]), 600)
            self.assertEqual(out["unfinished_sentence_ids"], [])
            self.assertTrue(all(len(chunk["sentence_ids"]) <= 40 for chunk in out["chunks"]))
            self.assertEqual((root / "results/checked_draft.txt").read_text(encoding="utf-8"), text)
            client._http.close()

    def test_token_budget_refuses_request_before_network_attempt(self):
        config = copy.deepcopy(DEFAULTS)
        config["document"]["token_budget"] = 1
        handler = Mock(side_effect=model_response)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("贵州茅台2025年收入100元。", encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY, config=config), handler)
            out = check_text(path, [annual()], Run(root, "tokens", {}), client, use_loop=False)
            handler.assert_not_called()
            self.assertEqual(out["unfinished_sentence_ids"], [1])
            self.assertEqual(out["execution"]["attempts"], [])
            client._http.close()

    def test_explicit_task_configuration_controls_actual_transport_and_restores_client(self):
        override = copy.deepcopy(DEFAULTS)
        override["http"]["max_retries"] = 0
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(503)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("贵州茅台2025年收入100元。", encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY), handler)
            out = check_text(path, [annual()], Run(root, "override", {}), client, use_loop=False, config=override)
            self.assertEqual(len(calls), 1)
            self.assertEqual(out["config"]["http"]["max_retries"], 0)
            self.assertEqual(client._http.max_retries, DEFAULTS["http"]["max_retries"])
            client._http.close()

    def test_returned_model_change_between_chunks_is_detected(self):
        config = copy.deepcopy(DEFAULTS)
        config["document"]["chunk_sentences"] = 1
        counter = []
        def handler(request):
            counter.append(1)
            return model_response(request, model="model-a" if len(counter) == 1 else "model-b")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("贵州茅台2025年收入100元。" * 2, encoding="utf-8")
            run = Run(root, "identity-change", {})
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY, config=config), handler)
            out = check_text(path, [annual()], run, client, use_loop=False)
            self.assertEqual(out["observed_models"], ["model-a", "model-b"])
            metadata = [json.loads(e) for e in run.events if json.loads(e)["event"] == "llm_usage"]
            self.assertFalse(metadata[0]["model_identity_changed"])
            self.assertTrue(metadata[1]["model_identity_changed"])
            client._http.close()

    def test_resume_rejects_changed_inputs_and_tampered_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("贵州茅台2025年收入100元。", encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY), model_response)
            out = check_text(path, [annual()], Run(root, "first", {}), client, use_loop=False)
            with self.assertRaises(LLMError):
                check_text(path, [annual(value="101")], Run(root, "different", {}), client, use_loop=False, resume=True)
            checkpoint = root / "results" / out["checkpoint_file"]
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            saved["budget"]["tokens_charged"] = 0
            checkpoint.write_text(json.dumps(saved), encoding="utf-8")
            with self.assertRaises(LLMError):
                check_text(path, [annual()], Run(root, "tampered", {}), client, use_loop=False, resume=True)
            client._http.close()

    def test_long_sentence_is_preserved_and_other_sentences_still_run(self):
        config = copy.deepcopy(DEFAULTS)
        config["document"]["chunk_characters"] = 30
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            text = "贵州茅台2025年" + "长文" * 50 + "。收入100元。"
            path.write_text(text, encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY, config=config), model_response)
            out = check_text(path, [annual()], Run(root, "long-sentence", {}), client, use_loop=False)
            self.assertEqual(out["unfinished_sentence_ids"], [1])
            self.assertEqual(len(out["execution"]["attempts"]), 1)
            self.assertEqual((root / "results/checked_draft.txt").read_text(encoding="utf-8"), text)
            client._http.close()

    def test_unknown_usage_reservation_survives_restore(self):
        config = copy.deepcopy(DEFAULTS)
        budget = ExecutionBudget(config, Mock())
        budget.before_attempt({"messages": []})
        budget.after_attempt(503, None, 0.1)
        restored = ExecutionBudget(config, Mock(), budget.snapshot())
        self.assertEqual(restored.tokens_charged, budget.tokens_charged)
        self.assertEqual(restored.unknown_attempts, 1)

    def test_model_identity_is_recorded_without_claiming_weight_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("贵州茅台2025年收入100元。", encoding="utf-8")
            run = Run(root, "identity", {})
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY), model_response)
            result = check_text(path, [annual()], run, client, use_loop=False)
            self.assertEqual(result["observed_models"], ["actual-model"])
            metadata = [json.loads(e) for e in run.events if json.loads(e)["event"] == "llm_usage"][0]
            self.assertFalse(metadata["requested_name_matches"])
            self.assertFalse(metadata["immutable_weights_verified"])
            client._http.close()


class TransportControlTests(unittest.TestCase):
    def test_redirect_is_terminal_even_without_httpx(self):
        with patch("llm_http.httpx", None):
            http = LLMHttp("https://example.invalid/v1/chat/completions", KEY)
            http._opener = Mock()
            http._opener.open.side_effect = urllib.error.HTTPError(
                http.url, 302, "Found", {"Location": "https://elsewhere.invalid"}, io.BytesIO())
            with self.assertRaises(TransportError):
                http.post_json({})
            self.assertEqual(http._opener.open.call_count, 1)

    def test_malformed_200_json_is_not_a_network_retry(self):
        http = LLMHttp("https://example.invalid/v1/chat/completions", KEY)
        http._post_once_json = Mock(return_value=(200, b"not-json"))
        with self.assertRaises(TransportError):
            http.post_json({})
        self.assertEqual(http._post_once_json.call_count, 1)
        http.close()

    def test_network_retry_attempts_and_usage_are_separate(self):
        http = LLMHttp("https://example.invalid/v1/chat/completions", KEY)
        http._post_once_json = Mock(side_effect=[(503, b""), (200, b'{"usage":{"total_tokens":13}}')])
        started, finished = [], []
        with patch("llm_http.time.sleep"):
            http.post_json({}, on_attempt=lambda body: started.append(body),
                           on_result=lambda status, body, elapsed: finished.append((status, body)))
        self.assertEqual(len(started), 2)
        self.assertEqual([status for status, _ in finished], [503, 200])
        http.close()

    def test_truncated_sse_does_not_invent_stop(self):
        http = LLMHttp("https://example.invalid/v1/chat/completions", KEY)
        with self.assertRaises(TransportError):
            http._accumulate_sse(['data: {"choices":[{"delta":{"content":"partial"}}]}'])
        http.close()

    def test_sse_preserves_returned_model_and_usage(self):
        http = LLMHttp("https://example.invalid/v1/chat/completions", KEY)
        out = http._accumulate_sse([
            'data: {"model":"actual","choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
            'data: {"model":"actual","choices":[],"usage":{"total_tokens":13}}', 'data: [DONE]'])
        self.assertEqual(out["model"], "actual")
        self.assertEqual(out["usage"]["total_tokens"], 13)
        http.close()

    def test_dripping_sse_is_checked_before_waiting_for_newline(self):
        clock, emitted = [0.0], []
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                for piece in (b"data:", b" ", b"{}\n"):
                    emitted.append(piece)
                    clock[0] += 3
                    yield piece
        http = LLMHttp("https://example.invalid/v1/chat/completions", KEY, request_deadline=2)
        http.close()
        http._client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Stream())))
        with patch("llm_http.time.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(TransportError):
                http.post_stream({})
        self.assertEqual(len(emitted), 1)
        http.close()

    def test_retry_after_invalid_values_never_disable_limits(self):
        self.assertEqual(retry_after_seconds("999999"), 30)
        self.assertIsNone(retry_after_seconds("nan"))
        self.assertIsNone(retry_after_seconds("-1"))


if __name__ == "__main__":
    unittest.main()
