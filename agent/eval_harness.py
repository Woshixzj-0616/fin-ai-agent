"""评测运行时：统一走 check_text，按原子陈述评分。

约定
----
- 草稿、材料、提示词和配置冻结；评测只调用当前完整核查流程（分块、门控、预算、恢复）。
- 参考答案（expect）只进 score_atomic，绝不进入草稿、提示词或模型客户端。
- 逐条原子陈述记录：命中、判定、弃答、漏拆、超时、失败和模型轨，不把未判定当通过。
"""
from __future__ import annotations

import json
from pathlib import Path

from audit.schema import LLMError
from backend_runtime import load_config

JUDGED_STATUSES = {"证据支持", "确认错误"}
ABSTAIN_STATUSES = {"证据不足", "口径冲突／需人工复核"}
MODEL_STATUS = "模型判断"
JUDGE_VERDICTS = {"kind": "judge"}
TIMEOUT_HINTS = ("时限", "预算", "ExecutionLimit", "deadline")


class ReplayClient:
    """固定协议样例客户端：按分块顺序回放预置 JSON，不联网、不读参考答案。

    与 LLMClient 的 extract(sentences, run, facts) 同签名；error_at 用于注入分块失败。
    """

    # key 只用于“凭证不得出现在草稿/检查点”的本地防线，取不可能出现在文本里的值。
    model, host, key, mode = "protocol-fixture", "offline", "offline-no-credential", "json_object"
    stream = False

    def __init__(self, payloads: list[dict] | None = None, *, items: list[dict] | None = None,
                 error_at: int | None = None, error: Exception | None = None):
        self.payloads = list(payloads or [])
        self.items = list(items or []) if items is not None else None
        self.error_at = error_at
        self.error = error or LLMError("回放客户端注入的分块失败")
        self.calls: list[dict] = []
        self.config = load_config()

    def _next(self, sentences: list[dict]) -> dict:
        identifiers = [sentence["sentence_id"] for sentence in sentences]
        self.calls.append({"sentence_ids": identifiers})
        if self.error_at is not None and len(self.calls) == self.error_at:
            raise self.error
        if self.items is not None:
            return payload_for(self.items, sentences)
        if self.payloads:
            payload = self.payloads.pop(0)
            return json.loads(json.dumps(payload, ensure_ascii=False))
        return {"items": [], "unclaimed_sentences": identifiers}

    def extract(self, sentences: list[dict], run, facts: list[dict] | None = None) -> dict:
        return self._next(sentences)

    def chat(self, messages: list[dict], run) -> str:
        raise LLMError("回放客户端不支持 JSON 多步协议；请用 use_loop=False")

    def chat_tools(self, messages: list[dict], tools: list[dict], run) -> dict:
        raise LLMError("回放客户端不支持原生工具协议；请用 use_loop=False")


def payload_for(items: list[dict], sentences: list[dict]) -> dict:
    """把预置协议项按句子归属装成一次 submit；未覆盖的句子如实列为未归属。"""
    identifiers = [sentence["sentence_id"] for sentence in sentences]
    chosen = [json.loads(json.dumps(item, ensure_ascii=False))
              for item in items if item.get("sentence_id") in identifiers]
    used = {item["sentence_id"] for item in chosen}
    return {"items": chosen, "unclaimed_sentences": [i for i in identifiers if i not in used]}


def run_full_flow(draft: str, facts: list[dict], run, client, *, use_loop: bool = False,
                  config: dict | None = None, name: str = "eval_draft.txt") -> dict:
    """把草稿写进运行目录后走当前 check_text 编排；返回结果包（含检查点与失败分块）。"""
    from audit.service import check_text

    path = run.output(name)
    path.write_text(draft, encoding="utf-8")
    return check_text(path, facts, run, client, use_loop=use_loop,
                      config=json.loads(json.dumps(config or load_config())))


def _quote_key(value) -> str:
    # 只忽略引文边缘标点/空白，保留原子内的数值、实体和关系。
    return (value or "").strip().strip("。；;，,！？!?").strip()


