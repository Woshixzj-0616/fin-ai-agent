"""B6 审计、工具与报告拆分契约。

这些测试先钉住旧门面、工具规格、统一事件、无模型回退和报告引用；
实现必须迁移到 audit 包后仍满足旧调用方契约。
"""
from __future__ import annotations

import ast
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from materials import ROOT, Run


FACADE_CONTRACT = {
    "llm_check": {
        "audit.schema": (
            "COMPANY_ALIASES",
            "load_companies",
            "COMPANIES",
            "companies_from_facts",
            "METRICS",
            "METRIC_NOTE",
            "UNITS",
            "OPERATORS",
            "CLAIM_TYPES",
            "SECRET_PATTERN",
            "MAX_ITEMS",
            "DEFAULT_TOLERANCE_PCT",
            "LLMError",
            "schema",
            "validate_schema",
        ),
        "audit.prompt": ("FUZZY", "FORECAST", "MAX_DRAFT_CHARS", "SYSTEM_PROMPT"),
        "audit.client": ("NoRedirect", "LLMClient"),
        "audit.grounding": ("split_draft", "resolve_company"),
        "audit.claim_check": ("check_one_claim", "check_payload", "_tool_verdict_rows"),
        "audit.report": ("render_report",),
        "audit.service": ("check_text",),
    },
    "tools": {
        "audit.tools.specs": ("TOOL_NAMES", "tool_specs"),
        "audit.tools.evidence": (
            "_err",
            "list_catalog",
            "_fact_quality",
            "_fact_identity",
            "_pick_fact",
            "find_evidence",
        ),
        "audit.tools.compute": ("compute_yoy", "_one_fact", "_norm_value", "compute_trend"),
        "audit.tools.compare": ("compare_claim", "compare_companies"),
        "audit.tools.search": ("search_text",),
        "audit.tools.dispatch": ("dispatch",),
    },
    "agent_loop": {
        "audit.loop": (
            "MAX_ROUNDS",
            "MAX_TOOL_CALLS",
            "MAX_REPAIR_ROUNDS",
            "LOOP_SYSTEM",
            "parse_loop_reply",
            "_repair_messages",
            "run_loop",
            "tool_summary",
            "_openai_tools",
            "run_loop_native",
        )
    },
    "audit_checks": {
        "audit.supplements": (
            "base_check",
            "correction",
            "check_valuation",
            "check_reference",
            "check_draft_supplements",
        )
    },
}


def sample_fact(**changes):
    fact = {
        "evidence_id": "e1",
        "company_code": "600519",
        "company_name": "贵州茅台",
        "metric": "revenue",
        "metric_name": "营业收入",
        "period_year": 2024,
        "report_year": 2024,
        "value": "100",
        "unit": "元",
        "normalized_value": "100",
        "normalized_unit": "元",
        "scope": "consolidated",
        "adjustment": "as_reported",
        "period_kind": "annual",
        "period_start": "2024-01-01",
        "period_end": "2024-12-31",
        "issues": [],
        "page": 5,
        "value_bbox": [0, 0, 1, 1],
        "source_file": "data/source.pdf",
        "source_sha256": "a" * 64,
        "currency": "CNY",
    }
    fact.update(changes)
    return fact


def event_capture():
    events = []

    def event(name, **data):
        events.append({"event": name, **data})

    return events, SimpleNamespace(event=event)


