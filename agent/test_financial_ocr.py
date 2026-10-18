from __future__ import annotations
import json

import os
import tempfile
import unittest
from pathlib import Path

import pymupdf

from extraction.orchestrator import extract_material
from materials import ROOT, Run, sha256


SOURCE = ROOT / "data" / "holdout" / "materials" / "600941-2025-annual-summary.pdf"
EXPECTED = {
    "revenue": "1050187000000",
    "parent_net_profit": "137095000000",
    "operating_cash_flow": "232919000000",
    "total_assets": "2092882000000",
    "parent_equity": "1393175000000",
}


def configured_tessdata() -> Path | None:
    candidates = []
    if os.environ.get("TESSDATA_PREFIX"):
        candidates.append(Path(os.environ["TESSDATA_PREFIX"]))
    candidates.append(ROOT / "data" / "ocr" / "tessdata")
    for directory in candidates:
        if all((directory / f"{name}.traineddata").is_file() for name in ("chi_sim", "eng")):
            if str(directory).isascii() or os.environ.get("TESSDATA_PREFIX"):
                return directory
    return None


def scanned_page() -> bytes:
    source = pymupdf.open(SOURCE)
    page = source[6]
    output = pymupdf.open()
    target = output.new_page(width=page.rect.width, height=page.rect.height)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), alpha=False)
    target.insert_image(target.rect, stream=pixmap.tobytes("png"))
    blob = output.tobytes()
    source.close()
    output.close()
    return blob


class FinancialScannedPdfTests(unittest.TestCase):
    def test_without_ocr_scanned_financial_report_is_explicitly_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "data/source.pdf"
            path.parent.mkdir()
            path.write_bytes(scanned_page())
            material = {
                "document_id": "scan-without-ocr", "company_code": "600941",
                "company_name": "中国移动", "report_year": 2025, "period": "annual",
                "local_file": "data/source.pdf", "sha256": sha256(path.read_bytes()),
            }
            run = Run(root, "financial-scan-rejected", {})
            with self.assertRaisesRegex(ValueError, "扫描|OCR"):
                extract_material(root, material, run)

    def test_financial_scan_requires_ocr_review_and_preserves_page_image(self):
        tessdata = configured_tessdata()
        if tessdata is None:
            self.skipTest("可选 OCR 语言数据未配置或路径非 ASCII；设置 TESSDATA_PREFIX 后重跑")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "data/source.pdf"
            path.parent.mkdir()
            path.write_bytes(scanned_page())
            material = {
                "document_id": "600941-2025-annual-scan", "company_code": "600941",
                "company_name": "中国移动", "report_year": 2025, "period": "annual",
                "document_type": "annual_report_summary", "local_file": "data/source.pdf",
                "sha256": sha256(path.read_bytes()),
            }
            run = Run(root, "financial-scan-ocr", {})
            old = os.environ.get("TESSDATA_PREFIX")
            os.environ["TESSDATA_PREFIX"] = str(tessdata)
            try:
                facts = extract_material(root, material, run, ocr=True)
            finally:
                if old is None:
                    os.environ.pop("TESSDATA_PREFIX", None)
                else:
                    os.environ["TESSDATA_PREFIX"] = old

            current = {fact["metric"]: fact for fact in facts
                       if fact.get("period_year") == 2025}
            for metric, value in EXPECTED.items():
                with self.subTest(metric=metric):
                    self.assertIn(metric, current)
                    self.assertEqual(current[metric]["normalized_value"], value)
                    self.assertTrue(current[metric]["ocr_used"])
                    self.assertEqual(current[metric]["review_status"], "needs_review")
                    self.assertIn("ocr_requires_manual_review", current[metric]["issues"])
                    image = current[metric].get("ocr_page_image")
                    self.assertTrue(image)
                    self.assertTrue((run.folder / image).is_file())
                    self.assertEqual(current[metric]["ocr_evidence"]["language"],
                                     "chi_sim+eng")
                    self.assertTrue(current[metric]["ocr_evidence"]["language_data_sha256"])

            events = [json.loads(event)["event"] for event in run.events]
            self.assertIn("ocr_used", events)
            self.assertIn("ocr_page_snapshot", events)
            self.assertIn("ocr_review_required", events)


if __name__ == "__main__":
    unittest.main()
