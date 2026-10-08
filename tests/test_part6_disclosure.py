from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from modules.part6_disclosure.agent import (
    _TOOLS,
    _compact_request_messages,
    _cover_identity,
    _completion_gate_issues,
    _coverage_status_supported,
    _findings_requiring_impacts,
    _research_turn_budget,
    _mandatory_audit_pages,
    _initial_pages,
    _normalise_result,
    _reported_difference,
    _safe_model_error,
    _summary_topics_without_findings,
    analyze_disclosure_report,
)
from modules.part6_disclosure.entry import (
    answer_follow_up,
    insufficient_material_result,
    partial_result_from_checkpoint,
)


class _FakeContext:
    def __init__(self, pages: dict[int, str]):
        self.page_count = max(pages)
        self.pages = pages
        self.read = []
        self.calculations = []

    def read_page(self, page: int) -> str | None:
        self.read.append(page)
        return self.pages.get(page)

    def record_calculation(self, name: str, **kwargs) -> None:
        self.calculations.append({"name": name, **kwargs})


class _FakeToolCall:
    def __init__(self, tool_id: str, name: str, arguments: dict):
        self.id = tool_id
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False))


class _FakeAssistantMessage:
    def __init__(self, calls: list[_FakeToolCall]):
        self.tool_calls = calls

    def model_dump(self, **kwargs):
        return {"role": "assistant", "content": None, "tool_calls": []}


class _FakeAnalysisContext:
    def __init__(self):
        self.file_name = "披露测试年报.pdf"
        self.file_sha256 = "a" * 64
        self.report_id = self.file_sha256
        self.page_count = 1
        self.pages = [{"page": 1, "text": "审计报告：标准无保留意见。审计师将收入确认为关键审计事项，详见附注。"}]
        self.related_results = {}
        self.events = []
        self.artifacts = {}

    def read_page(self, page: int) -> str | None:
        return self.pages[0]["text"] if page == 1 else None

    def search_pages(self, query: str, limit: int = 4) -> list[dict]:
        return [{"page": 1, "score": 10, "text": self.pages[0]["text"]}]

    def record_tool(self, name, arguments, result=None, *, error="", pages=None):
        self.events.append(name)

    def record_calculation(self, *args, **kwargs):
        self.events.append("calculation")

    def progress(self, *args):
        return None

    def tool_activity(self, *args):
        return None

    def save_artifact(self, name, value):
        self.artifacts[name] = value
        return {"path": f"{name}.json"}

    def call_model(self, **request):
        self.last_tool_names = [item["function"]["name"] for item in request["tools"]]
        calls = [
            _FakeToolCall("overview", "save_disclosure_overview", {
                "report_context": {"company_name": "测试公司"},
                "executive_summary": "本年报审计意见为标准无保留，审计师把收入确认列为关键审计事项，相关金额和披露仍需按专题证据逐页回查。",
                "audit_profile": {
                    "opinion_type": "标准无保留意见",
                    "evidence": [{"page": 1, "quote": "审计报告：标准无保留意见。", "supports": "审计意见类型"}],
                },
                "coverage": [{
                    "topic": "审计意见", "status": "confirmed_present",
                    "evidence": [{"page": 1, "quote": "审计报告：标准无保留意见。", "supports": "审计意见类型"}],
                }],
                "limitations": [], "reading_guide": "回看收入附注。",
            }),
            _FakeToolCall("finding", "save_disclosure_finding", {"finding": {
                "finding_id": "D1", "title": "收入确认", "topic": "关键审计事项",
                "evidence": [{"page": 1, "quote": "审计师将收入确认为关键审计事项"}],
            }}),
            _FakeToolCall("impact", "save_disclosure_impact", {"impact": {
                "impact_id": "I1", "finding_id": "D1", "target_module": "profit",
                "what_to_check": "复核收入确认附注。",
                "evidence_refs": [{"page": 1, "quote": "审计师将收入确认为关键审计事项"}],
            }}),
            _FakeToolCall("complete", "complete_disclosure_analysis", {}),
        ]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=_FakeAssistantMessage(calls))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100),
        )


