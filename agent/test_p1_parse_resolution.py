"""P1 解析可解释停判：公司、期间、单位与口径对抗样例。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.claim_check import check_one_claim
from audit.report import render_report


def fact(**changes):
    base = {
        "evidence_id": "e1", "company_code": "600519", "company_name": "贵州茅台",
        "metric": "revenue", "metric_name": "营业收入", "period_year": 2024, "report_year": 2024,
        "period_kind": "annual", "period_start": "2024-01-01", "period_end": "2024-12-31",
        "duration_months": 12, "comparison_group": "600519|t1", "document_id": "doc",
        "source_file": "data/test.pdf", "source_sha256": "a" * 64,
        "value": "100", "unit": "元", "normalized_value": "100", "normalized_unit": "元",
        "currency": "CNY", "scope": "consolidated", "adjustment": "as_reported",
        "issues": [], "page": 5, "value_bbox": [0, 0, 1, 1],
    }
    base.update(changes)
    return base


def protocol_item(**changes):
    base = {
        "claim_id": "C1", "sentence_id": 1, "quote": "", "context_quote": None,
        "claim_type": "amount", "company_name": "贵州茅台", "company_b": None,
        "comparison_operator": None, "period_year": 2024, "period_kind": "annual",
        "metric_text": "营业收入", "metric": "revenue", "scope": "consolidated",
        "kind": "amount", "value": "100", "unit": "元", "operator": "eq",
        "tolerance_pct": None, "direction": "unknown", "currency": "CNY",
        "qualifier": "exact", "plain_claim": "P1 对抗样例", "is_forecast": False,
        "ambiguity": "none", "verification_action": "compare_amount",
    }
    base.update(changes)
    return base


class ParseResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "data" / "evals" / "p1_parse_conflict_cases.json"
        cls.fixture = json.loads(path.read_text(encoding="utf-8"))

    def setUp(self):
        self.facts = [
            fact(evidence_id="mt-revenue", document_id="mt", comparison_group="mt"),
            fact(evidence_id="wly-revenue", company_code="000858", company_name="五粮液",
                 document_id="wly", comparison_group="wly", value="80", normalized_value="80"),
            fact(evidence_id="example-revenue", company_code="300999",
                 company_name="示例科技股份有限公司", document_id="example",
                 comparison_group="example"),
            fact(evidence_id="mt-profit", metric="parent_net_profit", metric_name="归母净利润",
                 scope="parent_shareholders", document_id="mt-profit", comparison_group="mt-profit"),
        ]

    def test_fixture_has_at_least_twenty_unique_cases(self):
        cases = self.fixture["cases"]
        ids = [case["id"] for case in cases]
        self.assertGreaterEqual(len(cases), 20)
        self.assertEqual(len(ids), len(set(ids)))

    def test_cases_have_explainable_resolution_and_no_silent_binding(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"], name=case["name"]):
                sentence = case["sentence"]
                draft = case.get("draft", sentence)
                item = protocol_item(quote=sentence, **case.get("item", {}))
                row = check_one_claim(item, self.facts, sentence, draft, "C1")
                resolution = row.get("resolution")
                self.assertIsInstance(resolution, dict, row)
                self.assertEqual(resolution.get("status"), case["expect"]["resolution"], row)
                candidates = resolution.get("candidates") or []
                if case["expect"].get("empty_candidates"):
                    self.assertEqual(candidates, [])
                if case["expect"].get("candidate"):
                    self.assertIn(case["expect"]["candidate"], {row.get("code") for row in candidates})
                if case["expect"].get("conflict"):
                    self.assertIn(case["expect"]["conflict"],
                                  {row.get("field") for row in resolution.get("conflicts") or []})
                if case["expect"].get("forbid_company"):
                    self.assertNotIn("normalized_claim", row,
                                     f"{case['id']} 在不唯一场景下静默绑定了公司")
                if case["expect"].get("company"):
                    self.assertIn("normalized_claim", row, row)
                    self.assertEqual(row["normalized_claim"]["company_code"], case["expect"]["company"])
                if resolution.get("status") != "unique":
                    self.assertNotIn("normalized_claim", row,
                                     f"{case['id']} 未唯一解析却产生了确定核查对象")
                    self.assertTrue(resolution.get("next_action"), row)

    def test_report_displays_resolution_candidates_and_action(self):
        row = {
            "claim_id": "C1", "quote": "示例", "status": "证据不足", "track": "review",
            "reason_code": "ambiguous_company_in_sentence", "reason": "公司歧义",
            "evidence": [], "resolution": {
                "status": "ambiguous_company",
                "candidates": [
                    {"code": "600519", "name": "贵州茅台", "matched": "贵州茅台", "loaded": True},
                    {"code": "000858", "name": "五粮液", "matched": "五粮液", "loaded": True},
                ],
                "conflicts": [],
                "next_action": {"code": "select_company", "message": "请选择唯一公司"},
            },
        }
        report = render_report([row], model="stub", run_id="p1")
        self.assertIn("解析状态", report)
        self.assertIn("ambiguous_company", report)
        self.assertIn("600519", report)
        self.assertIn("请选择唯一公司", report)


if __name__ == "__main__":
    unittest.main()
