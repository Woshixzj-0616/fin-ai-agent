"""B5 抽取模块拆分、兼容门面与结构化等价测试。"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from extract_cache import extract_many
from materials import ROOT, Run, load_materials

EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "B5"
BASELINE = EVIDENCE / "evidence-9-baseline.json"
BASELINE_HASH = EVIDENCE / "evidence-9-baseline.sha256"
BASELINE_SUMMARY = EVIDENCE / "baseline-summary.json"
FIXED_CODES = {"600519", "000858", "600887"}
FIXED_YEARS = {2023, 2024, 2025}

CONTRACT = {
    "extraction.catalog": (
        "METRICS",
        "ALIASES",
        "ADJUDICABLE",
        "REQUIRED",
        "PARENT_METRICS",
        "CONSOLIDATED_METRICS",
        "default_scope",
    ),
    "extraction.grid": (
        "box",
        "cell_bbox",
        "table_cells",
        "header_for",
        "amount_cell",
        "label_for",
        "metric_for",
        "document_currency",
        "_statement_title",
        "statement_sections",
        "corroborate_scope",
        "parse_table",
    ),
    "extraction.annual": (
        "extract_via_words",
        "extract_words_document",
        "extract_total_revenue",
    ),
    "extraction.orchestrator": (
        "_apply_scope",
        "_dedupe",
        "_scanned_pdf_hint",
        "extract_material",
    ),
}


def canonical_bytes(facts: list[dict]) -> bytes:
    ordered = sorted(facts, key=lambda item: item["evidence_id"])
    return json.dumps(
        ordered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class ModuleContractTests(unittest.TestCase):
    def test_catalog_constants_are_single_object(self):
        import extract
        from extraction import catalog

        for name in CONTRACT["extraction.catalog"]:
            with self.subTest(name=name):
                self.assertIs(getattr(extract, name), getattr(catalog, name))

    def test_legacy_names_are_direct_reexports(self):
        import extract

        for module_name, names in CONTRACT.items():
            module = importlib.import_module(module_name)
            for name in names:
                with self.subTest(module=module_name, name=name):
                    self.assertIs(getattr(extract, name), getattr(module, name))
                    target = getattr(extract, name)
                    if callable(target):
                        self.assertEqual(target.__module__, module_name)

    def test_package_does_not_import_legacy_facade(self):
        package = ROOT / "agent" / "extraction"
        self.assertTrue(package.is_dir(), "extraction 包尚未建立")
        files = sorted(package.glob("*.py"))
        self.assertGreaterEqual(len(files), 5)
        violations = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "extract" or alias.name.startswith("extract."):
                            violations.append((path.name, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module == "extract" or module.startswith("extract."):
                        violations.append((path.name, module))
        self.assertEqual(violations, [])

    def test_cache_fingerprint_changes_with_extraction_source(self):
        import extract_cache
        from extraction import annual

        annual_path = Path(annual.__file__).resolve()
        real_read_bytes = Path.read_bytes
        content = {"value": b"baseline"}

        def fake_read_bytes(path: Path) -> bytes:
            if path.resolve() == annual_path:
                return content["value"]
            return real_read_bytes(path)

        with mock.patch.object(Path, "read_bytes", fake_read_bytes):
            before = extract_cache._code_fingerprint()
            content["value"] = b"changed"
            after = extract_cache._code_fingerprint()

        self.assertNotEqual(before, after)


class MergeContractTests(unittest.TestCase):
    def test_dedupe_keeps_grid_winner_and_fills_missing_unit(self):
        import extract

        grid = {
            "evidence_id": "grid",
            "metric": "revenue",
            "period_year": 2024,
            "adjustment": "as_reported",
            "extraction_method": "grid_cells_and_geometric_headers",
            "value": "100",
            "normalized_value": "100",
            "unit": None,
            "issues": ["unit_unknown"],
            "reported_yoy": None,
            "adjustment_header": None,
            "label_bbox": None,
            "original_label": None,
        }
        words = {
            "evidence_id": "words",
            "metric": "revenue",
            "period_year": 2024,
            "adjustment": "as_reported",
            "extraction_method": "words_geometry",
            "value": "100",
            "normalized_value": "1000000",
            "unit": "万元",
            "issues": [],
            "reported_yoy": {"value": "10", "unit": "%"},
            "adjustment_header": {"text": "调整后"},
            "label_bbox": [1, 2, 3, 4],
            "original_label": "营业收入",
        }

        result = extract._dedupe([words, grid])
        self.assertEqual(len(result), 1)
        winner = result[0]
        self.assertEqual(winner["evidence_id"], "grid")
        self.assertEqual(winner["unit"], "万元")
        self.assertEqual(winner["normalized_value"], "1000000")
        self.assertNotIn("unit_unknown", winner["issues"])
        self.assertEqual(winner["reported_yoy"]["value"], "10")
        self.assertEqual(winner["adjustment_header"]["text"], "调整后")


class RealExtractionEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="b5_extraction_")
        cls.addClassCleanup(cls.temp.cleanup)
        run = Run(Path(cls.temp.name).resolve(), "b5-equivalence", {})
        cls.materials = [
            material
            for material in load_materials(ROOT)
            if material.get("company_code") in FIXED_CODES
            and material.get("report_year") in FIXED_YEARS
            and material.get("report_kind", "annual") == "annual"
        ]
        cls.facts, cls.failures = extract_many(
            ROOT,
            cls.materials,
            run,
            use_cache=False,
        )
        cls.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        cls.baseline_hash = BASELINE_HASH.read_text(encoding="ascii").strip().lower()
        cls.baseline_summary = json.loads(BASELINE_SUMMARY.read_text(encoding="utf-8"))

    def test_fixed_nine_reports_are_compared_to_frozen_snapshot(self):
        self.assertEqual(len(self.materials), 9)
        self.assertEqual(self.failures, [])
        actual = canonical_bytes(self.facts)
        self.assertEqual(hashlib.sha256(actual).hexdigest(), self.baseline_hash)
        self.assertEqual(json.loads(actual.decode("utf-8")), self.baseline)

    def test_channel_coverage_matches_baseline(self):
        methods = Counter(item["extraction_method"] for item in self.facts)
        self.assertEqual(dict(sorted(methods.items())), self.baseline_summary["by_method"])
        self.assertGreater(methods["grid_cells_and_geometric_headers"], 0)
        self.assertGreater(methods["words_geometry"], 0)
        self.assertGreater(methods["income_statement_total_revenue"], 0)

    def test_all_nine_reports_keep_current_core_metrics(self):
        core = (
            "revenue",
            "parent_net_profit",
            "adjusted_parent_net_profit",
            "operating_cash_flow",
        )
        current = [
            item
            for item in self.facts
            if item["period_year"] == item["report_year"]
            and item["adjustment"] in {"as_reported", "after"}
        ]
        for material in self.materials:
            for metric in core:
                with self.subTest(
                    code=material["company_code"],
                    year=material["report_year"],
                    metric=metric,
                ):
                    hit = [
                        item
                        for item in current
                        if item["company_code"] == material["company_code"]
                        and item["report_year"] == material["report_year"]
                        and item["metric"] == metric
                    ]
                    self.assertTrue(hit)

    def test_grid_channel_keeps_full_location_fields(self):
        grid = [
            item
            for item in self.facts
            if item["extraction_method"] == "grid_cells_and_geometric_headers"
        ]
        self.assertGreater(len(grid), 0)
        for item in grid:
            with self.subTest(evidence=item["evidence_id"]):
                self.assertTrue(item["value_bbox"])
                self.assertTrue(item["label_bbox"])
                self.assertTrue(item["table_bbox"])
                self.assertTrue(item["year_header"]["text"])

    def test_income_statement_channel_keeps_page_location(self):
        total = [
            item
            for item in self.facts
            if item["extraction_method"] == "income_statement_total_revenue"
        ]
        self.assertGreater(len(total), 0)
        for item in total:
            with self.subTest(evidence=item["evidence_id"]):
                self.assertEqual(item["metric"], "total_revenue")
                self.assertGreater(item["page"], 0)
                self.assertTrue(item["value_bbox"])
                self.assertTrue(item["year_header"]["text"])


if __name__ == "__main__":
    unittest.main()