def align_rows(checks: list[dict], statements: list[dict]) -> tuple[list[dict | None], list[dict]]:
    """一对一引文对齐；同跨度重复、多原子整句和无法唯一定位均保持未匹配。

    不使用 original_sentence 包含关系或生成的 claim_id 冒充原子身份。
    旧无引文结果仅在同句金标和结果都唯一时兼容。
    """
    candidates = {}
    by_sid = {}
    for index, statement in enumerate(statements):
        by_sid.setdefault(statement.get("sentence_id"), []).append(index)
    for ri, row in enumerate(checks):
        if row.get("reason_code") == "no_claim_extracted":
            continue
        quote = _quote_key(row.get("quote"))
        scope = [gi for gi, statement in enumerate(statements)
                 if statement.get("sentence_id") is None
                 or row.get("sentence_id") == statement.get("sentence_id")]
        exact = [gi for gi in scope if quote and quote == _quote_key(statements[gi].get("quote"))]
        if exact:
            candidates[ri] = set(exact)
            continue
        contained = [gi for gi in scope
                     if quote and _quote_key(statements[gi].get("quote"))
                     and _quote_key(statements[gi].get("quote")) in quote]
        # 整句同时覆盖两个原子不能成为任一原子的证据。
        if len(contained) == 1:
            candidates[ri] = set(contained)
        elif not quote and (not row.get("original_sentence") or row.get("reason_code") == "execution_incomplete"):
            sid = row.get("sentence_id")
            peers = [r for r in checks if r.get("sentence_id") == sid
                     and r.get("reason_code") not in {"no_claim_extracted", "execution_incomplete"}]
            if sid is not None and len(by_sid.get(sid, [])) == len(peers) == 1:
                candidates[ri] = set(by_sid[sid])
    matched = [None] * len(statements)
    consumed = set()
    # 必须两侧均唯一；不会因先处理的顺序把重复预测消成唯一项。
    reverse = {}
    for ri, gids in candidates.items():
        for gi in gids:
            reverse.setdefault(gi, set()).add(ri)
    for ri, gids in candidates.items():
        if len(gids) == 1:
            gi = next(iter(gids))
            if len(reverse[gi]) == 1:
                matched[gi] = checks[ri]
                consumed.add(ri)
    extras = [row for ri, row in enumerate(checks) if ri not in consumed
              and row.get("reason_code") not in {"no_claim_extracted", "execution_incomplete"}]
    return matched, extras


def match_row(checks: list[dict], statement: dict) -> dict | None:
    """兼容单条查询；多条评分必须一次调用 align_rows 以防复用。"""
    return align_rows(checks, [statement])[0][0]


def classify(bundle: dict, statement: dict, row: dict | None) -> str:
    sid = statement.get("sentence_id")
    if row is None:
        if sid in (bundle.get("unfinished_sentence_ids") or []):
            reasons = [row.get("reason") or "" for row in bundle.get("checks", [])
                       if row.get("sentence_id") == sid and row.get("reason_code") == "execution_incomplete"]
            reason = " ".join([bundle.get("reason") or "", *reasons])
            return "timeout" if any(hint in reason for hint in TIMEOUT_HINTS) else "failed"
        return "omitted"
    reason_code = row.get("reason_code")
    if reason_code == "no_claim_extracted":
        return "omitted"
    if reason_code in {"execution_incomplete", "tool_execution_failed"} or row.get("execution_failed"):
        reason = row.get("reason") or ""
        return "timeout" if any(hint in reason for hint in TIMEOUT_HINTS) else "failed"
    status = row.get("status")
    if status in ABSTAIN_STATUSES:
        return "abstain"
    if status == MODEL_STATUS or row.get("track") == "model":
        return "model_track"
    if status in JUDGED_STATUSES:
        return "judged"
    return "other"


def verdict_ok(row: dict, expect: dict) -> bool | None:
    kind = (expect or {}).get("kind")
    if kind == "status":
        return row.get("status") == expect.get("status")
    if kind == "judge":
        wanted = "确认错误" if expect.get("error") else "证据支持"
        return row.get("status") == wanted
    if kind == "model_track":
        return row.get("track") == "model" or row.get("status") == MODEL_STATUS
    if kind == "reason_code":
        return row.get("reason_code") == expect.get("code")
    if kind == "nonverdict":
        return row.get("status") in {MODEL_STATUS, *ABSTAIN_STATUSES}
    return None


