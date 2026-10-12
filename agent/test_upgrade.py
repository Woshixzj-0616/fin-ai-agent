"""后端升级回归：跨公司方向、分块失败保留、主体绑定、精确期间、可比性三态、重启恢复与评测口径。

这些用例覆盖本轮修复的错误与边界，不提供真实模型错误频率，也不构成业务准确率。
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.claim_check import check_one_claim
from audit.grounding import resolve_company, resolve_period, resolve_subject
from audit.service import check_text
from audit.tools.compare import compare_claim, compare_companies
from audit.tools.evidence import find_evidence
from audit.tools.search import document_key, search_run_text, search_text
from backend_runtime import load_config
from eval_harness import ReplayClient, run_full_flow, score_atomic
from finance import analyze
from materials import Run
from periods import comparability, derive_quarter
from runtime import RunManager, RunNotVisible, RunStatus


def fact(**changes):
    base = {
        "evidence_id": "e1", "company_code": "600519", "company_name": "贵州茅台",
        "metric": "revenue", "metric_name": "营业收入", "period_year": 2024, "report_year": 2024,
        "period_kind": "annual", "period_start": "2024-01-01", "period_end": "2024-12-31",
        "duration_months": 12, "comparison_group": "600519|t1", "document_id": "doc-mt",
        "source_file": "data/mt.pdf", "source_sha256": "a" * 64,
        "value": "100", "unit": "元", "normalized_value": "100", "normalized_unit": "元",
        "currency": "CNY", "scope": "consolidated", "adjustment": "as_reported",
        "issues": [], "page": 5, "value_bbox": [0, 0, 1, 1],
    }
    base.update(changes)
    return base


def protocol_item(**changes):
    base = {"claim_id": "C1", "sentence_id": 1, "quote": "", "context_quote": None,
            "claim_type": "amount", "company_name": "贵州茅台", "company_b": None,
            "comparison_operator": None, "period_year": 2024, "period_kind": "annual",
            "metric_text": "营业收入", "metric": "revenue", "scope": "consolidated",
            "kind": "amount", "value": "100", "unit": "元", "operator": "eq",
            "tolerance_pct": None, "direction": "unknown", "currency": "CNY",
            "qualifier": "exact", "plain_claim": "示例", "is_forecast": False,
            "ambiguity": "none", "verification_action": "compare_amount"}
    base.update(changes)
    return base


class CrossCompanyComparisonTests(unittest.TestCase):
    """跨公司比较必须传方向；单项失败不吞错误。"""

    def setUp(self):
        self.a = fact(company_code="600519", company_name="贵州茅台", evidence_id="mt",
                      value="100", normalized_value="100")
        self.b = fact(company_code="000858", company_name="五粮液", evidence_id="wly",
                      document_id="doc-wly", value="80", normalized_value="80")
        self.sentence = "2024年贵州茅台营业收入高于五粮液。"
        self.base = protocol_item(claim_type="comparison", company_name="贵州茅台",
                                  company_b="五粮液", comparison_operator="exceed",
                                  quote="贵州茅台营业收入高于五粮液", metric="revenue",
                                  period_year=2024)

    def test_operator_is_passed_and_verdict_is_deterministic(self):
        row = check_one_claim(self.base, [self.a, self.b], self.sentence, self.sentence, "C1")
        self.assertEqual(row["status"], "证据支持")
        self.assertEqual(set(row["evidence_ids"]), {"mt", "wly"})
        sentence = self.sentence.replace("高于", "低于")
        below = check_one_claim({**self.base, "comparison_operator": "below",
                                 "quote": self.base["quote"].replace("高于", "低于")},
                                [self.a, self.b], sentence, sentence, "C2")
        self.assertEqual(below["status"], "确认错误")
        self.assertTrue(below["suggestion"])

    def test_at_least_and_at_most_directions(self):
        equal = {**self.b, "evidence_id": "wly", "value": "100", "normalized_value": "100"}
        sentence = self.sentence.replace("高于", "不低于")
        at_least = check_one_claim({**self.base, "comparison_operator": "at_least",
                                    "quote": self.base["quote"].replace("高于", "不低于")},
                                   [self.a, equal], sentence, sentence, "C3")
        self.assertEqual(at_least["status"], "证据支持")
        sentence = self.sentence.replace("高于", "不超过")
        at_most = check_one_claim({**self.base, "comparison_operator": "at_most",
                                   "quote": self.base["quote"].replace("高于", "不超过")},
                                  [self.a, equal], sentence, sentence, "C4")
        self.assertEqual(at_most["status"], "证据支持")

    def test_company_outside_materials_is_explicit_review(self):
        """被比较方写在句内但不在本次材料里：明确弃答，不是静默的模型判断。"""
        row = check_one_claim(self.base, [self.a], self.sentence, self.sentence, "C5")
        self.assertEqual(row["status"], "证据不足")
        self.assertIn(row["reason_code"],
                      {"comparison_company_unresolved", "missing_evidence"})

    def test_missing_side_evidence_is_review_not_exception(self):
        """两侧公司都在材料里、但一方缺该指标证据时，仍走工具的 missing_evidence。"""
        row = check_one_claim(self.base, [self.a, {**self.b, "metric": "total_revenue"}],
                              self.sentence, self.sentence, "C5b")
        self.assertEqual(row["status"], "证据不足")
        self.assertEqual(row["reason_code"], "missing_evidence")

    def test_sentence_subject_wins_over_model_field(self):
        """句内明确写五粮液时，模型填贵州茅台不能覆盖它。"""
        sentence = "2024年五粮液营业收入高于贵州茅台。"
        high = {**self.b, "value": "120", "normalized_value": "120"}
        low = {**self.a, "value": "80", "normalized_value": "80"}
        row = check_one_claim({**self.base, "quote": "五粮液营业收入高于贵州茅台",
                               "company_name": "贵州茅台", "company_b": "贵州茅台"},
                             [high, low], sentence, sentence, "C6")
        self.assertEqual(row["status"], "证据支持", row)
        self.assertEqual(row["grounding"]["company"]["a"]["code"], "000858")
        self.assertTrue(row["grounding"]["company"]["a"]["conflict"])
        self.assertEqual(row["evidence_ids"], ["wly", "mt"])


class ChunkFailureRetentionTests(unittest.TestCase):
    """一块成功一块失败：保留已完成块，最终产物照写，运行不得标成成功。"""

    def build_run(self, temp):
        run = Run(Path(temp), "upgrade-chunks", {})
        draft = "2024年贵州茅台营业收入为100元。\n2024年贵州茅台归母净利润为50元。"
        draft_path = run.output("draft.txt")
        draft_path.write_text(draft, encoding="utf-8")
        settings = json.loads(json.dumps(load_config()))
        settings["document"]["chunk_sentences"] = 1
        return run, draft_path, settings

    def test_completed_chunk_survives_second_chunk_crash(self):
        with tempfile.TemporaryDirectory(prefix="upgrade_chunk_") as temp:
            run, draft_path, settings = self.build_run(temp)
            items = [protocol_item(sentence_id=1, quote="2024年贵州茅台营业收入为100元",
                                   value="100", unit="元")]
            client = ReplayClient(items=items, error_at=2, error=TypeError("boom"))
            bundle = check_text(draft_path, [fact()], run, client, use_loop=False,
                                config=settings)
            self.assertEqual(bundle["status"], "partial")
            self.assertEqual(len(bundle["failed_chunks"]), 1)
            self.assertEqual(bundle["failed_chunks"][0]["error_type"], "TypeError")
            self.assertEqual(bundle["unfinished_sentence_ids"], [2])
            statuses = {row["sentence_id"]: row["status"] for row in bundle["checks"]}
            self.assertEqual(statuses[1], "证据支持")
            self.assertEqual(statuses[2], "口径冲突／需人工复核")
            failed_row = next(row for row in bundle["checks"] if row["sentence_id"] == 2)
            self.assertIn("TypeError", failed_row["reason"])
            self.assertTrue(run.output("text_checks.json").is_file())
            self.assertTrue(run.output("text_report.md").is_file())
            saved = json.loads(run.output("text_checks.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "partial")
            self.assertEqual(len(saved["checks"]), len(bundle["checks"]))

    def test_no_false_success_after_chunk_failure(self):
        with tempfile.TemporaryDirectory(prefix="upgrade_chunk_") as temp:
            run = Run(Path(temp), "upgrade-chunks", {})
            draft_path = run.output("draft.txt")
            draft_path.write_text("2024年贵州茅台营业收入为100元。", encoding="utf-8")
            client = ReplayClient(items=[], error_at=1, error=TypeError("boom"))
            bundle = check_text(draft_path, [fact()], run, client, use_loop=False)
            self.assertEqual(bundle["status"], "partial")
            self.assertEqual(bundle["unfinished_sentence_ids"], [1])
            self.assertTrue(bundle.get("reason"))
            self.assertEqual([row["status"] for row in bundle["checks"]],
                             ["口径冲突／需人工复核"])
            self.assertEqual(bundle["counts"], {"口径冲突／需人工复核": 1})

    def test_webui_keeps_salvaged_checks_but_reports_failure(self):
        import llm_check
        try:
            from webui import _check_draft
        except ModuleNotFoundError:
            from agent.webui import _check_draft

        with tempfile.TemporaryDirectory(prefix="upgrade_webui_") as temp:
            run = Run(Path(temp), "upgrade-webui", {})

            class StubClient:
                key, model, host, mode = "offline-no-credential", "stub", "local", "json_object"
                config = None

            def fake_check_text(path, facts, run, client, use_loop=True, **kwargs):
                run.output("text_checks.json").write_text(json.dumps({
                    "status": "partial",
                    "checks": [{"claim_id": "C1", "status": "证据支持", "evidence_ids": []}],
                    "unfinished_sentence_ids": [2],
                    "checkpoint_file": "text_checkpoint.json",
                    "mode": "chunked", "counts": {"证据支持": 1},
                }, ensure_ascii=False), encoding="utf-8")
                raise RuntimeError("分块执行中断")

            with patch.object(llm_check.LLMClient, "from_environment",
                              staticmethod(lambda *a, **k: StubClient())), \
                    patch.object(llm_check, "check_text", fake_check_text):
                payload = _check_draft("2024年贵州茅台营业收入为100元。", [fact()], run)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "分块执行中断")
            self.assertEqual([row["claim_id"] for row in payload["checks"]], ["C1"])
            self.assertEqual(payload["execution_status"], "partial")
            self.assertEqual(payload["unfinished_sentence_ids"], [2])


class SubjectAndPeriodGroundingTests(unittest.TestCase):
    """公司错配修复：句内主体优先，公司与期间保留定位依据。"""

    def setUp(self):
        self.mt = fact(company_code="600519", company_name="贵州茅台", evidence_id="mt",
                       value="0.80", normalized_value="0.80", unit="亿元")
        self.wly = fact(company_code="000858", company_name="五粮液", evidence_id="wly",
                        document_id="doc-wly", value="0.80", normalized_value="0.80", unit="亿元")

    def test_other_paragraph_company_cannot_hijack_sentence(self):
        draft = "贵州茅台2024年年度报告。2024年，五粮液营业收入为0.80亿元。"
        sentence = "2024年，五粮液营业收入为0.80亿元。"
        item = protocol_item(quote=sentence, company_name="贵州茅台", value="0.80",
                             unit="亿元", metric_text="营业收入")
        row = check_one_claim(item, [self.mt, self.wly], sentence, draft, "C1")
        self.assertEqual(row["evidence_ids"], ["wly"])
        self.assertEqual(row["grounding"]["company"]["basis"], "sentence_company")
        self.assertTrue(row["grounding"]["company"]["conflict"])
        self.assertTrue(row["interpretation"]["model_company_conflict"])

    def test_multi_company_sentence_is_ambiguous_without_matching_field(self):
        sentence = "贵州茅台与五粮液2024年营业收入分别为100元与80元。"
        item = protocol_item(quote=sentence, company_name=None, value="100", unit="元")
        row = check_one_claim(item, [self.mt, self.wly], sentence, sentence, "C2")
        self.assertEqual(row["reason_code"], "ambiguous_company_in_sentence")
        self.assertEqual(row["status"], "证据不足")

    def test_company_outside_loaded_materials_is_unresolved(self):
        draft = "2024年，五粮液营业收入为0.80亿元。"
        item = protocol_item(quote=draft, company_name="五粮液", value="0.80", unit="亿元")
        row = check_one_claim(item, [self.mt], draft, draft, "C3")
        self.assertEqual(row["reason_code"], "unresolved_company")
        self.assertEqual(row["grounding"]["company"]["basis"], "sentence_company")

    def test_paragraph_subject_and_period_are_located(self):
        draft = ("公司层面看，2024 年贵州茅台营业收入为 100 元，同比增长 1%，"
                 "基本符合市场预期。归母净利润录得 50 元。")
        sentence = "归母净利润录得 50 元。"
        profit = fact(metric="parent_net_profit", metric_name="归母净利润", scope="parent_shareholders",
                      evidence_id="profit", value="50", normalized_value="50")
        item = protocol_item(quote=sentence, company_name="贵州茅台", metric="parent_net_profit",
                             metric_text="归母净利润", value="50", scope="parent_shareholders")
        row = check_one_claim(item, [profit, fact()], sentence, draft, "C4")
        self.assertEqual(row["status"], "证据支持", row)
        self.assertEqual(row["grounding"]["company"]["basis"], "inherited_paragraph")
        self.assertEqual(row["grounding"]["period"]["located_in"], "paragraph")

    def test_preceding_sentence_reference_when_paragraph_is_ambiguous(self):
        draft = ("2023 年五粮液营业收入为 80 元。2024 年贵州茅台营业收入为 100 元。"
                 "归母净利润录得 50 元。")
        sentence = "归母净利润录得 50 元。"
        profit = fact(metric="parent_net_profit", metric_name="归母净利润", scope="parent_shareholders",
                      evidence_id="profit", value="50", normalized_value="50")
        item = protocol_item(quote=sentence, company_name="贵州茅台", metric="parent_net_profit",
                             metric_text="归母净利润", value="50", scope="parent_shareholders")
        row = check_one_claim(item, [profit, fact()], sentence, draft, "C6")
        self.assertEqual(row["status"], "证据支持", row)
        self.assertEqual(row["grounding"]["company"]["basis"], "inherited_preceding_sentence")
        self.assertEqual(row["grounding"]["period"]["located_in"], "preceding_sentence")

    def test_ambiguous_paragraph_year_is_not_inherited(self):
        draft = ("2024 年贵州茅台营业收入为 100 元。2025 年预计增长。"
                 "归母净利润录得 50 元。")
        sentence = "归母净利润录得 50 元。"
        item = protocol_item(quote=sentence, company_name="贵州茅台", metric="parent_net_profit",
                             metric_text="归母净利润", value="50", scope="parent_shareholders")
        row = check_one_claim(item, [fact()], sentence, draft, "C5")
        self.assertEqual(row["reason_code"], "unresolved_period")

    def test_resolve_helpers_expose_basis(self):
        subject = resolve_subject("贵州茅台", sentence="2024年五粮液营业收入为80元。",
                                  draft="2024年五粮液营业收入为80元。", facts=[self.mt, self.wly])
        self.assertEqual(subject["code"], "000858")
        self.assertTrue(subject["conflict"])
        period = resolve_period(2024, sentence="没有年度", context="2024年五粮液营业收入为80元。")
        self.assertEqual(period["located_in"], "context")
        self.assertEqual(resolve_company("五粮液", "五粮液", "五粮液", [self.mt]), None)


class ToolPeriodAndDocumentIdentityTests(unittest.TestCase):
    """工具贯通报告类型、精确期间与材料 ID；旧调用继续兼容。"""

    def setUp(self):
        self.q1 = fact(evidence_id="q1", document_id="doc-q1", period_kind="quarter",
                       period_start="2025-01-01", period_end="2025-03-31", duration_months=3,
                       period_year=2025, report_year=2025, value="100", normalized_value="100",
                       comparison_group="600519|q1")
        self.h1 = fact(evidence_id="h1", document_id="doc-h1", period_kind="half",
                       period_start="2025-01-01", period_end="2025-06-30", duration_months=6,
                       period_year=2025, report_year=2025, value="250", normalized_value="250",
                       comparison_group="600519|h1")
        self.facts = [self.q1, self.h1]

    def test_find_evidence_needs_precise_period(self):
        self.assertEqual(find_evidence(self.facts, company_name_or_code="600519",
                                       metric="revenue", period_year=2025)["status"], "ambiguous")
        half = find_evidence(self.facts, company_name_or_code="600519", metric="revenue",
                             period_year=2025, report_kind="half")
        self.assertEqual(half["items"][0]["evidence_id"], "h1")
        self.assertEqual(half["items"][0]["period_end"], "2025-06-30")
        q1 = find_evidence(self.facts, company_name_or_code="600519", metric="revenue",
                           period_year=2025, period_end="2025-03-31")
        self.assertEqual(q1["items"][0]["evidence_id"], "q1")
        by_document = find_evidence(self.facts, company_name_or_code="600519", metric="revenue",
                                    period_year=2025, document_id="doc-h1")
        self.assertEqual(by_document["items"][0]["evidence_id"], "h1")
        missing = find_evidence(self.facts, company_name_or_code="600519", metric="revenue",
                                period_year=2025, report_kind="annual")
        self.assertEqual(missing["status"], "empty")
        self.assertEqual(missing["filters"]["period_kind"], "annual")

    def test_find_evidence_old_call_still_ambiguous_not_silent(self):
        out = find_evidence(self.facts, company_name_or_code="600519", metric="revenue",
                            period_year=2025)
        self.assertEqual(out["status"], "ambiguous")
        self.assertEqual(len(out["evidence_ids"]), 2)

    def test_compare_claim_uses_located_report_type(self):
        result = compare_claim([self.h1], company_name_or_code="600519", metric="revenue",
                               period_year=2025, kind="amount", claimed_value="250",
                               claimed_unit="元", operator="eq")
        self.assertEqual(result["verdict"], "evidence_supported", result)
        self.assertEqual(result["period"]["period_kind"], "half")
        self.assertEqual(result["period"]["period_end"], "2025-06-30")
        wrong = compare_claim([self.h1], company_name_or_code="600519", metric="revenue",
                              period_year=2025, kind="amount", claimed_value="100",
                              claimed_unit="元", operator="eq")
        self.assertEqual(wrong["verdict"], "confirmed_error")

    def test_compare_companies_rejects_mixed_periods(self):
        other_q1 = fact(company_code="000858", company_name="五粮液", evidence_id="wly-q1",
                        document_id="wly-q1", period_kind="quarter", period_start="2025-01-01",
                        period_end="2025-03-31", duration_months=3, period_year=2025,
                        report_year=2025, value="80", normalized_value="80",
                        comparison_group="000858|q1")
        mixed = compare_companies([self.q1, self.h1, other_q1], company_a="600519",
                                  company_b="000858", metric="revenue", period_year=2025,
                                  operator="exceed", report_kind="half")
        self.assertEqual(mixed["verdict"], "needs_review")
        self.assertIn(mixed["reason_code"], {"missing_evidence", "period_mismatch"})

    def test_search_text_identity_keys_and_legacy_fallback(self):
        docs = {document_key("600519", 2025, "doc-q1"): ["第一季度报告：营业收入100元。"],
                document_key("600519", 2025, "doc-h1"): ["半年度报告：营业收入250元。"]}
        ambiguous = search_text(self.facts, docs, company_name_or_code="600519",
                                source_report_year=2025, query="营业收入")
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertEqual(len(ambiguous["candidates"]), 2)
        picked = search_text(self.facts, docs, company_name_or_code="600519",
                             source_report_year=2025, query="营业收入", document_id="doc-h1")
        self.assertEqual(picked["status"], "ok")
        self.assertEqual(picked["document_id"], "doc-h1")
        self.assertEqual(picked["period_end"], "2025-06-30")
        self.assertTrue(all(hit["document_id"] == "doc-h1" for hit in picked["hits"]))
        # 旧键下同时有两份材料：无法区分期间，保持歧义而不是任取一份。
        legacy_multi = search_text(self.facts, {"600519_2025": ["旧键正文：营业收入250元。"]},
                                   company_name_or_code="600519", source_report_year=2025,
                                   query="营业收入")
        self.assertEqual(legacy_multi["status"], "ambiguous")
        legacy = search_text([self.h1], {"600519_2025": ["旧键正文：营业收入250元。"]},
                             company_name_or_code="600519", source_report_year=2025,
                             query="营业收入")
        self.assertEqual(legacy["status"], "ok")
        self.assertEqual(legacy["document_key"], "600519_2025")

    def test_search_run_text_labels_each_document(self):
        docs = {document_key("600519", 2025, "doc-q1"): ["第一季度报告：营业收入100元。"],
                document_key("600519", 2025, "doc-h1"): ["半年度报告：营业收入250元。"]}
        out = search_run_text(self.facts, docs, query="营业收入", top_k=5)
        self.assertEqual(out["status"], "ok")
        self.assertEqual({hit["document_id"] for hit in out["hits"]}, {"doc-q1", "doc-h1"})
        self.assertEqual(len(out["documents"]), 2)


class SearchApiDocumentIdentityTests(unittest.TestCase):
    """API 层检索：同年多报告不互相覆盖，命中带材料身份。"""

    def test_api_search_labels_each_document(self):
        try:
            from api.routes import ApiApplication
        except ModuleNotFoundError:
            from agent.api.routes import ApiApplication
        routes = sys.modules[ApiApplication.__module__]
        q1 = fact(evidence_id="q1", document_id="doc-q1", period_kind="quarter",
                  period_start="2025-01-01", period_end="2025-03-31", duration_months=3,
                  period_year=2025, report_year=2025, value="100", normalized_value="100",
                  comparison_group="600519|q1")
        h1 = fact(evidence_id="h1", document_id="doc-h1", period_kind="half",
                  period_start="2025-01-01", period_end="2025-06-30", duration_months=6,
                  period_year=2025, report_year=2025, value="250", normalized_value="250",
                  comparison_group="600519|h1")

        def workflow(context, command, parameters, pdf, comparisons):
            context.output("webui_evidence.json").write_text(
                json.dumps([q1, h1], ensure_ascii=False), encoding="utf-8")
            context.output("webui_document_texts.json").write_text(json.dumps({
                document_key("600519", 2025, "doc-q1"): ["第一季度报告：营业收入100元。"],
                document_key("600519", 2025, "doc-h1"): ["半年度报告：营业收入250元。"],
            }, ensure_ascii=False), encoding="utf-8")
            return {"ok": True, "counts": {}, "evidence": [q1, h1], "failures": [],
                    "report_md": "# 测试\n"}

        with tempfile.TemporaryDirectory(prefix="upgrade_search_") as temp:
            root = Path(temp)
            app = ApiApplication(root, workflow_runner=workflow)
            try:
                session = "s" * 32
                with patch.object(routes, "validate_pdf_upload", return_value={}):
                    created = app.create_run(session, "financial",
                                             {"code": "600519", "name": "贵州茅台", "year": 2025},
                                             b"pdf", [])
                deadline = time.monotonic() + 5
                while (app.get_run(session, created["run_id"])["status"] != "succeeded"
                       and time.monotonic() < deadline):
                    time.sleep(0.01)
                result = app.search(session, created["run_id"], "营业收入", top_k=5)
                self.assertEqual(result["status"], "ok")
                self.assertEqual({hit["document_id"] for hit in result["hits"]},
                                 {"doc-q1", "doc-h1"})
                self.assertEqual(len(result["documents"]), 2)
                self.assertEqual({item["period_kind"] for item in result["documents"]},
                                 {"quarter", "half"})
                self.assertEqual(len({hit["document_key"] for hit in result["hits"]}), 2)
            finally:
                app.close()


class DirectionCheckTests(unittest.TestCase):
    """无数字的增长方向：使用已有证据，不凭语气判断。"""

    def setUp(self):
        self.previous = fact(evidence_id="p2023", period_year=2023, report_year=2024,
                             period_start="2023-01-01", period_end="2023-12-31",
                             value="100", normalized_value="100")
        self.current = fact(evidence_id="c2024", period_year=2024, report_year=2024,
                            value="120", normalized_value="120")

    def run_claim(self, quote, **changes):
        sentence = f"2024年贵州茅台{quote}。"
        item = protocol_item(quote=sentence, claim_type="direction", operator="none",
                             value=None, unit=None, metric_text="营业收入", **changes)
        return check_one_claim(item, [self.current, self.previous], sentence, sentence, "D1")

    def test_growth_direction_supported_and_contradicted(self):
        up = self.run_claim("营业收入增长", direction="up")
        self.assertEqual(up["status"], "证据支持")
        self.assertEqual(up["track"], "deterministic")
        down = self.run_claim("营业收入下降", direction="down")
        self.assertEqual(down["status"], "确认错误")
        self.assertEqual(down["expected"], "增长")
        self.assertTrue(down["suggestion"])

    def test_flat_period_is_reported(self):
        same = {**self.previous, "value": "120", "normalized_value": "120"}
        sentence = "2024年贵州茅台营业收入基本持平。"
        item = protocol_item(quote=sentence, claim_type="direction", operator="none",
                             value=None, unit=None)
        row = check_one_claim(item, [self.current, same], sentence, sentence, "D3")
        self.assertEqual(row["status"], "证据支持")
        self.assertEqual(row["calculation"]["value"], "0")
        self.assertEqual(row["calculation"]["status"], "ok")
        growth_claim = protocol_item(quote="2024年贵州茅台营业收入增长。", claim_type="direction",
                                     operator="none", value=None, unit=None, direction="up")
        mismatch = check_one_claim(growth_claim, [self.current, same],
                                   "2024年贵州茅台营业收入增长。",
                                   "2024年贵州茅台营业收入增长。", "D4")
        self.assertEqual(mismatch["status"], "确认错误")
        self.assertEqual(mismatch["expected"], "持平")

    def test_missing_base_is_explicit_abstention(self):
        sentence = "2024年贵州茅台营业收入增长。"
        item = protocol_item(quote=sentence, claim_type="direction", operator="none",
                             value=None, unit=None)
        row = check_one_claim(item, [self.current], sentence, sentence, "D2")
        self.assertEqual(row["reason_code"], "direction_base_missing")
        self.assertEqual(row["status"], "证据不足")

    def test_no_direction_word_keeps_model_track_with_computed_value(self):
        row = self.run_claim("营业收入表现", direction="unknown")
        self.assertEqual(row["track"], "model")
        self.assertEqual(row["interpretation"]["computed_direction"], "up")


class ComparabilityTriStateTests(unittest.TestCase):
    """跨报告可比性：满足/冲突/未知，未知不默认一致。"""

    def setUp(self):
        self.h1 = fact(evidence_id="h1", document_id="doc-h1", period_kind="half",
                       period_start="2025-01-01", period_end="2025-06-30", duration_months=6,
                       period_year=2025, report_year=2025, value="250", normalized_value="250",
                       comparison_group="600519|h1")
        self.q1 = fact(evidence_id="q1", document_id="doc-q1", period_kind="quarter",
                       period_start="2025-01-01", period_end="2025-03-31", duration_months=3,
                       period_year=2025, report_year=2025, value="100", normalized_value="100",
                       comparison_group="600519|q1")

    def test_states(self):
        self.assertEqual(comparability(self.h1, self.h1)["status"], "satisfied")
        same_table = {**self.q1, "comparison_group": self.h1["comparison_group"],
                      "document_id": self.h1["document_id"],
                      "source_file": self.h1["source_file"], "source_sha256": self.h1["source_sha256"]}
        self.assertEqual(comparability(self.h1, same_table)["status"], "satisfied")
        unknown = comparability(self.h1, self.q1)
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(len(unknown["gaps"]), 3)
        declared = {"accounting_policy_version": "v1", "consolidation_basis": "consolidated",
                    "restatement_version": "none"}
        self.assertEqual(comparability({**self.h1, **declared}, {**self.q1, **declared})["status"],
                         "satisfied")
        conflict = comparability({**self.h1, **declared},
                                 {**self.q1, **declared, "restatement_version": "restated"})
        self.assertEqual(conflict["status"], "conflict")
        self.assertIn("restatement_version", conflict["conflicts"])

    def test_unknown_basis_blocks_derivation_but_keeps_operands(self):
        blocked = derive_quarter(self.h1, self.q1)
        self.assertEqual(blocked["status"], "not_comparable")
        self.assertEqual(blocked["attempted_value"], "150")
        self.assertEqual(blocked["operands"], {"current_ytd": "250", "previous_ytd": "100"})
        self.assertEqual(blocked["period_end"], "2025-06-30")
        self.assertEqual(sorted(blocked["evidence_ids"]), ["h1", "q1"])
        self.assertIn("accounting_policy_version", blocked["comparability"]["gaps"])

    def test_declared_basis_allows_derivation(self):
        declared = {"accounting_policy_version": "v1", "consolidation_basis": "consolidated",
                    "restatement_version": "none"}
        allowed = derive_quarter({**self.h1, **declared}, {**self.q1, **declared})
        self.assertEqual(allowed["status"], "ok")
        self.assertEqual(allowed["value"], "150")
        self.assertEqual(allowed["comparability"]["status"], "satisfied")

    def test_analyze_reports_blocked_attempts(self):
        events = []
        analysis = analyze([self.h1, self.q1], SimpleNamespace(event=lambda *a, **k: events.append(k)))
        self.assertFalse(any(row["qoq"]["status"] == "ok" for row in analysis["qoq_rows"]))
        blocked = analysis["blocked_derivations"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["attempted_value"], "150")
        self.assertTrue(blocked[0]["operands"])
        self.assertTrue(any(event.get("operation") == "derive_quarter" for event in events))


class RestartRestoreTests(unittest.TestCase):
    """服务重启后的历史恢复：终态可查、结果可读、中断标记未完成、会话隔离不变。"""

    def test_completed_run_is_restored_with_result(self):
        with tempfile.TemporaryDirectory(prefix="upgrade_restart_") as temp:
            root = Path(temp)
            first = RunManager(root)
            run = first.create_run("session-a", "financial", {"code": "600519"})
            first.start(run.id, lambda context: {"ok": True, "counts": {"match": 2}})
            first.wait(run.id, timeout=5)
            first.close()

            second = RunManager(root)
            try:
                restored = second.get_run(run.id, session_id="session-a")
                self.assertEqual(restored.status, RunStatus.SUCCEEDED)
                self.assertEqual(restored.snapshot()["result"], {"ok": True, "counts": {"match": 2}})
                self.assertEqual([item.id for item in second.list_runs("session-a")], [run.id])
                with self.assertRaises(RunNotVisible):
                    second.get_run(run.id, session_id="session-b")
                self.assertEqual(second.list_runs("session-b"), [])
                self.assertTrue(second.bus.replay(run.id))
            finally:
                second.close()

    def test_unfinished_run_is_marked_incomplete_after_restart(self):
        with tempfile.TemporaryDirectory(prefix="upgrade_restart_") as temp:
            root = Path(temp)
            gate = threading.Event()
            first = RunManager(root, shutdown_timeout=0.2)
            run = first.create_run("session-a", "financial", {})
            first.start(run.id, lambda context: gate.wait(10))
            deadline = time.monotonic() + 3
            while run.status != RunStatus.RUNNING and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(run.status, RunStatus.RUNNING)
            first.close()

            second = RunManager(root)
            try:
                restored = second.get_run(run.id, session_id="session-a")
                self.assertEqual(restored.status, RunStatus.FAILED)
                snapshot = restored.snapshot()
                self.assertEqual(snapshot["error_type"], "InterruptedByRestart")
                self.assertIn("未完成", snapshot["error"])
                self.assertIsNone(snapshot["result"])
                event_types = [event.event_type for event in second.bus.replay(run.id)]
                self.assertIn("run_interrupted", event_types)
            finally:
                second.close()
            gate.set()
            first.wait(run.id, timeout=5)

    def test_corrupt_state_is_skipped_without_breaking_startup(self):
        with tempfile.TemporaryDirectory(prefix="upgrade_restart_") as temp:
            root = Path(temp)
            folder = root / "results" / "runs" / "broken"
            folder.mkdir(parents=True)
            (folder / "run.json").write_text("{not json", encoding="utf-8")
            manager = RunManager(root)
            try:
                self.assertEqual(manager.restored, [])
                self.assertEqual(manager.list_runs("session-a"), [])
            finally:
                manager.close()


class EvalHarnessTests(unittest.TestCase):
    """评测统一走 check_text；答案只供评分程序读取；遗漏/超时/弃答/失败都保留。"""

    def test_protocol_items_never_contain_answers(self):
        from scripts.eval import run_report_eval as report_eval
        from audit.grounding import split_draft

        answers = report_eval.load_answers(report_eval.GOLD)
        sample = answers["samples"][0]
        aligned = report_eval.align_quotes(sample, split_draft(sample["text"]))
        items = report_eval.build_items(sample, aligned)
        statements = report_eval.build_statements(sample, aligned)
        dumped = json.dumps(items, ensure_ascii=False)
        for forbidden in ("gold_status", "gold", "expect", "证据支持", "模型判断"):
            self.assertNotIn(forbidden, dumped)
        self.assertTrue(items and statements)
        self.assertTrue(all("expect" in statement for statement in statements))
        self.assertEqual({statement["id"] for statement in statements},
                         {item["claim_id"] for item in items} | {statement["id"] for statement in statements
                                                                if statement["sentence_id"] is None})

    def test_client_visible_payload_has_no_answer_fields(self):
        seen = []

        class RecordingClient(ReplayClient):
            def extract(self, sentences, run, facts=None):
                seen.extend(sentences)
                return super().extract(sentences, run, facts)

        with tempfile.TemporaryDirectory(prefix="upgrade_eval_") as temp:
            run = Run(Path(temp), "upgrade-eval", {})
            sentence = "2024年贵州茅台营业收入为100元。"
            client = RecordingClient(items=[protocol_item(quote=sentence, value="100", unit="元")])
            run_full_flow(sentence, [fact()], run, client, use_loop=False)
        self.assertTrue(seen)
        for row in seen:
            self.assertEqual(set(row), {"sentence_id", "text"})

    def test_atomic_scoring_keeps_every_outcome(self):
        bundle = {
            "status": "partial",
            "unfinished_sentence_ids": [4],
            "failed_chunks": [{"chunk_id": 2}],
            "execution": {"attempts": [{"attempt": 1}], "tokens_charged": 120, "tool_calls": 2,
                          "active_seconds": 1.5},
            "checks": [
                {"sentence_id": 1, "status": "确认错误", "track": "deterministic",
                 "reason_code": "deterministic_check", "evidence_ids": ["e1"]},
                {"sentence_id": 3, "status": "证据不足", "track": "review",
                 "reason_code": "direction_base_missing", "evidence_ids": []},
                {"sentence_id": 4, "status": "口径冲突／需人工复核", "track": "review",
                 "reason_code": "execution_incomplete", "reason": "ExecutionLimit: 整篇任务时限已用尽",
                 "evidence_ids": []},
                {"sentence_id": 5, "status": "模型判断", "track": "model",
                 "reason_code": "forecast_marked_only", "evidence_ids": []},
                {"sentence_id": 6, "reason_code": "no_claim_extracted", "status": "口径冲突／需人工复核"},
            ],
        }
        statements = [
            {"id": "A1", "sentence_id": 1, "quote": "x", "expect": {"kind": "judge", "error": True}},
            {"id": "A2", "sentence_id": 2, "quote": "y", "expect": {"kind": "judge", "error": False}},
            {"id": "A3", "sentence_id": 3, "quote": "z", "expect": {"kind": "judge", "error": False}},
            {"id": "A4", "sentence_id": 4, "quote": "w", "expect": {"kind": "judge", "error": False}},
            {"id": "A5", "sentence_id": 5, "quote": "v",
             "expect": {"kind": "reason_code", "code": "forecast_marked_only"}},
            {"id": "A6", "sentence_id": 6, "quote": "u", "expect": {"kind": "nonverdict"}},
        ]
        scored = score_atomic(bundle, statements)
        summary = scored["summary"]
        self.assertEqual(summary["judged"], 1)
        self.assertEqual(summary["correct"], 1)
        self.assertEqual(summary["wrong"], 0)
        self.assertEqual(summary["abstain"], 1)
        self.assertEqual(summary["omitted"], 2)
        self.assertEqual(summary["timeout"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["model_track"], 1)
        self.assertEqual(summary["completion"], "partial")
        self.assertEqual(summary["failed_chunks"], 1)
        self.assertEqual(summary["tokens_charged"], 120)

    def test_chunk_failure_is_scored_as_failure_not_pass(self):
        with tempfile.TemporaryDirectory(prefix="upgrade_eval_") as temp:
            run = Run(Path(temp), "upgrade-eval", {})
            draft = "2024年贵州茅台营业收入为100元。\n2024年贵州茅台归母净利润为50元。"
            settings = json.loads(json.dumps(load_config()))
            settings["document"]["chunk_sentences"] = 1
            items = [protocol_item(sentence_id=1, quote=draft.split("\n")[0], value="100",
                                   unit="元")]
            client = ReplayClient(items=items, error_at=2, error=TypeError("boom"))
            bundle = run_full_flow(draft, [fact()], run, client, use_loop=False, config=settings)
            scored = score_atomic(bundle, [
                {"id": "B1", "sentence_id": 1, "quote": draft.split("\n")[0],
                 "expect": {"kind": "judge", "error": False}},
                {"id": "B2", "sentence_id": 2, "quote": draft.split("\n")[1],
                 "expect": {"kind": "judge", "error": False}},
            ])
            self.assertEqual(scored["summary"]["correct"], 1)
            self.assertEqual(scored["summary"]["failed"], 1)
            self.assertNotEqual(scored["summary"]["completion"], "completed")


if __name__ == "__main__":
    unittest.main()
