from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from backend_runtime import DEFAULTS, load_config
from eval_harness import ReplayClient
from live_stability import (
    assess_live_stability,
    build_long_document,
    canonical_signature,
    run_live_rounds,
)


def sample_gold() -> dict:
    return {
        "dataset_id": "unit-live-stability",
        "statements": [
            {"id": "S01", "text": "中国移动2025年营业收入为100亿元。"},
            {"id": "S02", "text": "紫金矿业2025年净利润为20亿元。"},
            {"id": "S03", "text": "中国移动2026年第一季度营业收入为30亿元。"},
        ],
    }


class LongDocumentProtocolTests(unittest.TestCase):
    def test_default_chunk_size_keeps_real_structured_output_below_limit(self):
        self.assertLessEqual(load_config()["document"]["chunk_sentences"], 20)

    def test_long_document_has_unique_markers_and_no_scoring_fields(self):
        draft = build_long_document(sample_gold(), repeats=2)
        self.assertIn("长文段01-S01", draft)
        self.assertIn("长文段02-S03", draft)
        self.assertEqual(draft.count("中国移动2025年营业收入为100亿元。"), 2)
        for forbidden in ("expect", "GOLD_ONLY", "abstention_allowed"):
            self.assertNotIn(forbidden, draft)

    def test_signature_is_stable_across_order_but_changes_with_verdict(self):
        first = {"checks": [
            {"sentence_id": 1, "claim_id": "S1", "status": "证据支持",
             "reason_code": "deterministic_check", "failure_code": None,
             "evidence_ids": ["E2", "E1"]},
            {"sentence_id": 2, "claim_id": "S2", "status": "证据不足",
             "reason_code": "missing_evidence", "failure_code": None,
             "evidence_ids": []},
        ]}
        reordered = {"checks": list(reversed(first["checks"]))}
        changed = copy.deepcopy(first)
        changed["checks"][1]["status"] = "口径冲突／需人工复核"
        self.assertEqual(canonical_signature(first), canonical_signature(reordered))
        self.assertNotEqual(canonical_signature(first), canonical_signature(changed))

    def test_rounds_force_multiple_chunks_and_record_runtime_cost(self):
        config = copy.deepcopy(DEFAULTS)
        config["document"]["chunk_sentences"] = 2
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run_live_rounds(
                sample_gold(),
                facts=[],
                output_dir=output,
                client_factory=lambda: ReplayClient(items=[]),
                runs=2,
                repeats=2,
                config=config,
            )
            self.assertEqual(len(result["runs"]), 2)
            self.assertTrue(all(row["sentences"] == 6 for row in result["runs"]))
            self.assertTrue(all(row["chunks"] == 3 for row in result["runs"]))
            self.assertTrue(all(row["status"] == "completed" for row in result["runs"]))
            self.assertEqual(result["consistency"]["rate"], 1.0)
            self.assertTrue((output / "live-run-01" / "results" / "text_checks.json").is_file())
            encoded = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("offline-no-credential", encoded)

    def test_assessment_refuses_to_call_simulated_runs_live_pass(self):
        result = {
            "runs": [
                {"status": "completed", "chunks": 2, "unfinished": 0,
                 "failed_chunks": 0, "requests": 0, "signature": "same"},
                {"status": "completed", "chunks": 2, "unfinished": 0,
                 "failed_chunks": 0, "requests": 0, "signature": "same"},
            ],
            "consistency": {"rate": 1.0},
        }
        assessed = assess_live_stability(result)
        self.assertFalse(assessed["ok"])
        self.assertFalse(assessed["checks"]["real_provider_requests"])
        self.assertTrue(assessed["checks"]["multi_chunk"])
        self.assertTrue(assessed["checks"]["consistent"])


if __name__ == "__main__":
    unittest.main()
