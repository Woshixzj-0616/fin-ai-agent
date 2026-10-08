"""JSON 多步工具协议：语义层请求工具 → 本地执行 → 回灌结果 → 直到交待核主张。

不依赖模型的 tool_calls 字段，任何能出 JSON 的 chat/completions 都能跑。

每轮模型只能二选一：
  {"action": "call_tools", "tool_calls": [{"name": "...", "arguments": {...}}]}
  {"action": "submit_claims", "items": [...], "unclaimed_sentences": [...]}

铁律
----
- compare_claim 的 verdict 模型不得改写；
- 工具 empty/ambiguous ⇒ 只能 needs_review；
- 轮次与调用次数有上限，超限强制收束。
"""
from __future__ import annotations

import json
from collections import Counter

from llm_check import LLMError, schema, validate_schema
from tools import dispatch, tool_specs

MAX_ROUNDS = 5
MAX_TOOL_CALLS = 30
LOOP_SYSTEM = """现在进入核验执行阶段。你通过 JSON 请求工具，本地程序执行后把结果交回给你。

可调用工具（name → 作用）：
- list_catalog(kind)：查公司/指标/单位/运算符目录
- find_evidence(company_name_or_code, metric, period_year[, source_report_year])：取年报证据
- compute_yoy(company_name_or_code, metric, year)：算同比
- compare_claim(...)：**唯一**能产生 证据支持/确认错误 的入口
- search_text(company_name_or_code, source_report_year, query)：搜原文；不能单独定罪

规则：
1. 对每个 amount/yoy 主张，必须先 find_evidence 或 compare_claim，禁止空谈结论。
2. 不得改写 compare_claim 返回的 verdict。
3. 工具返回 empty/ambiguous/error 时，该主张 verification_action=needs_review。
4. 整篇工具调用不超过 {max_tools} 次；轮次不超过 {max_rounds}。
5. 就绪后只输出 submit_claims，items 必须符合主协议 JSON Schema。

每轮输出只能是下面两种 JSON 之一（不要 markdown 代码块）：
{{"action":"call_tools","tool_calls":[{{"name":"find_evidence","arguments":{{"company_name_or_code":"贵州茅台","metric":"revenue","period_year":2024}}}}]}}
{{"action":"submit_claims","items":[...],"unclaimed_sentences":[...]}}
""".format(max_tools=MAX_TOOL_CALLS, max_rounds=MAX_ROUNDS)


def parse_loop_reply(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise LLMError("模型回复不是合法 JSON 多步协议帧") from exc
    if not isinstance(payload, dict) or payload.get("action") not in {"call_tools", "submit_claims"}:
        raise LLMError("模型回复缺少合法 action（call_tools / submit_claims）")
    return payload


def run_loop(client, draft: str, facts: list[dict], run,
             sentences: list[dict] | None = None,
             document_texts: dict | None = None) -> dict:
    """跑多步协议，返回与 check_payload 兼容的 payload + 工具审计。"""
    tools_used: list[dict] = []
    tool_budget = MAX_TOOL_CALLS
    messages = [
        {"role": "system", "content": LOOP_SYSTEM},
        {"role": "user", "content": json.dumps(
            {"task": "parse_and_verify", "draft": draft,
             "sentences": sentences or [], "tool_specs": tool_specs()},
            ensure_ascii=False)},
    ]
    run.event("agent_loop_start", max_rounds=MAX_ROUNDS, max_tools=MAX_TOOL_CALLS)

    for round_no in range(1, MAX_ROUNDS + 1):
        raw = client.chat(messages, run)  # 见 LLMClient.chat（P3 新增）
        frame = parse_loop_reply(raw)
        run.event("agent_loop_round", round=round_no, action=frame["action"])

        if frame["action"] == "call_tools":
            calls = frame.get("tool_calls") or []
            if not isinstance(calls, list) or not calls:
                raise LLMError("call_tools 未带 tool_calls")
            results = []
            for call in calls:
                if tool_budget <= 0:
                    results.append({"name": call.get("name"),
                                    "result": {"status": "error", "error": "工具调用预算已用尽"}})
                    continue
                name = call.get("name")
                args = call.get("arguments") or {}
                result = dispatch(name, facts, args, document_texts=document_texts, draft=draft)
                tool_budget -= 1
                tools_used.append({"round": round_no, "name": name,
                                   "arguments": args, "status": result.get("status")})
                run.event("agent_tool", round=round_no, tool=name,
                          status=result.get("status"), budget_left=tool_budget,
                          arguments=args, result=result, evidence_ids=result.get("evidence_ids", []))
                results.append({"name": name, "arguments": args, "result": result})
            messages.append({"role": "assistant", "content": json.dumps(frame, ensure_ascii=False)})
            messages.append({"role": "user", "content": json.dumps(
                {"tool_results": results, "budget_left": tool_budget}, ensure_ascii=False)})
            continue

        items = frame.get("items") or []
        payload = {"items": items,
                   "unclaimed_sentences": frame.get("unclaimed_sentences") or []}
        validate_schema(payload, schema())
        run.event("agent_loop_submit", items=len(items), tools_used=len(tools_used))
        return {"payload": payload, "tools_used": tools_used,
                "rounds": round_no, "budget_left": tool_budget,
                "mode": "json_multi_step"}

    # 轮次用尽仍未 submit：强制收束为无主张，全部进人工
    raise LLMError(f"工具循环超过 {MAX_ROUNDS} 轮仍未提交主张；请拆短草稿后重试")


def tool_summary(tools_used: list[dict]) -> str:
    if not tools_used:
        return "（本模式未调用工具）"
    counts = Counter(t["name"] for t in tools_used)
    return "、".join(f"{n}×{c}" for n, c in counts.most_common())
