from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from holdout_eval import (  # noqa: E402
    check_leakage,
    compare_runs,
    render_markdown,
    runtime_payload,
    unified_metrics,
    validate_gold,
    validate_manifest,
    verify_dataset,
)


def row(ident: str, *, outcome: str, status: str | None, expect: dict,
        reason_code: str | None = None, evidence_ids: list[str] | None = None,
        evidence_locations: list[dict] | None = None, model_ms: float | None = 10,
        local_ms: float | None = 2) -> dict:
    return {
        "id": ident,
        "outcome": outcome,
        "status": status,
        "reason_code": reason_code,
        "expect": expect,
        "reason_category": "confirmed_error" if expect.get("error") else "correct_support",
        "evidence_ids": evidence_ids or [],
        "evidence_locations": evidence_locations or [],
        "model_ms": model_ms,
        "local_ms": local_ms,
    }


def location(document_id: str = "doc-a", page: int = 7) -> dict:
    return {
        "document_id": document_id,
        "page": page,
        "locator": f"{document_id}#page={page}",
    }


class UnifiedMetricTests(unittest.TestCase):
    def test_metrics_keep_accuracy_abstention_coverage_and_failure_apart(self):
        rows = [
            row("ok", outcome="judged", status="证据支持", expect={"kind": "judge", "error": False},
                evidence_ids=["e1"], evidence_locations=[location()]),
            row("err", outcome="judged", status="确认错误", expect={"kind": "judge", "error": True},
                evidence_ids=["e2"], evidence_locations=[location(page=8)]),
            row("fp", outcome="judged", status="确认错误", expect={"kind": "judge", "error": False},
                evidence_ids=["e3"], evidence_locations=[location(page=9)]),
            row("abstain", outcome="abstain", status="证据不足",
                expect={"kind": "status", "status": "证据不足"}),
            row("timeout", outcome="timeout", status=None, expect={"kind": "judge", "error": True}),
            row("omitted", outcome="omitted", status=None, expect={"kind": "judge", "error": False}),
        ]
        metrics = unified_metrics(rows)
        self.assertEqual(metrics["total"], 6)
        self.assertEqual(metrics["correct"], 3)
        self.assertAlmostEqual(metrics["judgment_accuracy"], 0.5)
        self.assertAlmostEqual(metrics["error_recall"], 1.0)
        self.assertAlmostEqual(metrics["false_positive_rate"], 0.5)
        self.assertAlmostEqual(metrics["correct_coverage"], 0.5)
        self.assertAlmostEqual(metrics["reasonable_abstention_rate"], 1.0)
        self.assertAlmostEqual(metrics["failure_rate"], 1 / 6)
        self.assertAlmostEqual(metrics["omitted_rate"], 1 / 6)
        self.assertEqual(metrics["correct_judgments"], 2)

    def test_citation_requires_id_document_page_and_locator(self):
        rows = [
            row("full", outcome="judged", status="证据支持", expect={"kind": "judge", "error": False},
                evidence_ids=["e1"], evidence_locations=[location()]),
            row("id_only", outcome="judged", status="证据支持",
                expect={"kind": "judge", "error": False}, evidence_ids=["e2"]),
            row("page_zero", outcome="judged", status="确认错误",
                expect={"kind": "judge", "error": True}, evidence_ids=["e3"],
                evidence_locations=[location(page=0)]),
        ]
        metrics = unified_metrics(rows)
        self.assertEqual(metrics["citation_supported"], 1)
        self.assertAlmostEqual(metrics["citation_support_rate"], 1 / 3)

    def test_latency_reports_model_and_local_separately(self):
        rows = [
            row("a", outcome="judged", status="证据支持", expect={"kind": "judge", "error": False},
                model_ms=10, local_ms=2),
            row("b", outcome="judged", status="证据支持", expect={"kind": "judge", "error": False},
                model_ms=30, local_ms=4),
        ]
        metrics = unified_metrics(rows)
        self.assertEqual(metrics["model_mean_ms"], 20)
        self.assertEqual(metrics["local_mean_ms"], 3)
        self.assertEqual(metrics["total_mean_ms"], 23)
        self.assertEqual(metrics["total_p95_ms"], 23)


class ConsistencyTests(unittest.TestCase):
    def test_consistency_uses_full_result_signature(self):
        base = {
            "id": "A", "outcome": "judged", "status": "证据支持",
            "reason_code": None, "evidence_ids": ["e1"],
        }
        runs = [
            [dict(base)],
            [dict(base)],
            [dict(base, evidence_ids=["e2"])],
        ]
        result = compare_runs(runs)
        self.assertEqual(result["statement_count"], 1)
        self.assertAlmostEqual(result["consistency_rate"], 1 / 3)
        self.assertEqual(result["differences"][0]["id"], "A")
        self.assertEqual(result["differences"][0]["run_count"], 3)