class _FakeJsonMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = []

    def model_dump(self, **kwargs):
        return {"role": "assistant", "content": self.content}


class _FakeFinalJsonContext(_FakeAnalysisContext):
    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload

    def call_model(self, **request):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=_FakeJsonMessage(json.dumps(self.payload, ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
        )


class _FakeFollowUpContext(_FakeAnalysisContext):
    def call_model(self, **request):
        self.follow_up_request = request
        payload = {
            "answer": "年报原文将收入确认列为关键审计事项。",
            "evidence": [{
                "page": 1,
                "quote": "审计师将收入确认为关键审计事项",
                "supports": "年报将收入确认列为关键审计事项",
            }],
            "limitations": ["摘录只能证明年报披露该事项，不能单独证明收入确认存在错报。"],
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(message=_FakeJsonMessage(json.dumps(payload, ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=30),
        )


class ModuleSixDisclosureTests(unittest.TestCase):
    def test_checkpoint_adapter_keeps_partial_findings_and_interruption_reason(self):
        checkpoint = {
            "checkpoint_status": "partial",
            "submitted_overview": {
                "executive_summary": "已识别审计意见，专题分析未完成。",
                "audit_profile": {"opinion_type": "标准无保留意见"},
                "coverage": [{"topic": "审计意见", "status": "confirmed_present"}],
                "report_context": {"company_name": "测试公司"},
            },
            "findings": [{"finding_id": "F1", "title": "收入确认"}],
            "impacts": [{"impact_id": "I1", "target_module": "profit"}],
            "calculations": [{"name": "披露金额变化", "difference": None}],
            "read_pages": [1, 83],
            "completion_issues": ["下游模块尚无对应复核线索：profit"],
            "model_turns_completed": 4,
        }
        result = partial_result_from_checkpoint(checkpoint, "HTTP 402：余额不足")
        self.assertEqual(result["findings"][0]["title"], "收入确认")
        self.assertEqual(result["impacts"][0]["target_module"], "profit")
        self.assertEqual(result["calculations"][0]["difference"], None)
        self.assertEqual(result["read_pages"], [1, 83])
        self.assertEqual(result["partial_reason"], "HTTP 402：余额不足")
        self.assertIn("下游模块尚无对应复核线索：profit", result["run_quality"]["critical_gaps"])
        self.assertIn("不代表", result["run_quality"]["notice"])

    def test_insufficient_material_does_not_claim_audit_or_special_findings(self):
        result = insufficient_material_result("scan.pdf", 26)
        self.assertEqual(result["report_context"]["page_count"], 26)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["audit_profile"], {})
        self.assertTrue(any("掃描" in item or "扫描" in item for item in result["limitations"]))

    def test_follow_up_reloads_professional_guidance_result_and_current_pdf_evidence(self):
        context = _FakeFollowUpContext()
        result = answer_follow_up(
            context,
            initial_result={
                "module_id": "disclosure",
                "module_version": "test-version",
                "executive_summary": "本次结果已识别收入确认关键审计事项。",
                "findings": [{"finding_id": "F1", "title": "收入确认"}],
            },
            history=[{"status": "completed", "question": "此前问题", "answer": "此前答复"}],
            question="年报对收入确认有哪些审计关注？",
        )
        request = context.follow_up_request
        self.assertIn("模块六：披露可信度与特殊事项", request["messages"][0]["content"])
        self.assertIn("收入确认关键审计事项", request["messages"][1]["content"])
        self.assertIn("此前答复", request["messages"][1]["content"])
        self.assertEqual(result["read_pages"], [1])
        self.assertEqual(result["trace"][0]["evidence"][0]["verification"], "quote_present")
        self.assertIn("年报原文将收入确认列为关键审计事项", result["answer"])
        self.assertIn("不能单独证明", result["answer"])

    def test_deepseek_402_error_is_actionable_and_non_network(self):
        error_type = type("APIStatusError", (Exception,), {})
        error = error_type("provider response")
        error.response = SimpleNamespace(status_code=402)
        message = str(_safe_model_error(error))
        self.assertIn("HTTP 402", message)
        self.assertIn("余额不足", message)
        self.assertIn("本地检查点", message)

    def test_initial_pages_prioritise_audit_report_over_generic_financial_terms(self):
        pages = [
            {"page": 1, "text": "公司年度报告封面"},
            {"page": 20, "text": "主要会计数据和财务指标"},
            {"page": 40, "text": "审计报告 审计意见 形成审计意见的基础"},
            {"page": 41, "text": "关键审计事项 收入确认"},
        ]
        selected = _initial_pages(pages)
        self.assertIn(1, [item["page"] for item in selected])
        self.assertIn(40, [item["page"] for item in selected])
        self.assertIn(41, [item["page"] for item in selected])

    def test_cover_identity_and_audit_core_pages_are_explicit(self):
        pages = [
            {"page": 1, "text": "公司代码：600887 公司简称：伊利股份 内蒙古伊利实业集团股份有限公司 2024 年年度报告"},
            {"page": 82, "text": "财务报表目录"},
            {"page": 83, "text": "审计报告\n我们认为，财务报表在所有重大方面公允反映。"},
            {"page": 84, "text": "关键审计事项：收入确认。"},
        ]
        identity = _cover_identity(pages)
        self.assertEqual(identity["code"], "600887")
        self.assertEqual(identity["short_name"], "伊利股份")
        self.assertEqual(identity["company"], "内蒙古伊利实业集团股份有限公司")
        self.assertEqual(identity["report_year"], "2024年度")
        self.assertEqual([page["page"] for page in _mandatory_audit_pages(pages)], [83, 84])

    def test_amount_difference_requires_both_exact_quote_and_numeric_sources(self):
        context = _FakeContext({
            6: "2023年度合并利润表，营业收入原披露金额为800万元（人民币）。",
            7: "2024年度合并利润表，营业收入披露金额为1000万元（人民币）。",
        })
        args = {
            "metric_name": "营业收入",
            "earlier_value": "800",
            "later_value": "1000",
            "unit": "万元",
            "currency": "人民币",
            "earlier_period": "2023年度",
            "later_period": "2024年度",
            "earlier_basis": "原披露",
            "later_basis": "原披露",
            "reporting_scope": "合并报表",
            "earlier_source": {"page": 6, "quote": "2023年度合并利润表，营业收入原披露金额为800万元（人民币）。"},
            "later_source": {"page": 7, "quote": "2024年度合并利润表，营业收入披露金额为1000万元（人民币）。"},
        }
        result = _reported_difference(context, args, {6, 7}, [])
        self.assertEqual(result["difference"], "200")
        self.assertEqual(result["difference_yuan"], "2000000")
        self.assertEqual(result["percentage_change"], "25")
        self.assertEqual(len(context.calculations), 1)

    def test_zero_base_reports_amount_difference_without_percentage(self):
        context = _FakeContext({
            1: "2024年末合并报表预计负债期初金额为0万元（人民币）。",
            2: "2024年末合并报表预计负债调整后金额为15万元（人民币）。",
        })
        args = {
            "metric_name": "预计负债",
            "earlier_value": "0",
            "later_value": "15",
            "unit": "万元",
            "currency": "CNY",
            "earlier_period": "2024年末",
            "later_period": "2024年末",
            "earlier_basis": "调整前",
            "later_basis": "调整后",
            "reporting_scope": "合并报表",
            "earlier_source": {"page": 1, "quote": "2024年末合并报表预计负债期初金额为0万元（人民币）。"},
            "later_source": {"page": 2, "quote": "2024年末合并报表预计负债调整后金额为15万元（人民币）。"},
        }
        result = _reported_difference(context, args, {1, 2}, [])
        self.assertEqual(result["difference"], "15")
        self.assertIsNone(result["percentage_change"])
        self.assertEqual(result["percentage_status"], "not_computed_nonpositive_or_zero_base")

    def test_negative_amount_difference_preserves_statement_sign(self):
        context = _FakeContext({
            1: "2023年度合并利润表资产减值损失（损失以负号列示）为-10万元（人民币）。",
            2: "2024年度合并利润表资产减值损失（损失以负号列示）为-20万元（人民币）。",
        })
        args = {
            "metric_name": "资产减值损失", "earlier_value": "-10", "later_value": "-20",
            "unit": "万元", "currency": "人民币", "earlier_period": "2023年度", "later_period": "2024年度",
            "earlier_basis": "合并利润表", "later_basis": "合并利润表", "reporting_scope": "合并报表",
            "earlier_source": {"page": 1, "quote": "2023年度合并利润表资产减值损失（损失以负号列示）为-10万元（人民币）。"},
            "later_source": {"page": 2, "quote": "2024年度合并利润表资产减值损失（损失以负号列示）为-20万元（人民币）。"},
        }
        result = _reported_difference(context, args, {1, 2}, [])
        self.assertEqual(result["difference"], "-10")
        self.assertIsNone(result["percentage_change"])
        args["later_value"] = "20"
        with self.assertRaisesRegex(ValueError, "没有找到所报金额"):
            _reported_difference(context, args, {1, 2}, [])

    def test_amount_match_keeps_adjacent_table_values_separate(self):
        quote = "合并资产负债表（单位：人民币元）商誉七（27）2024年末：2,336,555,144.44；2023年末：5,160,099,488.23"
        context = _FakeContext({1: quote})
        args = {
            "metric_name": "商誉账面价值", "earlier_value": "5,160,099,488.23", "later_value": "2,336,555,144.44",
            "unit": "元", "currency": "人民币", "earlier_period": "2023年末", "later_period": "2024年末",
            "earlier_basis": "合并资产负债表", "later_basis": "合并资产负债表", "reporting_scope": "合并报表",
            "earlier_source": {"page": 1, "quote": quote}, "later_source": {"page": 1, "quote": quote},
        }
        result = _reported_difference(context, args, {1}, [])
        self.assertEqual(result["difference"], "-2823544343.79")

    def test_amount_calculation_rejects_values_reversed_against_period_columns(self):
        quote = "合并资产负债表（单位：人民币元）2024年末 2023年末 商誉七（27）5,160,099,488.23 2,336,555,144.44"
        context = _FakeContext({1: quote})
        args = {
            "metric_name": "商誉账面价值", "earlier_value": "5,160,099,488.23", "later_value": "2,336,555,144.44",
            "unit": "元", "currency": "人民币", "earlier_period": "2023年末", "later_period": "2024年末",
            "earlier_basis": "合并资产负债表", "later_basis": "合并资产负债表", "reporting_scope": "合并报表",
            "earlier_source": {"page": 1, "quote": quote}, "later_source": {"page": 1, "quote": quote},
        }
        with self.assertRaisesRegex(ValueError, "年份列顺序与指标金额顺序不一致"):
            _reported_difference(context, args, {1}, [])
        self.assertEqual(context.calculations, [])

    def test_summary_material_topics_must_have_their_own_findings(self):
        summary = "担保总额102亿元，非经常性损益来自子公司处置。"
        self.assertEqual(
            _summary_topics_without_findings(summary, []),
            ["对外担保", "非经常性损益/资产处置"],
        )
        findings = [
            {"title": "对外担保余额", "topic": "担保与或有事项"},
            {"title": "非经常性损益", "disclosed_facts": "资产处置收益"},
        ]
        self.assertEqual(_summary_topics_without_findings(summary, findings), [])

    def test_summary_that_explicitly_marks_a_topic_not_checked_is_not_a_finding_gap(self):
        summary = "关联方及关联交易两主题本轮未查证（not_checked），不作为有/无结论。"
        self.assertEqual(_summary_topics_without_findings(summary, []), [])
        material_fact = "关联方交易金额500万元，本轮未查证。"
        self.assertEqual(_summary_topics_without_findings(material_fact, []), ["关联方交易"])

    def test_related_party_guarantee_does_not_cover_related_party_transaction(self):
        summary = "存在关联担保4,000万元并发生关联方交易。"
        findings = [{"title": "关联担保", "topic": "担保与或有事项", "disclosed_facts": "关联方提供担保。"}]
        self.assertEqual(_summary_topics_without_findings(summary, findings), ["关联方交易"])

    def test_only_findings_assigned_to_analysis_modules_require_impact_handoffs(self):
        findings = [
            {"finding_id": "audit", "related_modules": ["综合判断"]},
            {"finding_id": "profit", "related_modules": ["profit", "综合判断"]},
            {"finding_id": "unknown", "related_modules": ["unknown"]},
        ]
        self.assertEqual(_findings_requiring_impacts(findings), {"profit"})

    def test_default_model_budget_reserves_turns_for_findings_and_handoffs(self):
        self.assertEqual(_research_turn_budget(18), 6)
        self.assertEqual(_research_turn_budget(30), 6)
        self.assertEqual(_research_turn_budget(4), 1)

    def test_calculation_refuses_unseen_page_or_quote_without_amount(self):
        context = _FakeContext({
            1: "2023年度合并利润表营业收入为80万元（人民币）。",
            2: "2024年度合并利润表营业收入为100万元（人民币）。",
        })
        args = {
            "metric_name": "营业收入", "earlier_value": "80", "later_value": "100",
            "unit": "万元", "currency": "人民币", "earlier_period": "2023年度", "later_period": "2024年度",
            "earlier_basis": "原披露", "later_basis": "原披露", "reporting_scope": "合并报表",
            "earlier_source": {"page": 1, "quote": "2023年度合并利润表营业收入为80万元（人民币）。"},
            "later_source": {"page": 2, "quote": "2024年度合并利润表营业收入为100万元（人民币）。"},
        }
        with self.assertRaisesRegex(ValueError, "尚未读取或检索"):
            _reported_difference(context, args, {1}, [])
        context.pages[2] = "2024年度收入为 100 万元，报告显示调整口径按年报附注所列披露。"
        args["later_source"] = {"page": 2, "quote": "报告显示调整口径按年报附注所列披露。"}
        with self.assertRaisesRegex(ValueError, "没有找到所报金额"):
            _reported_difference(context, args, {1, 2}, [])
        self.assertEqual(context.calculations, [])

    def test_amount_calculation_rejects_unit_period_and_reporting_scope_mismatches(self):
        pages = {
            1: "2023年度合并利润表营业收入为80亿元（人民币）。",
            2: "2024年度母公司利润表营业收入为100万元（人民币）。",
        }
        context = _FakeContext(pages)
        args = {
            "metric_name": "营业收入", "earlier_value": "80", "later_value": "100",
            "unit": "万元", "currency": "人民币", "earlier_period": "2023年度", "later_period": "2024年度",
            "earlier_basis": "原披露", "later_basis": "原披露", "reporting_scope": "合并报表",
            "earlier_source": {"page": 1, "quote": pages[1]}, "later_source": {"page": 2, "quote": pages[2]},
        }
        with self.assertRaisesRegex(ValueError, "单位缺失或与计算单位不符"):
            _reported_difference(context, args, {1, 2}, [])
        args["unit"] = "亿元"
        args["earlier_period"] = "2022年度"
        with self.assertRaisesRegex(ValueError, "期间年份"):
            _reported_difference(context, args, {1, 2}, [])
        args["earlier_period"] = "2023年度"
        args["unit"] = "万元"
        args["earlier_value"] = "80000"
        context.pages[1] = "2023年度合并利润表营业收入为80000万元（人民币）。"
        args["earlier_source"] = {"page": 1, "quote": context.pages[1]}
        with self.assertRaisesRegex(ValueError, "范围缺失、含糊或与计算范围不符"):
            _reported_difference(context, args, {1, 2}, [])
        self.assertEqual(context.calculations, [])

    def test_amount_calculation_accepts_separate_table_header_evidence(self):
        context = _FakeContext({
            1: "2023年度营业收入原披露金额为80。",
            2: "金额单位：人民币万元。合并利润表。",
            3: "2024年度营业收入原披露金额为100。",
            4: "金额单位：人民币万元。合并利润表。",
        })
        args = {
            "metric_name": "营业收入", "earlier_value": "80", "later_value": "100",
            "unit": "万元", "currency": "CNY", "earlier_period": "2023年度", "later_period": "2024年度",
            "earlier_basis": "原披露", "later_basis": "原披露", "reporting_scope": "合并报表",
            "earlier_source": {"page": 1, "quote": context.pages[1], "evidence": [{"page": 2, "quote": context.pages[2]}]},
            "later_source": {"page": 3, "quote": context.pages[3], "evidence": [{"page": 4, "quote": context.pages[4]}]},
        }
        result = _reported_difference(context, args, {1, 2, 3, 4}, [])
        self.assertEqual(result["difference"], "20")
        self.assertEqual(len(result["source_evidence"][0]["evidence"]), 1)

    def test_explicit_no_disclosure_must_negate_the_named_topic(self):
        unrelated_negative = [{
            "verification": "quote_present",
            "quote": "本期关联方交易金额为500万元；关联方担保不适用。",
        }]
        direct_negative = [{
            "verification": "quote_present",
            "quote": "本期关联方交易不适用。",
        }]
        self.assertFalse(_coverage_status_supported("关联方交易", "explicit_no_disclosure", unrelated_negative)[0])
        self.assertTrue(_coverage_status_supported("关联方交易", "explicit_no_disclosure", direct_negative)[0])

    def test_result_preserves_special_fields_and_marks_quote_verification(self):
        context = _FakeContext({4: "因存货可变现净值估计存在不确定性，审计师将其列为关键审计事项。"})
        raw = {
            "executive_summary": "审计意见为无保留。",
            "audit_profile": {"opinion_type": "无保留意见", "custom_detail": "保留模块六字段"},
            "findings": [{
                "finding_id": "D-1", "title": "存货估值", "custom_visual_block": {"kind": "timeline"},
                "evidence": [
                    {"page": 4, "quote": "因存货可变现净值估计存在不确定性"},
                    {"page": 4, "quote": "因存货可变现净值……估计"},
                ],
            }],
            "impacts": [], "coverage": [{"topic": "审计意见", "status": "confirmed_present"}],
            "limitations": [], "reading_guide": "回看附注。",
        }
        result = _normalise_result(raw, context=context, seen_pages={4}, calculations=[], turns=3, usage={"prompt_tokens": 1, "completion_tokens": 1})
        self.assertEqual(result["audit_profile"]["custom_detail"], "保留模块六字段")
        self.assertEqual(result["findings"][0]["custom_visual_block"]["kind"], "timeline")
        self.assertEqual(result["findings"][0]["evidence_status"], "partially_verified")
        self.assertEqual(result["findings"][0]["evidence"][0]["verification"], "quote_present")
        self.assertEqual(result["findings"][0]["evidence"][1]["verification"], "quote_uses_ellipsis")

    def test_missing_coverage_evidence_is_downgraded_and_impact_links_source_finding(self):
        context = _FakeContext({4: "审计师将存货可变现净值估计列为关键审计事项。"})
        raw = {
            "report_context": {"company_name": "测试公司", "stock_code": "600000"},
            "audit_profile": {"opinion_type": "标准无保留意见"},
            "findings": [{
                "finding_id": "F1", "title": "存货估值", "evidence": [{
                    "page": 4, "quote": "审计师将存货可变现净值估计列为关键审计事项。",
                    "supports": "审计关注事项",
                }],
            }],
            "impacts": [{"impact_id": "I1", "finding_id": "F1", "target_module": "assets", "evidence_refs": []}],
            "coverage": [{"topic": "存货估值", "status": "confirmed_present", "basis": "PDF第4页"}],
        }
        result = _normalise_result(
            raw, context=context, seen_pages={4}, calculations=[], turns=1,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
        self.assertEqual(result["report_context"]["company"], "测试公司")
        self.assertEqual(result["report_context"]["code"], "600000")
        self.assertEqual(result["coverage"][0]["reported_status"], "confirmed_present")
        self.assertEqual(result["coverage"][0]["status"], "not_checked")
        self.assertEqual(result["impacts"][0]["evidence_status"], "linked_finding_reference_only")
        self.assertEqual(result["impacts"][0]["evidence_refs"][0]["reference_scope"], "linked_finding_not_direct_impact_proof")

    def test_compacted_messages_do_not_replay_full_transcript(self):
        context = SimpleNamespace(file_name="report.pdf", page_count=10)
        trace = [{
            "tool": "read_report_pages", "result": {"pages": [{"page": 2, "text": "原文" * 3000}]},
        }]
        messages = _compact_request_messages(
            "专业工作流", context=context, seen_pages={2}, trace=trace, related_results={},
            identity_hint={}, audit_core_pages=[],
            submitted_overview=None, submitted_findings=[], submitted_impacts=[],
            provisional_result=None, last_model_response="", next_instruction="继续核查",
        )
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        state = json.loads(messages[1]["content"])
        recent_page = state["recent_search_and_page_evidence"][0]["pages"][0]
        self.assertEqual(len(recent_page["text"]), 2_200)
        self.assertTrue(recent_page["text_truncated_for_context"])

    def test_module_saves_summary_findings_impacts_in_separate_tools(self):
        context = _FakeAnalysisContext()
        workflow = analyze_disclosure_report(context)
        result = workflow["result"]
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(len(result["impacts"]), 1)
        self.assertEqual(result["findings"][0]["evidence_status"], "verified_quote")
        self.assertEqual(result["impacts"][0]["evidence_refs"][0]["verification"], "quote_present")
        self.assertEqual(context.events.count("complete_disclosure_analysis"), 1)
        self.assertEqual(context.artifacts["disclosure_checkpoint"]["checkpoint_status"], "completed")
        self.assertIn("save_disclosure_overview", context.last_tool_names)
        self.assertIn("save_disclosure_finding", context.last_tool_names)

    def test_final_json_cannot_bypass_handoff_or_coverage_gate(self):
        audit_quote = "审计报告：标准无保留意见。"
        finding_quote = "审计师将收入确认为关键审计事项"
        payload = {
            "report_context": {"company_name": "测试公司", "report_year": "2024年度"},
            "executive_summary": "本年报披露标准无保留审计意见，并将收入确认为关键审计事项；本结论仅覆盖已回查的原文范围。",
            "audit_profile": {
                "opinion_type": "标准无保留意见",
                "opinion_excerpt": audit_quote,
                "evidence": [{"page": 1, "quote": audit_quote}],
            },
            "coverage": [
                {"topic": "审计意见", "status": "confirmed_present", "evidence": [{"page": 1, "quote": audit_quote}]},
                {"topic": "债务重组", "status": "explicit_no_disclosure", "evidence": [{"page": 1, "quote": audit_quote}]},
            ],
            "limitations": [],
            "reading_guide": "继续核对收入附注和债务重组附注。",
            "findings": [{
                "finding_id": "F1", "title": "收入确认关键审计事项", "related_modules": ["profit"],
                "evidence": [{"page": 1, "quote": finding_quote}],
            }],
            "impacts": [],
        }
        context = _FakeFinalJsonContext(payload)
        with self.assertRaisesRegex(Exception, "下游模块尚无对应复核线索") as raised:
            analyze_disclosure_report(context, max_model_turns=3)
        self.assertIn("债务重组", str(raised.exception))
        checkpoint = context.artifacts["disclosure_checkpoint"]
        self.assertEqual(checkpoint["checkpoint_status"], "partial")
        self.assertIn("provisional_result", checkpoint)


if __name__ == "__main__":
    unittest.main()
