"""草稿核查端到端编排、分块恢复、循环回退和输出文件。"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from audit.claim_check import _atom_key, _tool_verdict_rows, check_payload
from audit.client import LLMClient
from audit.loop import run_loop, run_loop_native, tool_summary
from audit.report import render_report
from audit.schema import COMPANIES, SECRET_PATTERN, LLMError, companies_from_facts
from audit.supplements import check_draft_supplements
from backend_runtime import (
    ExecutionBudget,
    ExecutionLimit,
    atomic_json,
    chunk_context,
    digest,
    load_config,
    plan_chunks,
    sentence_rows,
    validate_config,
)
from materials import AGENT_DIR, sha256, write_json


def _check_chunk(draft, sentences, facts, run, client, use_loop, tools_used, tool_results,
                 completed_atom_keys=None):
    """单块仍使用 fin 原协议、门控与本地裁决。"""
    mode = "fallback_single_shot"
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
            if exc.terminal:
                raise
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
                    if exc2.terminal:
                        raise
                    preserved = sum(1 for t in tool_results
                                    if t.get("name") in {"compare_claim", "compare_companies"})
                    run.event("loop_fallback", reason=str(exc2), first_error=str(exc),
                              preserved_tool_calls=len(tools_used),
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
    results = check_payload(payload, sentences, draft, facts, completed_atom_keys=completed_atom_keys)
    seen = {(tuple(row.get("evidence_ids") or []), row.get("status")) for row in results}
    for row in _tool_verdict_rows(tool_results, facts):
        key = (tuple(row.get("evidence_ids") or []), row.get("status"))
        if key not in seen:
            results.append(row)
            seen.add(key)
    run.event("model_parse", sentences=sentences, parsed=payload)
    return results, payload, mode


def _unfinished_rows(chunk, reason):
    """未完成的句子保留原句和失败原因；已完成的块不受影响，也不会被标成成功。"""
    return [{"claim_id": f"S{sentence['sentence_id']}", "sentence_id": sentence["sentence_id"],
             "original_sentence": sentence["text"], "status": "口径冲突／需人工复核",
             "track": "review", "reason_code": "execution_incomplete", "reason": reason,
             "evidence_ids": [], "evidence": [], "calculation": None,
             "expected": None, "suggestion": None} for sentence in chunk["sentences"]]



def _execution_failed(row):
    return bool(row.get("execution_failed")) or row.get("reason_code") in {
        "tool_execution_failed", "execution_incomplete"}


def _merge_preserved(cached, fresh):
    """保留已经完成的原子；同句含失败项时只用新结果替换待重试项。"""
    preserved = [row for row in (cached or {}).get("checks", []) if not _execution_failed(row)]
    def key(row):
        return row.get("atom_key") or (
            row.get("sentence_id"), row.get("quote"), row.get("plain_claim"),
            (row.get("normalized_claim") or {}).get("metric"),
            (row.get("interpretation") or {}).get("claim_type"))
    retained = {key(row) for row in preserved}
    identifiers = {row["claim_id"] for row in preserved}
    added = []
    for row in fresh:
        if key(row) in retained:
            continue
        row = dict(row)
        ident = row["claim_id"]
        index = 1
        while row["claim_id"] in identifiers:
            row["claim_id"] = f"{ident}_retry_{index}"
            index += 1
        identifiers.add(row["claim_id"])
        added.append(row)
    return sorted([*preserved, *added], key=lambda row: row.get("sentence_id") or 0)


def _chunk_completion(record, sentence_ids):
    failed = [row for row in record["checks"] if _execution_failed(row)]
    failed_ids = {row.get("sentence_id") for row in failed}
    if record.get("status") == "failed" and not failed:
        failed_ids.update(sentence_ids)
    record["completed_sentence_ids"] = [sid for sid in sentence_ids if sid not in failed_ids]
    if failed:
        record["status"] = "partial" if any(not _execution_failed(row) for row in record["checks"]) else "failed"
        record["reason"] = "；".join(dict.fromkeys(row.get("reason") or "执行失败" for row in failed))
        record["error_type"] = next((row.get("error_type") for row in failed if row.get("error_type")),
                                    record.get("error_type") or "ExecutionFailure")

def _checkpoint_binding(draft, facts, run, client, config, use_loop):
    import importlib.metadata
    import platform

    sources = {}
    for fact in facts:
        relative = fact.get("source_file")
        if not isinstance(relative, str):
            continue
        path = (run.root / relative).resolve()
        if not path.is_relative_to(run.root.resolve()) or path.suffix.lower() != ".pdf":
            sources[relative] = "unavailable_or_outside_root"
        elif path.is_file():
            sources[relative] = sha256(path.read_bytes())
        else:
            sources[relative] = "missing"
    code = {
        str(path.relative_to(AGENT_DIR)).replace("\\", "/"): sha256(path.read_bytes())
        for path in sorted(AGENT_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts and not path.name.startswith("test")
    }
    requirements = AGENT_DIR.parent / "requirements.txt"
    return {"schema_version": 1, "draft_sha256": sha256(draft.encode("utf-8")),
            "evidence_sha256": digest(facts), "source_hashes": sources, "code_hashes": code,
            "requirements_sha256": sha256(requirements.read_bytes()),
            "versions": {"python": platform.python_version(),
                         "pymupdf": importlib.metadata.version("pymupdf")},
            "config": config, "requested_model": client.model, "provider_host": client.host,
            "response_format": client.mode, "stream": getattr(client, "stream", False),
            "use_loop": use_loop}


def check_text(path: Path, facts: list[dict], run, client: LLMClient, *,
               use_loop: bool = True, resume: bool = False,
               config: dict | None = None) -> dict:
    draft = run.read(path).decode("utf-8-sig")
    if client.key in draft or SECRET_PATTERN.search(draft):
        raise LLMError("草稿疑似包含凭证，已停止读取后续流程；请使用单独的纯草稿文件")
    settings = json.loads(json.dumps(config or getattr(client, "config", None) or load_config()))
    validate_config(settings)
    try:
        ledger = sentence_rows(draft, settings)
    except ValueError as exc:
        raise LLMError(str(exc), terminal=True) from None
    sentences = [{"sentence_id": row["sentence_id"], "text": row["text"]} for row in ledger]
    chunks = plan_chunks(draft, ledger, settings)
    binding = _checkpoint_binding(draft, facts, run, client, settings, use_loop)
    identity = digest(binding)
    checkpoint = run.output(f"text_checkpoint_{identity[:24]}.json")
    state = {"binding": binding, "binding_sha256": identity, "chunks": {}, "budget": {},
             "observed_models": []}
    if resume:
        if not checkpoint.is_file():
            raise LLMError("没有与输入、源码、证据、模型及配置匹配的检查点；拒绝自动新开请求",
                           terminal=True)
        if checkpoint.stat().st_size > 16 * 1024 * 1024:
            raise LLMError("检查点超过16MiB，拒绝恢复", terminal=True)
        saved = checkpoint.read_text(encoding="utf-8")
        if client.key in saved or SECRET_PATTERN.search(saved):
            raise LLMError("检查点疑似包含凭证，拒绝恢复", terminal=True)
        candidate = json.loads(saved)
        checksum = candidate.pop("payload_sha256", None)
        if checksum != digest(candidate):
            raise LLMError("检查点内容指纹不一致，拒绝恢复", terminal=True)
        if candidate.get("binding") != binding or candidate.get("binding_sha256") != identity:
            raise LLMError("检查点绑定不一致，不能续跑", terminal=True)
        state = candidate
        valid_chunks = {str(chunk["chunk_id"]): chunk for chunk in chunks}
        for number, cached in state.get("chunks", {}).items():
            expected = valid_chunks.get(number)
            if (expected is None
                    or cached.get("sentence_ids") != [s["sentence_id"] for s in expected["sentences"]]
                    or cached.get("start") != expected["start"]
                    or cached.get("end") != expected["end"]):
                raise LLMError("检查点分块位置不一致，拒绝恢复", terminal=True)
        run.event("llm_checkpoint_resumed", checkpoint=checkpoint.name, binding_sha256=identity)

    execution = ExecutionBudget(settings, run, state.get("budget"))
    original = {name: (hasattr(client, name), getattr(client, name, None))
                for name in ("_execution", "_checkpoint_commit", "_document_context", "config",
                             "_observed_models")}
    transport = getattr(client, "_http", None)
    previous_http = ({name: getattr(transport, name) for name in
                      ("timeout", "max_retries", "request_deadline")}
                     if transport is not None else {})
    if transport is not None:
        transport.timeout = settings["http"]["timeout_seconds"]
        transport.max_retries = settings["http"]["max_retries"]
        transport.request_deadline = settings["http"]["request_deadline_seconds"]

    def save():
        state["budget"] = execution.snapshot()
        state["observed_models"] = sorted(getattr(client, "_observed_models", set()))
        encoded = json.dumps(state, ensure_ascii=False)
        if client.key in encoded or SECRET_PATTERN.search(encoded):
            raise LLMError("检查点疑似包含凭证，拒绝保存", terminal=True)
        atomic_json(checkpoint, {**state, "payload_sha256": digest(state)})

    client._execution, client.config = execution, settings
    client._observed_models = set(state.get("observed_models", []))
    client._checkpoint_commit = save
    all_results, all_tools, all_tool_results, modes = [], [], [], []
    merged = {"items": [], "unclaimed_sentences": []}
    summary, completed_sentences, stop_reason = [], [], None
    run.event("draft_loaded", characters=len(draft), sha256=sha256(draft.encode("utf-8")),
              sentences=len(sentences), chunks=len(chunks), config=settings)
    try:
        save()
        for chunk in chunks:
            number = str(chunk["chunk_id"])
            cached = state["chunks"].get(number)
            if resume and cached and cached.get("status") == "completed":
                record = cached
                run.event("llm_chunk_reused", chunk_id=chunk["chunk_id"],
                          sentence_ids=[s["sentence_id"] for s in chunk["sentences"]])
            else:
                if cached:
                    state.setdefault("history", []).append(cached)
                used, tool_results = [], []
                retry_ids = set(cached.get("sentence_ids", [])) - set(cached.get("completed_sentence_ids", [])) if cached else set()
                retry_sentences = ([sentence for sentence in chunk["sentences"]
                                    if sentence["sentence_id"] in retry_ids] if cached else chunk["sentences"])
                # 兼容旧失败检查点；未标记已完成句时整块重试。
                retry_sentences = retry_sentences or chunk["sentences"]
                began = time.monotonic()
                context = chunk_context(draft, chunk, companies_from_facts(facts) or COMPANIES)
                client._document_context = context
                context_text = "\n".join(
                    str(context[key]) for key in
                    ("section_heading", "company_text", "period_text") if context.get(key)
                )
                local_draft = context_text + "\n" + chunk["text"] if context_text else chunk["text"]
                try:
                    execution.remaining_seconds()
                    if stop_reason:
                        raise LLMError(stop_reason, terminal=True)
                    if chunk["oversized_sentence"]:
                        raise LLMError("单句超过分块上限，保留原句并转人工处理，没有截断")
                    run.event("llm_chunk_started", chunk_id=chunk["chunk_id"], context=context,
                              start=chunk["start"], end=chunk["end"])
                    results, payload, mode = _check_chunk(
                        local_draft, retry_sentences, facts, run, client, use_loop,
                        used, tool_results,
                        completed_atom_keys={row["atom_key"] for row in (cached or {}).get("checks", [])
                                             if not _execution_failed(row) and row.get("atom_key")},
                    )
                    if not cached:
                        results.extend(check_draft_supplements(chunk["text"], facts, root=run.root, run=run))
                    if len(chunks) > 1:
                        for result in results:
                            result["claim_id"] = f"B{chunk['chunk_id']}_{result['claim_id']}"
                    if cached:
                        results = _merge_preserved(cached, results)
                        successful_atoms = {row["atom_key"] for row in cached["checks"]
                                            if not _execution_failed(row) and row.get("atom_key")}
                        preserved_items = [item for item in cached["parsed"]["items"]
                                           if item.get("sentence_id") not in retry_ids
                                           or _atom_key(item) in successful_atoms]
                        preserved_keys = {_atom_key(item) for item in preserved_items}
                        payload = {"items": [*preserved_items, *(item for item in payload["items"]
                                                               if _atom_key(item) not in preserved_keys)],
                                   "unclaimed_sentences": [
                                       *(sid for sid in cached["parsed"]["unclaimed_sentences"] if sid not in retry_ids),
                                       *payload["unclaimed_sentences"]]}
                        used = [*cached.get("tools_used", []), *used]
                        tool_results = [*cached.get("tool_results", []), *tool_results]
                    record = {"status": "completed", "checks": results, "parsed": payload,
                              "mode": mode, "tools_used": used, "tool_results": tool_results}
                    _chunk_completion(record, [sentence["sentence_id"] for sentence in chunk["sentences"]])
                    run.event("llm_chunk_completed" if record["status"] == "completed" else "llm_chunk_failed",
                              chunk_id=chunk["chunk_id"], status=record["status"],
                              reason=record.get("reason"), error_type=record.get("error_type"), checks=len(results))
                except Exception as exc:  # noqa: BLE001 - 业务边界必须把单项失败留在本块
                    failure = str(exc) or type(exc).__name__
                    if getattr(exc, "request_status", None) in {400, 401, 403, 404, 422}:
                        stop_reason = "接口参数或认证失败；后续分块未发请求，请修正配置后恢复"
                    results = _unfinished_rows({"sentences": retry_sentences},
                                               f"{type(exc).__name__}: {failure}")
                    for row in results:
                        row.update(execution_failed=True, error_type=type(exc).__name__)
                    results.extend(_tool_verdict_rows(tool_results, facts))
                    if len(chunks) > 1:
                        for result in results:
                            result["claim_id"] = f"B{chunk['chunk_id']}_{result['claim_id']}"
                    if cached:
                        results = _merge_preserved(cached, results)
                    record = {"status": "failed", "checks": results,
                              "parsed": (cached["parsed"] if cached else {"items": [], "unclaimed_sentences": []}),
                              "mode": "incomplete", "reason": failure,
                              "error_type": type(exc).__name__,
                              "tools_used": [*cached.get("tools_used", []), *used] if cached else used,
                              "tool_results": [*cached.get("tool_results", []), *tool_results] if cached else tool_results}
                    _chunk_completion(record, [sentence["sentence_id"] for sentence in chunk["sentences"]])
                    run.event("llm_chunk_failed", chunk_id=chunk["chunk_id"],
                              reason=failure, error_type=type(exc).__name__,
                              preserved_verdicts=len(_tool_verdict_rows(tool_results, facts)))
                record.update(chunk_id=chunk["chunk_id"],
                              sentence_ids=[s["sentence_id"] for s in chunk["sentences"]],
                              start=chunk["start"], end=chunk["end"],
                              elapsed_seconds=round(time.monotonic() - began, 4))
                state["chunks"][number] = record
                save()
            all_results.extend(record["checks"])
            all_tools.extend(record["tools_used"])
            all_tool_results.extend(record["tool_results"])
            modes.append(record["mode"])
            merged["items"].extend(record["parsed"]["items"])
            merged["unclaimed_sentences"].extend(record["parsed"]["unclaimed_sentences"])
            completed_sentences.extend(record.get("completed_sentence_ids",
                                                 record["sentence_ids"] if record["status"] == "completed" else []))
            summary.append({key: record[key] for key in
                            ("chunk_id", "status", "sentence_ids", "start", "end",
                             "elapsed_seconds")}
                           | ({"reason": record["reason"], "error_type": record.get("error_type")}
                              if record["status"] != "completed" else {}))
        unfinished = sorted(set(s["sentence_id"] for s in sentences) - set(completed_sentences))
        failed_chunks = [entry for entry in summary if entry["status"] != "completed"]
        mode = modes[0] if len(chunks) == 1 else "chunked"
        bundle = {"schema_version": 2, "run_id": run.id,
                  "status": "partial" if unfinished else "completed", "mode": mode,
                  "model": client.model, "provider_host": client.host,
                  "response_format": client.mode, "sentences": sentences,
                  "sentence_ledger": ledger, "parsed": merged, "checks": all_results,
                  "tools_used": all_tools, "tool_results": all_tool_results,
                  "counts": dict(Counter(row["status"] for row in all_results)),
                  "tracks": dict(Counter(row.get("track") or "review" for row in all_results)),
                  "chunks": summary, "failed_chunks": failed_chunks,
                  "unfinished_sentence_ids": unfinished,
                  "checkpoint_file": checkpoint.name, "binding_sha256": identity,
                  "execution": execution.snapshot(),
                  "observed_models": sorted(client._observed_models),
                  "config": settings}
        if unfinished:
            bundle["reason"] = (f"{len(unfinished)}句未完成模型核查（失败分块 {len(failed_chunks)} 个），"
                                f"已保留已完成分块的结果和恢复检查点")
        run.output("checked_draft.txt").write_text(draft, encoding="utf-8")
        write_json(run.output("text_checks.json"), bundle)
        rendered = render_report(all_results, model=client.model, run_id=run.id,
                                 tools_used=all_tools, mode=mode)
        if unfinished:
            rendered = f"> 未完成：{len(unfinished)}句。检查点：{checkpoint.name}\n\n" + rendered
        run.output("text_report.md").write_text(rendered, encoding="utf-8")
        for result in all_results:
            run.event("claim_gate", claim_id=result["claim_id"],
                      reason_code=result["reason_code"], reason=result.get("reason"),
                      evidence_ids=result["evidence_ids"],
                      normalized_claim=result.get("normalized_claim"))
            run.event("text_claim_checked", claim_id=result["claim_id"],
                      status=result["status"], reason_code=result["reason_code"],
                      track=result.get("track"), evidence_ids=result["evidence_ids"])
        save()
        run.event("document_execution_finished", status=bundle["status"],
                  unfinished_sentence_ids=unfinished, execution=bundle["execution"])
        return bundle
    finally:
        if transport is not None:
            for name, value in previous_http.items():
                setattr(transport, name, value)
        for name, (existed, value) in original.items():
            if existed:
                setattr(client, name, value)
            elif hasattr(client, name):
                # 早期异常可能还没设过该属性；清理不能让原错误被 AttributeError 覆盖。
                delattr(client, name)