class LeakageTests(unittest.TestCase):
    def test_three_way_leakage_is_automatic(self):
        dev_scope = [{"code": "600519", "name": "贵州茅台"}]
        dev_manifest = [
            {"code": "600519", "sha256": "same", "announcement_id": "100"},
        ]
        holdout = {
            "materials": [
                {"document_id": "D1", "company_code": "600519", "sha256": "same",
                 "announcement_id": "100", "document_type": "annual_report_summary"},
                {"document_id": "D2", "company_code": "600519", "sha256": "same",
                 "announcement_id": "100", "document_type": "quarterly_report"},
                {"document_id": "D3", "company_code": "600941", "sha256": "new-1",
                 "announcement_id": "200", "document_type": "annual_report_summary"},
                {"document_id": "D4", "company_code": "601899", "sha256": "new-2",
                 "announcement_id": "300", "document_type": "interim_report_summary"},
            ]
        }
        result = check_leakage(dev_scope, holdout, dev_manifest)
        self.assertFalse(result["ok"])
        joined = "\n".join(result["issues"])
        self.assertIn("company_code", joined)
        self.assertIn("sha256", joined)
        self.assertIn("announcement_id", joined)
        self.assertIn("duplicate document_id", joined)

    def test_manifest_requires_company_and_material_diversity(self):
        manifest = {
            "dataset_id": "x",
            "materials": [
                {"document_id": "D1", "company_code": "600941", "company_name": "中国移动",
                 "sha256": "1", "announcement_id": "1", "document_type": "annual_report_summary"},
            ],
        }
        issues = validate_manifest(manifest)
        self.assertTrue(any("至少 2 家" in issue for issue in issues))
        self.assertTrue(any("至少 4 份" in issue for issue in issues))
        self.assertTrue(any("其他材料" in issue for issue in issues))

    def test_gold_requires_thirty_atomic_statements_and_freeze_fields(self):
        gold = {
            "dataset_id": "x",
            "independent_review": False,
            "statements": [
                {"id": f"S{i}", "text": "句", "claim": {}, "expect": {},
                 "reason_category": "correct_support", "allowed_document_ids": ["D1"],
                 "allowed_evidence": {"document_ids": ["D1"], "pages": [1], "fields": ["revenue"]},
                 "abstention_allowed": False}
                for i in range(29)
            ],
        }
        issues = validate_gold(gold, document_ids={"D1"})
        self.assertTrue(any("至少 30 条" in issue for issue in issues))
        gold["statements"].append({
            "id": "S30", "text": "句", "claim": {}, "expect": {},
            "allowed_document_ids": ["D9"], "reason_category": "correct_support",
            "allowed_evidence": {}, "abstention_allowed": False,
        })
        issues = validate_gold(gold, document_ids={"D1"})
        self.assertTrue(any("未登记文档" in issue for issue in issues))

    def test_runtime_payload_excludes_scoring_side_fields(self):
        gold = {
            "dataset_id": "x",
            "scoring_note": "GOLD_ONLY_SENTINEL",
            "statements": [
                {"id": "S1", "text": "中国移动2025年营业收入为10,501.87亿元。",
                 "claim": {"company_name": "中国移动", "metric": "revenue"},
                 "expect": {"kind": "judge", "error": False},
                 "reason_category": "correct_support",
                 "allowed_document_ids": ["D1"],
                 "allowed_evidence": {"document_ids": ["D1"], "pages": [7]},
                 "abstention_allowed": False},
            ],
        }
        draft, items = runtime_payload(gold)
        serialized = json.dumps({"draft": draft, "items": items}, ensure_ascii=False)
        self.assertNotIn("GOLD_ONLY_SENTINEL", serialized)
        self.assertNotIn('"expect"', serialized)
        self.assertNotIn('"allowed_document_ids"', serialized)


class DatasetAndReportTests(unittest.TestCase):
    def test_verify_dataset_checks_material_and_gold_hashes(self):
        with tempfile.TemporaryDirectory(prefix="holdout_eval_") as temp:
            root = Path(temp)
            (root / "materials").mkdir()
            (root / "materials" / "a.pdf").write_bytes(b"%PDF-test")
            import hashlib
            sha = hashlib.sha256(b"%PDF-test").hexdigest()
            manifest = {
                "dataset_id": "d",
                "materials": [
                    {"document_id": "D1", "company_code": "600941", "company_name": "中国移动",
                     "sha256": sha, "announcement_id": "1", "document_type": "annual_report_summary",
                     "local_file": "materials/a.pdf"},
                    {"document_id": "D2", "company_code": "601899", "company_name": "紫金矿业",
                     "sha256": "x2", "announcement_id": "2", "document_type": "quarterly_report",
                     "local_file": "materials/missing.pdf"},
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            gold = {"dataset_id": "d", "statements": []}
            gold_text = json.dumps(gold, ensure_ascii=False)
            (root / "gold.json").write_text(gold_text, encoding="utf-8")
            (root / "gold.sha256").write_text(hashlib.sha256(gold_text.encode()).hexdigest() + "\n", encoding="utf-8")
            result = verify_dataset(root)
            self.assertFalse(result["ok"])
            self.assertTrue(any("missing.pdf" in issue for issue in result["issues"]))

    def test_report_without_independent_review_does_not_claim_blind_accuracy(self):
        rows = [row("A", outcome="judged", status="证据支持",
                    expect={"kind": "judge", "error": False}, evidence_ids=["e"],
                    evidence_locations=[location()])]
        text = render_markdown(
            unified_metrics(rows),
            compare_runs([[rows[0]], [rows[0]], [rows[0]]]),
            {"independent_review": False, "dataset_id": "d", "run_mode": "replay"},
        )
        self.assertIn("留出集回归", text)
        self.assertIn("未进行独立人工复核", text)
        self.assertNotIn("盲测准确率", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