class FacadeCompatibilityTests(unittest.TestCase):
    def test_facade_names_are_direct_reexports(self):
        for facade_name, modules in FACADE_CONTRACT.items():
            facade = importlib.import_module(facade_name)
            for module_name, names in modules.items():
                implementation = importlib.import_module(module_name)
                for name in names:
                    with self.subTest(facade=facade_name, module=module_name, name=name):
                        self.assertIs(getattr(facade, name), getattr(implementation, name))
                        target = getattr(facade, name)
                        if callable(target):
                            self.assertEqual(target.__module__, module_name)

    def test_facades_do_not_define_implementation(self):
        for name in ("llm_check.py", "tools.py", "agent_loop.py", "audit_checks.py"):
            with self.subTest(name=name):
                tree = ast.parse((ROOT / "agent" / name).read_text(encoding="utf-8"))
                definitions = [
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                self.assertEqual(definitions, [])


class ToolContractTests(unittest.TestCase):
    def test_tool_names_specs_and_handlers_are_one_contract(self):
        from audit.tools import dispatch as dispatch_module
        from audit.tools import specs

        self.assertIs(specs.TOOL_NAMES, importlib.import_module("tools").TOOL_NAMES)
        self.assertEqual(set(specs.TOOL_NAMES), set(dispatch_module.DISPATCH_HANDLERS))
        self.assertEqual([spec["name"] for spec in specs.tool_specs()], list(specs.TOOL_NAMES))
        self.assertEqual(
            set(specs.TOOL_NAMES),
            {item["function"]["name"] for item in specs.openai_tools()}
            - {"submit_claims"},
        )

    def test_openai_submit_spec_is_derived_from_schema(self):
        from audit.schema import schema
        from audit.tools.specs import openai_tools

        submit = next(item for item in openai_tools() if item["function"]["name"] == "submit_claims")
        contract = schema()
        self.assertEqual(submit["function"]["parameters"]["properties"], contract["properties"])
        self.assertEqual(submit["function"]["parameters"]["required"], contract["required"])
        self.assertFalse(submit["function"]["parameters"]["additionalProperties"])

    def test_native_openai_tools_are_derived_from_specs(self):
        from audit.loop import _openai_tools
        from audit.tools.specs import openai_tools

        self.assertEqual(_openai_tools(), openai_tools())


class ToolEventTests(unittest.TestCase):
    def _run_json(self, events, run):
        from audit.loop import run_loop

        frames = [
            {
                "action": "call_tools",
                "tool_calls": [
                    {
                        "name": "find_evidence",
                        "arguments": {
                            "company_name_or_code": "600519",
                            "metric": "revenue",
                            "period_year": 2024,
                        },
                    }
                ],
            },
            {"action": "submit_claims", "items": [], "unclaimed_sentences": [1]},
        ]

        class Stub:
            model, host, key, mode = "stub", "local", "k", "json_object"

            def chat(self, messages, run):
                return json.dumps(frames.pop(0), ensure_ascii=False)

        return run_loop(Stub(), "贵州茅台2024年营业收入为100元。", [sample_fact()], run,
                        sentences=[{"sentence_id": 1, "text": "贵州茅台2024年营业收入为100元。"}])

    def _run_native(self, events, run):
        from audit.loop import run_loop_native

        frames = [
            {
                "tool_calls": [
                    {
                        "id": "provider-1",
                        "type": "function",
                        "function": {
                            "name": "find_evidence",
                            "arguments": json.dumps(
                                {
                                    "company_name_or_code": "600519",
                                    "metric": "revenue",
                                    "period_year": 2024,
                                }
                            ),
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "provider-2",
                        "type": "function",
                        "function": {
                            "name": "submit_claims",
                            "arguments": json.dumps({"items": [], "unclaimed_sentences": [1]}),
                        },
                    }
                ]
            },
        ]

        class Stub:
            model, host, key, mode = "stub", "local", "k", "json_object"

            def chat_tools(self, messages, tools, run):
                return frames.pop(0)

        return run_loop_native(Stub(), "贵州茅台2024年营业收入为100元。", [sample_fact()], run,
                               sentences=[{"sentence_id": 1, "text": "贵州茅台2024年营业收入为100元。"}])

    def test_json_and_native_emit_same_tool_event_shape(self):
        json_events, json_run = event_capture()
        native_events, native_run = event_capture()
        self._run_json(json_events, json_run)
        self._run_native(native_events, native_run)

        json_tool = [event for event in json_events if event["event"].startswith("agent_tool")]
        native_tool = [event for event in native_events if event["event"].startswith("agent_tool")]
        self.assertEqual([event["event"] for event in json_tool], ["agent_tool_start", "agent_tool"])
        self.assertEqual([event["event"] for event in native_tool], ["agent_tool_start", "agent_tool"])
        self.assertEqual(set(json_tool[0]), set(native_tool[0]))
        self.assertEqual(set(json_tool[1]), set(native_tool[1]))
        self.assertEqual(json_tool[0]["call_id"], "json_r1_1")
        self.assertEqual(native_tool[0]["call_id"], "provider-1")
        self.assertEqual(json_tool[1]["result"], native_tool[1]["result"])
        self.assertEqual(json_tool[1]["status"], "ok")

    def test_budget_exhaustion_still_emits_complete_event_pair(self):
        from audit import loop

        events, run = event_capture()
        frames = [
            {
                "action": "call_tools",
                "tool_calls": [
                    {
                        "name": "find_evidence",
                        "arguments": {
                            "company_name_or_code": "600519",
                            "metric": "revenue",
                            "period_year": 2024,
                        },
                    }
                ],
            },
            {"action": "submit_claims", "items": [], "unclaimed_sentences": [1]},
        ]

        class Stub:
            model, host, key, mode = "stub", "local", "k", "json_object"

            def chat(self, messages, run):
                return json.dumps(frames.pop(0), ensure_ascii=False)

        with mock.patch.object(loop, "MAX_TOOL_CALLS", 0):
            loop.run_loop(Stub(), "x", [sample_fact()], run, sentences=[{"sentence_id": 1, "text": "x"}])

        tool_events = [event for event in events if event["event"].startswith("agent_tool")]
        self.assertEqual([event["event"] for event in tool_events], ["agent_tool_start", "agent_tool"])
        self.assertEqual(tool_events[1]["status"], "budget_exhausted")
        self.assertEqual(tool_events[1]["budget_left"], 0)


class BehaviorRegressionTests(unittest.TestCase):
    def test_check_text_without_loop_keeps_single_shot_mode(self):
        from audit.service import check_text

        class Client:
            key = "unit-test-key"
            model = "stub"
            host = "local"
            mode = "json_object"

            def extract(self, sentences, run, facts):
                return {"items": [], "unclaimed_sentences": [row["sentence_id"] for row in sentences]}

        with tempfile.TemporaryDirectory(prefix="b6_no_loop_") as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("2024年，贵州茅台营业收入为100元。", encoding="utf-8")
            run = Run(root, "b6-no-loop", {})
            bundle = check_text(path, [sample_fact()], run, Client(), use_loop=False)
            self.assertEqual(bundle["mode"], "fallback_single_shot")
            self.assertEqual(bundle["schema_version"], 2)
            self.assertTrue((root / "results" / "text_report.md").is_file())

    def test_loop_failure_falls_back_and_preserves_event(self):
        from audit.service import check_text
        from audit.schema import LLMError

        class Client:
            key = "unit-test-key"
            model = "stub"
            host = "local"
            mode = "json_object"

            def chat_tools(self, messages, tools, run):
                raise LLMError("native unavailable")

            def chat(self, messages, run):
                raise LLMError("json unavailable")

            def extract(self, sentences, run, facts):
                return {"items": [], "unclaimed_sentences": [row["sentence_id"] for row in sentences]}

        with tempfile.TemporaryDirectory(prefix="b6_fallback_") as directory:
            root = Path(directory)
            path = root / "draft.txt"
            path.write_text("2024年，贵州茅台营业收入为100元。", encoding="utf-8")
            run = Run(root, "b6-fallback", {})
            bundle = check_text(path, [sample_fact()], run, Client(), use_loop=True)
            self.assertEqual(bundle["mode"], "fallback_single_shot")
            self.assertIn("loop_fallback", "".join(run.events))


class ReportReferenceTests(unittest.TestCase):
    def test_report_keeps_evidence_link(self):
        from audit.report import render_report

        row = {
            "claim_id": "C1",
            "original_sentence": "贵州茅台2024年营业收入为100元。",
            "quote": "贵州茅台2024年营业收入为100元。",
            "track": "deterministic",
            "status": "证据支持",
            "reason_code": "deterministic_check",
            "reason": "一致",
            "evidence_ids": ["e1"],
            "evidence": [
                {
                    "evidence_id": "e1",
                    "source_file": "data/source.pdf",
                    "page": 5,
                }
            ],
            "calculation": {"status": "match"},
        }
        report = render_report([row], model="stub", run_id="R1")
        self.assertIn("[e1](../data/source.pdf)（PDF第5页）", report)

    def test_tool_verdict_rows_hydrate_evidence(self):
        from audit.claim_check import _tool_verdict_rows
        from audit.report import render_report

        rows = _tool_verdict_rows(
            [
                {
                    "round": 1,
                    "name": "compare_claim",
                    "arguments": {"metric": "revenue"},
                    "result": {
                        "verdict": "confirmed_error",
                        "reason_code": "deterministic_check",
                        "reason": "不一致",
                        "evidence_ids": ["e1"],
                        "expected": "100",
                    },
                }
            ],
            [sample_fact()],
        )
        self.assertEqual(rows[0]["evidence"][0]["evidence_id"], "e1")
        report = render_report(rows, model="stub", run_id="R1")
        self.assertIn("[e1](../data/source.pdf)（PDF第5页）", report)


class BoundaryAndArchiveTests(unittest.TestCase):
    def test_audit_package_does_not_import_legacy_facades(self):
        package = ROOT / "agent" / "audit"
        violations = []
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"llm_check", "tools", "agent_loop", "audit_checks"}:
                            violations.append((path.name, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module in {"llm_check", "tools", "agent_loop", "audit_checks"}:
                        violations.append((path.name, module))
        self.assertEqual(violations, [])

    def test_run_sources_include_audit_package(self):
        with tempfile.TemporaryDirectory(prefix="b6_sources_") as directory:
            run = Run(Path(directory), "b6-sources", {})
            self.assertIn("audit/schema.py", run.sources)
            self.assertIn("audit/service.py", run.sources)
            self.assertIn("audit/tools/specs.py", run.sources)


if __name__ == "__main__":
    unittest.main()
