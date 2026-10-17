"""P3 真实长文档稳定性协议：只汇总执行事实，不评价业务准确率。"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from eval_harness import run_full_flow
from materials import Run
from stability import classify_failure


def build_long_document(gold: dict, repeats: int) -> str:
    """重复冻结陈述文本以形成多块输入；评分字段不进入草稿。"""
    if repeats < 1 or repeats > 10:
        raise ValueError("重复组数必须在 1 到 10 之间")
    statements = gold.get("statements") or []
    if not statements:
        raise ValueError("长文协议缺少冻结陈述")
    lines = []
    for repeat in range(1, repeats + 1):
        for statement in statements:
            text = statement.get("text")
            ident = statement.get("id")
            if not isinstance(text, str) or not text.strip() or not isinstance(ident, str):
                raise ValueError("冻结陈述缺少 id 或 text")
            lines.append(f"[长文段{repeat:02d}-{ident}] {text}")
    return "\n".join(lines)


def _quote_key(value) -> str:
    return (value or "").strip().strip("。；;，,！？!?").strip()


def canonical_signature(bundle: dict) -> str:
    """只签裁决语义，不签运行 ID 或模型展示文本。"""
    rows = []
    for row in bundle.get("checks") or []:
        rows.append({
            "sentence_id": row.get("sentence_id"),
            "status": row.get("status"),
            "reason_code": row.get("reason_code"),
            "failure_code": row.get("failure_code"),
            "evidence_ids": sorted(row.get("evidence_ids") or []),
            "quote": _quote_key(row.get("quote")),
        })
    rows.sort(key=lambda row: (
        row["sentence_id"] if isinstance(row["sentence_id"], int) else -1,
        row["quote"], row["status"] or "", row["reason_code"] or "",
        row["failure_code"] or "",
    ))
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _summarize_run(bundle: dict, index: int, elapsed_seconds: float) -> dict:
    execution = bundle.get("execution") or {}
    attempts = execution.get("attempts") or []
    failed_chunks = bundle.get("failed_chunks") or []
    chunks = bundle.get("chunks") or []
    return {
        "run": index,
        "status": bundle.get("status"),
        "elapsed_seconds": round(elapsed_seconds, 4),
        "active_seconds": execution.get("active_seconds"),
        "sentences": len(bundle.get("sentences") or []),
        "chunks": len(chunks),
        "completed_chunks": sum(row.get("status") == "completed" for row in chunks),
        "failed_chunks": len(failed_chunks),
        "failure_codes": bundle.get("failure_codes") or {},
        "unfinished": len(bundle.get("unfinished_sentence_ids") or []),
        "requests": len(attempts),
        "known_tokens": execution.get("known_tokens", 0),
        "tokens_charged": execution.get("tokens_charged", 0),
        "unknown_attempts": execution.get("unknown_attempts", 0),
        "tool_calls": execution.get("tool_calls", 0),
        "model": bundle.get("model"),
        "provider_host": bundle.get("provider_host"),
        "response_format": bundle.get("response_format"),
        "observed_models": bundle.get("observed_models") or [],
        "signature": canonical_signature(bundle),
    }


def _failed_run_summary(index: int, elapsed_seconds: float, exc: Exception) -> dict:
    failure = classify_failure(exc)
    return {
        "run": index,
        "status": "failed",
        "elapsed_seconds": round(elapsed_seconds, 4),
        "active_seconds": None,
        "sentences": 0,
        "chunks": 0,
        "completed_chunks": 0,
        "failed_chunks": 1,
        "failure_codes": {failure.code: 1},
        "unfinished": 0,
        "requests": 0,
        "known_tokens": 0,
        "tokens_charged": 0,
        "unknown_attempts": 0,
        "tool_calls": 0,
        "model": None,
        "provider_host": None,
        "response_format": None,
        "observed_models": [],
        "signature": None,
        "reason": failure.user_message,
    }


def _consistency(runs: list[dict]) -> dict:
    signatures = [row["signature"] for row in runs if row.get("signature")]
    if len(signatures) < 2:
        return {"rate": None, "consistent": None, "unique_signatures": len(set(signatures)),
                "note": "少于两轮完整签名，不计算一致性"}
    counts = Counter(signatures)
    common = max(counts.values())
    return {"rate": round(common / len(signatures), 4),
            "consistent": len(counts) == 1,
            "unique_signatures": len(counts),
            "majority_runs": common}


def assess_live_stability(result: dict) -> dict:
    runs = result.get("runs") or []
    requested = {row.get("model") for row in runs if isinstance(row.get("model"), str) and row.get("model")}
    observed = {
        model for row in runs for model in (row.get("observed_models") or [])
        if isinstance(model, str) and model
    }
    checks = {
        "at_least_two_runs": len(runs) >= 2,
        "multi_chunk": bool(runs) and all((row.get("chunks") or 0) > 1 for row in runs),
        "all_runs_completed": bool(runs) and all(row.get("status") == "completed" for row in runs),
        "no_unfinished_sentences": bool(runs) and all(row.get("unfinished") == 0 for row in runs),
        "no_failed_chunks": bool(runs) and all(row.get("failed_chunks") == 0 for row in runs),
        "real_provider_requests": bool(runs) and all((row.get("requests") or 0) > 0 for row in runs),
        "known_token_usage": bool(runs) and all((row.get("known_tokens") or 0) > 0 for row in runs),
        "observed_model_identity": bool(observed),
        "stable_model_identity": len(observed) == 1,
        "consistent": ((result.get("consistency") or {}).get("consistent") is True
                        or (result.get("consistency") or {}).get("rate") == 1.0),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {"ok": not failed, "status": "PASS" if not failed else "FAIL",
            "checks": checks, "failed_criteria": failed,
            "requested_models": sorted(requested), "observed_models": sorted(observed),
            "model_alias_mismatch": bool(requested and observed and requested != observed)}


def run_live_rounds(
    gold: dict,
    facts: list[dict],
    output_dir: Path,
    *,
    client_factory: Callable[[], object],
    runs: int,
    repeats: int,
    config: dict,
    use_loop: bool = False,
) -> dict:
    """执行真实长文多轮；生产脚本由主入口校验密钥和 provider。"""
    if runs < 1 or runs > 10:
        raise ValueError("运行轮数必须在 1 到 10 之间")
    output_dir = Path(output_dir).resolve()
    draft = build_long_document(gold, repeats)
    rows = []
    for index in range(1, runs + 1):
        client = client_factory()
        run = Run(output_dir / f"live-run-{index:02d}", "p3_live_stability",
                  {"run": index, "runs": runs, "repeats": repeats,
                   "use_loop": use_loop, "chunk_sentences": config["document"]["chunk_sentences"]})
        started = time.perf_counter()
        try:
            bundle = run_full_flow(draft, facts, run, client, use_loop=use_loop, config=config)
        except Exception as exc:  # noqa: BLE001 - 协议边界记录失败后停止继续请求
            rows.append(_failed_run_summary(index, time.perf_counter() - started, exc))
            break
        rows.append(_summarize_run(bundle, index, time.perf_counter() - started))
    consistency = _consistency(rows)
    result = {
        "schema_version": 1,
        "kind": "p3-live-long-document",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_requested": runs,
        "runs_completed": len(rows),
        "repeats": repeats,
        "characters": len(draft),
        "sentences": len((gold.get("statements") or [])) * repeats,
        "chunk_sentences": config["document"]["chunk_sentences"],
        "chunk_characters": config["document"]["chunk_characters"],
        "use_loop": use_loop,
        "runs": rows,
        "consistency": consistency,
    }
    result["assessment"] = assess_live_stability(result)
    return result


def render_summary(result: dict) -> str:
    assessment = result.get("assessment") or {}
    requested = ", ".join(assessment.get("requested_models") or []) or "—"
    observed = ", ".join(assessment.get("observed_models") or []) or "—"
    hosts = sorted({row.get("provider_host") for row in result.get("runs") or []
                   if row.get("provider_host")})
    alias = "是" if assessment.get("model_alias_mismatch") else "否"
    lines = [
        "# P3 真实长文档稳定性",
        "",
        f"- 结论：{assessment.get('status', 'FAIL')}",
        f"- 服务商：{', '.join(hosts) or '—'}；请求模型：{requested}；实际返回：{observed}；名称别名差异：{alias}。",
        f"- 请求轮数：{result.get('runs_requested')}；实际完成：{result.get('runs_completed')}。",
        f"- 输入：{result.get('sentences')} 句，{result.get('characters')} 字，"
        f"{result.get('repeats')} 个冻结陈述重复组。",
        f"- 分块：每块最多 {result.get('chunk_sentences')} 句或 "
        f"{result.get('chunk_characters')} 字；多步循环：{'是' if result.get('use_loop') else '否'}。",
        f"- 跨轮一致性：{(result.get('consistency') or {}).get('rate')}。",
        "",
        "## 逐轮执行",
        "",
        "| 轮次 | 状态 | 分块 | 已完成块 | 请求 | 已知Token | 计费Token | 耗时秒 | 失败码 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in result.get("runs") or []:
        codes = ",".join(sorted((row.get("failure_codes") or {}).keys())) or "—"
        lines.append(
            f"| {row.get('run')} | {row.get('status')} | {row.get('chunks')} | "
            f"{row.get('completed_chunks')} | {row.get('requests')} | "
            f"{row.get('known_tokens')} | {row.get('tokens_charged')} | "
            f"{row.get('elapsed_seconds')} | {codes} |"
        )
    lines += ["", "## 通过判据", ""]
    for name, passed in (assessment.get("checks") or {}).items():
        lines.append(f"- {name}: {'PASS' if passed else 'FAIL'}")
    lines += [
        "",
        "## 边界",
        "",
        "- 这是真实服务商调用证据，但输入由冻结陈述组成，只用于执行稳定性，不代表业务准确率或盲测。",
        "- 结果不包含密钥；模型原文与事件保存在 `live-run-*`，本摘要只保留聚合执行事实。",
        "- 模型身份、Token、耗时和失败码均来自实际响应与本地执行预算记录。",
    ]
    return "\n".join(lines)
