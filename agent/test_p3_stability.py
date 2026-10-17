from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from backend_runtime import DEFAULTS
from llm_check import LLMClient, check_text
from materials import Run
from stability import classify_failure, run_offline_fault_matrix
from test_competition import fact

KEY = "unit-test-credential"


def annual(**changes):
    return fact(period_kind="annual", duration_months=12, period_end="2025-12-31", **changes)


def model_response(request):
    payload = json.loads(request.content)
    submitted = json.loads(payload["messages"][-1]["content"])
    identifiers = [sentence["sentence_id"] for sentence in submitted["sentences"]]
    body = {"model": "actual-model", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"items": [], "unclaimed_sentences": identifiers})}}],
        "usage": {"total_tokens": 13}}
    return httpx.Response(200, json=body)


def attach_mock(client, handler):
    client._http.close()
    client._http._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


class FailureTaxonomyTests(unittest.TestCase):
    def test_http_failures_have_stable_safe_codes(self):
        cases = [
            (401, None, "auth_failed"),
            (403, None, "auth_failed"),
            (429, None, "rate_limited"),
            (503, None, "upstream_unavailable"),
            (302, None, "redirect_rejected"),
            (None, "模型接口连接失败或超时：ReadTimeout", "timeout"),
            (200, "模型流式响应中断，未收到结束标记", "response_truncated"),
            (200, "模型响应不是JSON", "invalid_response"),
        ]
        for status, message, expected in cases:
            from llm_http import TransportError
            with self.subTest(status=status, message=message):
                error = TransportError(message or "模型接口HTTP", status=status)
                self.assertEqual(classify_failure(error).code, expected)


class StabilityIntegrationTests(unittest.TestCase):
    def test_auth_failure_code_is_exposed_and_later_chunks_are_not_requested(self):
        config = copy.deepcopy(DEFAULTS)
        config["http"]["max_retries"] = 0
        config["document"]["chunk_sentences"] = 1
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(401, text=KEY)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft = root / "draft.txt"
            draft.write_text("贵州茅台2025年收入100元。" * 3, encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY,
                                           config=config), handler)
            result = check_text(draft, [annual()], Run(root, "auth-taxonomy", {}), client,
                                use_loop=False)
            self.assertEqual(len(requests), 1)
            self.assertEqual(result["status"], "partial")
            self.assertTrue(result["failed_chunks"])
            self.assertEqual({chunk["failure_code"] for chunk in result["failed_chunks"]},
                             {"auth_failed"})
            self.assertTrue(all(row.get("failure_code") == "auth_failed"
                                for row in result["checks"]))
            for path in (root / "results").iterdir():
                if path.is_file():
                    self.assertNotIn(KEY.encode(), path.read_bytes())
            client._http.close()

    def test_timeout_is_not_reported_as_abstention(self):
        config = copy.deepcopy(DEFAULTS)
        config["http"]["max_retries"] = 0
        config["document"]["chunk_sentences"] = 1

        def handler(_request):
            raise httpx.ReadTimeout("injected timeout")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            draft = root / "draft.txt"
            draft.write_text("贵州茅台2025年收入100元。", encoding="utf-8")
            client = attach_mock(LLMClient("https://example.invalid/v1", "test-model", KEY,
                                           config=config), handler)
            result = check_text(draft, [annual()], Run(root, "timeout", {}), client,
                                use_loop=False)
            self.assertEqual(result["failed_chunks"][0]["failure_code"], "timeout")
            self.assertEqual(result["checks"][0]["failure_code"], "timeout")
            self.assertNotEqual(result["checks"][0]["reason_code"], "abstain")
            client._http.close()

    def test_offline_matrix_covers_required_failures_and_has_no_secret(self):
        result = run_offline_fault_matrix(KEY)
        names = {row["name"] for row in result["cases"]}
        self.assertTrue({
            "timeout", "rate_limited", "auth_failed", "invalid_response",
            "truncated_response", "model_identity_changed", "partial_success",
            "secret_safety",
        }.issubset(names))
        self.assertTrue(all(row["passed"] for row in result["cases"]), result)
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(KEY, encoded)


if __name__ == "__main__":
    unittest.main()
