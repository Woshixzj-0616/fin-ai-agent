from __future__ import annotations

import os
from pathlib import Path
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
    issues = issues or []
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
        "issues": issues,
        "ocr_used": "ocr_requires_manual_review" in issues,
        "review_status": ("needs_review" if "ocr_requires_manual_review" in issues
                         else "verified"),
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
        self.assertEqual(result["material_stats"]["failed"], 1)
        self.assertEqual(result["material_stats"]["automatic_failures"], 1)
        self.assertEqual(result["silent_errors"], 0)


class RealExtractionHoldoutTests(unittest.TestCase):
    def test_four_materials_match_all_twenty_target_facts(self):
        protocol = ROOT / "data" / "extraction_holdout" / "manifest.json"
        report = run_holdout(ROOT, protocol)["report"]
        self.assertEqual(report["material_failures"], 0, report)
        self.assertEqual(report["total"], 20)
        self.assertEqual(report["correct"], 20, report)
        self.assertEqual(report["silent_errors"], 0, report)



    def test_ocr_review_is_excluded_from_automatic_accuracy(self):
        targets = [gold("G1", "revenue", "营业收入", "100", "元")]
        auto = fact("revenue", "营业收入", "100", "元")
        scan_target = {**gold("G2", "revenue", "营业收入", "200", "元"),
                       "requires_review": True, "review_reason": "ocr"}
        scan = fact("revenue", "营业收入", "200", "元",
                    issues=["ocr_requires_manual_review"])
        result = compare_extraction(targets + [scan_target], [auto, scan])
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["automatic_total"], 1)
        self.assertEqual(result["automatic_correct"], 1)
        self.assertEqual(result["ocr_total"], 1)
        self.assertEqual(result["ocr_review"], 1)
        self.assertEqual(result["ocr_value_correct"], 1)
        self.assertEqual(result["rows"][1]["status"], "ocr_review")

    @staticmethod
    def _configure_tessdata():
        candidates = []
        if os.environ.get("TESSDATA_PREFIX"):
            candidates.append(Path(os.environ["TESSDATA_PREFIX"]))
        candidates.append(ROOT / "data" / "ocr" / "tessdata")
        for directory in candidates:
            if all((directory / f"{name}.traineddata").is_file()
                   for name in ("chi_sim", "eng")):
                if str(directory).isascii() or os.environ.get("TESSDATA_PREFIX"):
                    os.environ["TESSDATA_PREFIX"] = str(directory)
                    return True
        return False


    def test_expanded_protocol_separates_ocr_review_from_automatic_accuracy(self):
        if not self._configure_tessdata():
            self.skipTest("可选 OCR 语言数据未配置或路径非 ASCII")
        protocol = ROOT / "data" / "extraction_holdout" / "manifest-v2.json"
        report = run_holdout(ROOT, protocol)["report"]
        self.assertEqual(report["material_failures"], 0, report)
        self.assertEqual(report["total"], 25, report)
        self.assertEqual(report["automatic_total"], 20, report)
        self.assertEqual(report["automatic_correct"], 20, report)
        self.assertEqual(report["ocr_total"], 5, report)
        self.assertEqual(report["ocr_review"], 5, report)
        self.assertEqual(report["ocr_value_correct"], 5, report)
        self.assertEqual(report["silent_errors"], 0, report)
        self.assertEqual(report["material_stats"]["ocr_materials"], 1, report)


if __name__ == "__main__":
    unittest.main()