def score_atomic(bundle: dict, statements: list[dict]) -> dict:
    """逐条原子陈述评分；遗漏、超时、弃答和失败全部保留在结果里。"""
    checks = bundle.get("checks") or []
    rows = []
    aligned, extras = align_rows(checks, statements)
    for statement, row in zip(statements, aligned):
        outcome = classify(bundle, statement, row)
        expect = statement.get("expect") or {}
        ok = verdict_ok(row, expect) if outcome in {"judged", "model_track", "abstain"} else None
        rows.append({
            "id": statement.get("id"), "sample": statement.get("sample"),
            "sentence_id": statement.get("sentence_id"), "quote": statement.get("quote"),
            "expect": expect, "outcome": outcome,
            "status": (row or {}).get("status"), "track": (row or {}).get("track"),
            "reason_code": (row or {}).get("reason_code"), "reason": (row or {}).get("reason"),
            "evidence_ids": (row or {}).get("evidence_ids") or [],
            "grounding": (row or {}).get("grounding"),
            "ok": ok,
        })
    judged = [row for row in rows if row["outcome"] == "judged"]
    correct = [row for row in judged if row["ok"]]
    wrong = [row for row in judged if row["ok"] is False]
    expected_judge = [row for row in rows if row["expect"].get("kind") == "judge"
                      or (row["expect"].get("kind") == "status" and row["expect"].get("status") in JUDGED_STATUSES)]
    cited = [row for row in rows if row["evidence_ids"]]
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for row in judged:
        expect = row["expect"]
        expected = (("确认错误" if expect.get("error") else "证据支持")
                    if expect.get("kind") == "judge" else expect.get("status"))
        if expected not in JUDGED_STATUSES:
            continue
        actual_error, expected_error = row["status"] == "确认错误", expected == "确认错误"
        key = ("tp" if expected_error else "fp") if actual_error else ("fn" if expected_error else "tn")
        confusion[key] += 1
    tp, fp, tn, fn = (confusion[key] for key in ("tp", "fp", "tn", "fn"))
    execution = bundle.get("execution") or {}
    attempts = execution.get("attempts") or []
    summary = {
        "total": len(rows),
        "judged": len(judged),
        "correct": len(correct),
        "wrong": len(wrong),
        "abstain": sum(1 for row in rows if row["outcome"] == "abstain"),
        "abstain_expected": sum(1 for row in rows if row["outcome"] == "abstain" and row["ok"]),
        "abstain_avoidable": sum(1 for row in rows
                                 if row["outcome"] == "abstain" and row["ok"] is False),
        "omitted": sum(1 for row in rows if row["outcome"] == "omitted"),
        "timeout": sum(1 for row in rows if row["outcome"] == "timeout"),
        "failed": sum(1 for row in rows if row["outcome"] == "failed"),
        "model_track": sum(1 for row in rows if row["outcome"] == "model_track"),
        "expected_judge": len(expected_judge),
        "citation": len(cited),
        **confusion,
        "extra_predictions": len(extras),
        "accuracy": round(len(correct) / len(judged), 4) if judged else None,
        "error_rate": round(len(wrong) / len(judged), 4) if judged else None,
        "correct_coverage": round(len(correct) / len(expected_judge), 4) if expected_judge else None,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else None,
        "coverage": round((len(judged) + sum(1 for row in rows if row["outcome"] == "abstain"))
                          / len(rows), 4) if rows else None,
        "completion": bundle.get("status"),
        "unfinished_sentences": len(bundle.get("unfinished_sentence_ids") or []),
        "failed_chunks": len(bundle.get("failed_chunks") or []),
        "attempts": len(attempts),
        "tokens_charged": execution.get("tokens_charged"),
        "tool_calls": execution.get("tool_calls"),
        "active_seconds": execution.get("active_seconds"),
    }
    return {"summary": summary, "rows": rows, "extra_predictions": extras,
            "note": "逐条原子一对一评分；重复/多余预测单列；未判定不计正确。"
                    "accuracy 为已判定正确率；precision/recall/FPR 以确认错误为正类，"
                    "仅覆盖已判定二分类项，未判定影响 correct_coverage/coverage。"}


def load_answers(path: Path) -> dict:
    """读取参考答案文件；只有评分程序调用这里。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = ["ReplayClient", "align_rows", "classify", "load_answers", "match_row", "payload_for",
           "run_full_flow", "score_atomic", "verdict_ok"]
