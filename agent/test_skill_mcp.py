"""Skill/MCP 骨架回归：协议帧、工具同源、Skill 文档与 tool_specs 不漂移。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp_server import EXTRA_TOOLS, all_tool_specs, handle, Session
from tools import TOOL_NAMES, tool_specs


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(root=Path.cwd())

    def test_initialize_advertises_tools_capability(self):
        resp = handle(self.session, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        })
        self.assertIsNone(resp.get("error"))
        result = resp["result"]
        self.assertEqual(result["serverInfo"]["name"], "fin-report-audit")
        self.assertIn("tools", result["capabilities"])

    def test_tools_list_is_same_contract_as_tool_specs(self):
        resp = handle(self.session, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [t["name"] for t in resp["result"]["tools"]]
        for name in TOOL_NAMES:
            self.assertIn(name, names)
        # 业务七个工具的 parameters 与 tools.tool_specs 逐字段一致（不许双份漂移）
        listed = {t["name"]: t for t in resp["result"]["tools"]}
        for spec in tool_specs():
            self.assertEqual(listed[spec["name"]], spec)

    def test_unknown_method_is_jsonrpc_error(self):
        resp = handle(self.session, {"jsonrpc": "2.0", "id": 3, "method": "nope/v1"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notification_returns_none(self):
        self.assertIsNone(handle(self.session, {
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }))

    def test_tools_call_list_catalog_without_facts(self):
        resp = handle(self.session, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "list_catalog", "arguments": {"kind": "operators"}},
        })
        result = resp["result"]
        self.assertFalse(result["isError"])
        payload = result["structuredContent"]
        self.assertEqual(payload["status"], "ok")
        self.assertIn("eq", json.dumps(payload, ensure_ascii=False))

    def test_load_facts_and_session_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.json"
            path.write_text(json.dumps([{
                "evidence_id": "e1", "company_code": "600519", "company_name": "贵州茅台",
                "metric": "revenue", "period_year": 2024, "value": "100", "unit": "元",
                "normalized_value": "100", "normalized_unit": "元", "page": 1,
            }], ensure_ascii=False), encoding="utf-8")
            resp = handle(self.session, {
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "load_facts", "arguments": {"path": str(path)}},
            })
            self.assertEqual(resp["result"]["structuredContent"]["status"], "ok")
            self.assertEqual(len(self.session.facts), 1)
            info = handle(self.session, {
                "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "session_info", "arguments": {}},
            })["result"]["structuredContent"]
            self.assertEqual(info["facts_loaded"], 1)
            self.assertIn("600519", info["company_codes"])

    def test_tools_call_error_is_isError_not_crash(self):
        resp = handle(self.session, {
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "not_a_tool", "arguments": {}},
        })
        self.assertTrue(resp["result"]["isError"])
        self.assertEqual(resp["result"]["structuredContent"]["status"], "error")

    def test_compare_claim_needs_facts_is_review_not_crash(self):
        resp = handle(self.session, {
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "compare_claim", "arguments": {
                "company_name_or_code": "600519", "metric": "revenue",
                "period_year": 2024, "kind": "amount",
                "claimed_value": "100", "claimed_unit": "元", "operator": "eq",
            }},
        })
        payload = resp["result"]["structuredContent"]
        self.assertIn(payload["verdict"], {"needs_review", "evidence_supported", "confirmed_error"})


class SkillContractTests(unittest.TestCase):
    """Skill 文档必须与代码契约同步——防「空造文件 / 文档漂移」。"""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.skill = (cls.root / "skills" / "fin-report-audit" / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_file_exists_and_declares_name(self):
        self.assertIn("fin-report-audit", self.skill)
        self.assertIn("compare_claim", self.skill)

    def test_skill_lists_every_tool_name(self):
        for name in TOOL_NAMES:
            self.assertIn(f"`{name}`", self.skill)

    def test_skill_states_iron_rules(self):
        for phrase in ("模型只拆句", "唯一", "needs_review", "evidence_id"):
            self.assertIn(phrase, self.skill)

    def test_tool_contract_reference_mentions_dispatch_tools(self):
        text = (self.root / "skills" / "fin-report-audit" / "references" / "tool-contract.md").read_text(encoding="utf-8")
        for name in TOOL_NAMES:
            self.assertIn(name, text)

    def test_mcp_server_module_exports_extra_tools(self):
        names = [t["name"] for t in EXTRA_TOOLS]
        self.assertEqual(names, ["load_facts", "session_info"])
        self.assertEqual(len(all_tool_specs()), len(TOOL_NAMES) + len(EXTRA_TOOLS))


if __name__ == "__main__":
    unittest.main()
