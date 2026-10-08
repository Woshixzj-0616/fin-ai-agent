from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend.app as app
from backend.modules.profit import engine


class ProfitWorkbenchRecoveryTests(unittest.TestCase):
    def test_insufficient_data_is_not_reported_as_zero_or_complete(self):
        result = app._insufficient_profit_result("PDF 没有可提取文字。")

        self.assertEqual(result["module"], "profit")
        self.assertEqual(result["analysis_completion_status"], "insufficient_data")
        self.assertIsNone(result["profit_bridge"]["target_change_yuan"])
        self.assertEqual(result["profit_lines"], [])
        self.assertEqual(result["uncertainties"], ["PDF 没有可提取文字。"])

    def test_recovery_prefers_latest_saved_facts_and_marks_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            original_root = app.ROOT
            try:
                app.ROOT = Path(directory)
                artifacts = (
                    Path(directory)
                    / "data"
                    / "app"
                    / "run_records"
                    / "test-run"
                    / "profit"
                    / "attempt-0001"
                    / "artifacts"
                )
                artifacts.mkdir(parents=True)
                (artifacts / "module2_v2_initial_facts.json").write_text(
                    json.dumps({"module": "profit", "summary": "初始事实", "profit_lines": [], "uncertainties": []}),
                    encoding="utf-8",
                )
                (artifacts / "module2_v2_repaired_facts.json").write_text(
                    json.dumps({"module": "profit", "summary": "补查事实", "profit_lines": [{"label": "营业收入"}], "uncertainties": []}),
                    encoding="utf-8",
                )

                result = app._recover_profit_partial_result("test-run", "连接超时")

                self.assertEqual(result["summary"], "补查事实")
                self.assertEqual(result["analysis_completion_status"], "partial")
                self.assertEqual(result["analysis_review_status"], "模型阶段中断；已保留中断前可用结果，仍需复核")
                self.assertIn("连接超时", result["interruption_message"])
                self.assertTrue(any("中断前" in item for item in result["uncertainties"]))
            finally:
                app.ROOT = original_root

    def test_recovery_does_not_accept_another_modules_result(self):
        with tempfile.TemporaryDirectory() as directory:
            original_root = app.ROOT
            try:
                app.ROOT = Path(directory)
                artifacts = (
                    Path(directory)
                    / "data"
                    / "app"
                    / "run_records"
                    / "test-run"
                    / "profit"
                    / "attempt-0001"
                    / "artifacts"
                )
                artifacts.mkdir(parents=True)
                (artifacts / "module2_v2_initial_facts.json").write_text(
                    json.dumps({"module": "business", "summary": "不应接受"}),
                    encoding="utf-8",
                )

                self.assertIsNone(app._recover_profit_partial_result("test-run", "模型中断"))
            finally:
                app.ROOT = original_root


class ProfitFollowUpContextTests(unittest.TestCase):
    def test_follow_up_receives_saved_profit_result_rules_and_report_tools(self):
        captured = {}

        def fake_tool_loop(**kwargs):
            captured.update(kwargs)
            return (
                "营业收入为程序已保存数值；本轮查阅 PDF 第 2 页。",
                [{"tool": "read_pdf_page", "pages": [2]}],
                {"_tool_pages": [2], "completion_tokens": 7},
            )

        get_page = lambda page_number: f"第{page_number}页原文"
        search_pages = lambda query: [{"page": 2, "text": "营业收入"}]
        result = {
            "company": "示例公司",
            "period": "2024 年",
            "reporting_scope": "合并报表",
            "summary": "利润变化摘要",
            "profit_lines": [{"label": "营业收入", "current_value": "123"}],
            "profit_metrics": [{"name": "营业毛利率", "current_value": "30"}],
            "profit_bridge": {"residual_yuan": "0"},
            "business_segments": [{"segment_name": "示例产品"}],
            "nonrecurring_items": [{"label": "政府补助"}],
            "findings": [{"title": "利润变化"}],
            "uncertainties": ["分部口径待核"],
            "read_pages": [1],
        }

        with patch.object(engine, "_run_tool_loop", side_effect=fake_tool_loop):
            answer = engine.answer_profit_question(
                file_name="示例年报.pdf",
                page_count=10,
                initial_result=result,
                history=[{"status": "completed", "question": "上一次追问", "answer": "上一次回答"}],
                question="营业收入变化能说明什么？",
                get_page=get_page,
                search_pages=search_pages,
            )

        self.assertIn("先区分合并净利润、归母净利润和扣非归母净利润", captured["messages"][0]["content"])
        user_message = captured["messages"][1]["content"]
        context_text = user_message.split("以下是完整上下文：\n", 1)[1].split("\n\n本轮问题：", 1)[0]
        context = json.loads(context_text)
        self.assertEqual(context["profit_lines"], result["profit_lines"])
        self.assertEqual(context["profit_bridge"], result["profit_bridge"])
        self.assertEqual(context["business_segments"], result["business_segments"])
        self.assertEqual(context["nonrecurring_items"], result["nonrecurring_items"])
        self.assertEqual(context["recent_questions_and_answers"][0]["question"], "上一次追问")
        self.assertIs(captured["get_page"], get_page)
        self.assertIs(captured["search_pages"], search_pages)
        self.assertEqual(captured["available_tools"], engine.TOOLS[:2])
        self.assertEqual(captured["max_rounds"], 3)
        self.assertEqual(answer["read_pages"], [2])
        self.assertEqual(answer["unsupported_pages"], [])


if __name__ == "__main__":
    unittest.main()
