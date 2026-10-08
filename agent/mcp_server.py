"""MCP stdio server：把 tools.py 的只读工具按 Model Context Protocol 暴露。

对齐竞赛技术硬清单「源码含编排/Tool/Prompt/Skill/MCP/日志」中的 MCP 一项。

协议
----
JSON-RPC 2.0 over stdio，实现 MCP 子集：
  initialize / notifications/initialized / tools/list / tools/call / ping

设计约束
--------
- 工具实现只读进 `agent/tools.py`（唯一出口），本文件不复制业务逻辑；
- `tools/list` 直接读 `tool_specs()`，与 JSON 多步协议、Skill 文档同一套定义；
- facts 由调用方加载（--facts / load_facts 工具），服务不隐式读库。

启动
----
    python agent/mcp_server.py --facts results/evidence.json
    python agent/mcp_server.py --facts results/evidence.json --self-test

作为 MCP 服务器时保持 stdin/stdout 为 JSON-RPC，日志走 stderr。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools import dispatch, tool_specs

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "fin-report-audit", "version": "1.0.0"}

# 除 tools.py 七个工具外，MCP 侧额外的会话工具
EXTRA_TOOLS = [
    {
        "name": "load_facts",
        "description": "加载证据 JSON（list[dict]）到当前会话。路径相对仓库根或绝对路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "evidence.json 路径"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "session_info",
        "description": "返回当前会话已加载证据条数、公司/指标覆盖与源码指纹提示。",
        "parameters": {"type": "object", "properties": {}},
    },
]


def _log(*parts: Any) -> None:
    print(*parts, file=sys.stderr, flush=True)


class Session:
    """一次 MCP 会话的证据上下文。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd()
        self.facts: list[dict] = []
        self.document_texts: dict[str, list[str]] = {}
        self.draft: str = ""
        self.facts_path: str | None = None

    def load_facts(self, path: str) -> dict:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        if not p.is_file():
            return {"status": "error", "error": f"文件不存在：{p}"}
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return {"status": "error", "error": f"读取失败：{exc}"}
        if isinstance(data, dict) and "facts" in data:
            data = data["facts"]
        if not isinstance(data, list):
            return {"status": "error", "error": "证据文件须为 list 或 {facts: list}"}
        self.facts = data
        self.facts_path = str(p)
        return {"status": "ok", "count": len(self.facts), "path": str(p)}

    def info(self) -> dict:
        companies = sorted({f.get("company_code") for f in self.facts if f.get("company_code")})
        metrics = sorted({f.get("metric") for f in self.facts if f.get("metric")})
        return {
            "status": "ok",
            "facts_loaded": len(self.facts),
            "facts_path": self.facts_path,
            "company_codes": companies,
            "metrics": metrics,
            "document_texts_keys": sorted(self.document_texts),
        }


def all_tool_specs() -> list[dict]:
    return list(tool_specs()) + EXTRA_TOOLS


def call_tool(session: Session, name: str, arguments: dict | None) -> dict:
    args = arguments or {}
    if name == "load_facts":
        return session.load_facts(str(args.get("path", "")))
    if name == "session_info":
        return session.info()
    return dispatch(name, session.facts, args,
                    document_texts=session.document_texts,
                    draft=session.draft)


def handle(session: Session, request: dict) -> dict | None:
    """处理单条 JSON-RPC 请求；通知返回 None。"""
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": all_tool_specs()})
    if method == "tools/call":
        name = params.get("name")
        if not name:
            return err(-32602, "tools/call 缺少 name")
        try:
            result = call_tool(session, name, params.get("arguments"))
        except Exception as exc:  # noqa: BLE001 — 对模型侧不抛栈
            result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        # MCP tools/call 包一层 content；业务 status 仍保留在 structuredContent
        text = json.dumps(result, ensure_ascii=False)
        return ok({
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": bool(result.get("status") == "error"),
        })
    if req_id is None:
        return None
    return err(-32601, f"未知方法：{method}")


def serve_stdio(session: Session) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _log("stdin 不是合法 JSON，已跳过")
            continue
        response = handle(session, request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def self_test(facts_path: Path) -> int:
    """无客户端冒烟：initialize → tools/list → tools/call，打印结果。"""
    session = Session(root=Path.cwd())
    steps = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "load_facts", "arguments": {"path": str(facts_path)}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "session_info", "arguments": {}}},
    ]
    # 有 facts 时再打一次真实工具
    if facts_path.is_file():
        steps.append({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "list_catalog", "arguments": {"kind": "metrics"}}})
    ok = True
    for req in steps:
        resp = handle(session, req)
        print(json.dumps(resp, ensure_ascii=False), flush=True)
        if resp and resp.get("error"):
            ok = False
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fin-report-audit MCP server (stdio)")
    parser.add_argument("--facts", default="results/evidence.json",
                        help="启动时加载的证据 JSON（默认 results/evidence.json）")
    parser.add_argument("--self-test", action="store_true",
                        help="跑一遍 initialize/tools 冒烟后退出")
    parser.add_argument("--root", default=None, help="仓库根（默认 cwd）")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root / "agent") if (root / "agent").is_dir() else str(root))

    session = Session(root=root)
    if args.facts:
        loaded = session.load_facts(args.facts)
        if loaded.get("status") == "ok":
            _log(f"facts loaded: {loaded['count']} from {loaded['path']}")
        else:
            _log(f"facts not preloaded: {loaded.get('error')}")

    if args.self_test:
        return self_test(root / args.facts)
    return serve_stdio(session)


if __name__ == "__main__":
    # 保证 `python agent/mcp_server.py` 可找到 agent 内同级模块
    agent_dir = Path(__file__).resolve().parent
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    raise SystemExit(main())
