"""草稿核查端到端编排、循环回退和输出文件。"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from audit.claim_check import _tool_verdict_rows, check_payload
from audit.client import LLMClient
from audit.grounding import split_draft
from audit.loop import run_loop, run_loop_native, tool_summary
from audit.report import render_report
from audit.schema import SECRET_PATTERN, LLMError
from audit.supplements import check_draft_supplements
from materials import sha256, write_json


def check_text(path: Path, facts: list[dict], run, client: LLMClient, *,
               use_loop: bool = True) -> dict:
    draft = run.read(path).decode("utf-8-sig")
    if client.key in draft or SECRET_PATTERN.search(draft):
        raise LLMError("草稿疑似包含凭证，已停止读取后续流程；请使用单独的纯草稿文件")
    sentences = split_draft(draft)
    run.event("draft_loaded", characters=len(draft), sha256=sha256(draft.encode("utf-8")), sentences=len(sentences))
    mode = "fallback_single_shot"
    tools_used: list[dict] = []
    tool_results: list[dict] = []
    if use_loop and hasattr(client, "chat"):
        try:
            loop_fn = run_loop_native if hasattr(client, "chat_tools") else run_loop
            loop_out = loop_fn(client, draft, facts, run, sentences=sentences,
                               tools_used=tools_used, tool_results=tool_results)
            payload = loop_out["payload"]
            tools_used = loop_out["tools_used"]
            tool_results = loop_out.get("tool_results") or tool_results
            mode = loop_out["mode"]
            run.event("tool_summary", tools=tool_summary(tools_used),
                      rounds=loop_out.get("rounds"), budget_left=loop_out.get("budget_left"))
        except LLMError as exc:
            if hasattr(client, "chat_tools") and not tools_used:
                try:
                    loop_out = run_loop(client, draft, facts, run, sentences=sentences,
                                        tools_used=tools_used, tool_results=tool_results)
                    payload = loop_out["payload"]
                    tools_used = loop_out["tools_used"]
                    tool_results = loop_out.get("tool_results") or tool_results
                    mode = loop_out["mode"]
                    run.event("tool_summary", tools=tool_summary(tools_used),
                              rounds=loop_out.get("rounds"),
                              budget_left=loop_out.get("budget_left"))
                except LLMError as exc2:
                    preserved = sum(1 for t in tool_results
                                    if t.get("name") in {"compare_claim", "compare_companies"})
                    run.event("loop_fallback", reason=str(exc2),
                              first_error=str(exc), preserved_tool_calls=len(tools_used),
                              preserved_verdicts=preserved)
                    payload = client.extract(sentences, run, facts)
                    mode = ("fallback_single_shot_tools_preserved" if tools_used
                            else "fallback_single_shot")
            else:
                preserved = sum(1 for t in tool_results
                                if t.get("name") in {"compare_claim", "compare_companies"})
                run.event("loop_fallback", reason=str(exc),
                          preserved_tool_calls=len(tools_used),
                          preserved_verdicts=preserved)
                payload = client.extract(sentences, run, facts)
                mode = ("fallback_single_shot_tools_preserved" if tools_used
                        else "fallback_single_shot")
    else:
        payload = client.extract(sentences, run, facts)
    results = check_payload(payload, sentences, draft, facts)
    seen = {(tuple(r.get("evidence_ids") or []), r.get("status")) for r in results}
    for row in _tool_verdict_rows(tool_results, facts):
        key = (tuple(row.get("evidence_ids") or []), row.get("status"))
        if key not in seen:
            results.append(row)
            seen.add(key)
    run.event("model_parse", sentences=sentences, parsed=payload)
    results.extend(check_draft_supplements(draft, facts, root=run.root, run=run))
    bundle = {"schema_version": 2, "run_id": run.id, "status": "completed",
              "mode": mode, "model": client.model, "provider_host": client.host,
              "response_format": client.mode,
              "sentences": sentences, "parsed": payload, "checks": results,
              "tools_used": tools_used,
              "tool_results": tool_results,
              "counts": dict(Counter(row["status"] for row in results)),
              "tracks": dict(Counter(row.get("track") or "review" for row in results))}
    run.output("checked_draft.txt").write_text(draft, encoding="utf-8")
    write_json(run.output("text_checks.json"), bundle)
    run.output("text_report.md").write_text(
        render_report(results, model=client.model, run_id=run.id,
                      tools_used=tools_used, mode=mode), encoding="utf-8")
    for result in results:
        run.event("claim_gate", claim_id=result["claim_id"], reason_code=result["reason_code"],
                  reason=result.get("reason"), evidence_ids=result["evidence_ids"],
                  normalized_claim=result.get("normalized_claim"))
        run.event("text_claim_checked", claim_id=result["claim_id"], status=result["status"],
                  reason_code=result["reason_code"], track=result.get("track"),
                  evidence_ids=result["evidence_ids"])
    return bundle
