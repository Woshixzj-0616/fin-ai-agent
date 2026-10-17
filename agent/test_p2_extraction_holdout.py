from __future__ import annotations

import unittest

from extraction_holdout import compare_extraction, run_holdout
from materials import ROOT


def gold(ident: str, metric: str, name: str, value: str, unit: str, page: int = 2) -> dict:
    kind = "instant" if metric in {"total_assets", "parent_equity"} else "quarter"
    start = "2026-03-31" if kind == "instant" else "2026-01-01"
    return {
        "document_id": "D1",
        "evidence_id": ident,
        "metric": metric,
        "metric_name": name,
        "normalized_value": value,
        "normalized_unit": unit,
        "period_kind": kind,
        "period_start": start,
        "period_end": "2026-03-31",
        "page": page,
    }


def fact(metric: str, name: str, value: str, unit: str, page: int = 2,
         period_start: str = "2026-01-01", period_kind: str = "quarter",
         issues: list[str] | None = None) -> dict:
    return {
        "document_id": "D1",
        "metric": metric,
        "metric_name": name,
        "original_label": name,
        "normalized_value": value,
        "normalized_unit": unit,
        "period_kind": period_kind,
        "period_start": period_start,
        "period_end": "2026-03-31",
        "page": page,
        "label_bbox": [1, 2, 3, 4],
        "value_bbox": [5, 6, 7, 8],
        "issues": issues or [],
    }


class ExtractionHoldoutMetricTests(unittest.TestCase):
    def test_value_period_unit_page_label_and_silent_errors_are_separate(self):
        targets = [
            gold("G1", "revenue", "营业收入", "100", "元"),
            gold("G2", "revenue", "营业收入", "200", "元"),
            gold("G3", "parent_net_profit", "归母净利润", "300", "元"),
        ]
        candidates = [
            fact("revenue", "营业收入", "100", "元"),
            fact("revenue", "营业收入", "999", "元"),
            fact("parent_net_profit", "归母净利润", "999", "元",
                 issues=["conflicting_extraction_values"]),
        ]
        result = compare_extraction(targets, candidates)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["value_correct"], 1)
        self.assertEqual(result["period_correct"], 3)
        self.assertEqual(result["unit_correct"], 3)
        self.assertEqual(result["page_correct"], 3)
        self.assertEqual(result["label_correct"], 3)
        self.assertEqual(result["silent_errors"], 1)
        self.assertEqual(result["explicit_uncertain"], 1)
        self.assertEqual(result["mismatch_counts"]["value"], 2)

    def test_material_failure_is_explicit_and_not_folded_into_correct(self):
        result = compare_extraction(
            [gold("G1", "revenue", "营业收入", "100", "元")],
            [],
            material_failures=[{"document_id": "D1", "error": "扫描件无文字层"}],
        )
        self.assertEqual(result["correct"], 0)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["material_failures"], 1)
        self.assertEqual(result["silent_errors"], 0)


class RealExtractionHoldoutTests(unittest.TestCase):
    def test_four_materials_match_all_twenty_target_facts(self):
        protocol = ROOT / "data" / "extraction_holdout" / "manifest.json"
        report = run_holdout(ROOT, protocol)["report"]
        self.assertEqual(report["material_failures"], 0, report)
        self.assertEqual(report["total"], 20)
        self.assertEqual(report["correct"], 20, report)
        self.assertEqual(report["silent_errors"], 0, report)


if __name__ == "__main__":
    unittest.main()
