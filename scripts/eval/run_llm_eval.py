"""E5c · 模型拆句准确率评测（统一走当前 check_text）。

评什么
------
1. **端到端**：草稿经当前完整核查流程（分块、门控、预算、恢复）后的原子陈述结果；
2. **原子陈述**：逐条记录判定、弃答、漏拆、超时、失败与模型轨，未判定不算通过；
3. **字段级**：模型解析出的公司/年度/指标/数值/单位/运算符与金标是否一致；
4. **对照**：单次 extract vs JSON 多步工具循环（同一批句）。

约定
----
- 参考答案只交给评分程序；草稿、提示词与客户端都看不到金标字段。
- --output-dir 指定报告与运行记录目录；默认生成独立临时目录并保留，不覆盖版本库历史。

用法（仓库根）：
  set LLM_BASE_URL=...  set LLM_MODEL=...  set LLM_API_KEY=...
  python scripts/eval/run_llm_eval.py

  # 若密钥在智能旅行 backend/.env（只读、不回显）：
  python scripts/eval/run_llm_eval.py --from-travel-env

无密钥时：--dry-run 用「金标解析 + 扰动解析」自检评测机（不算模型分）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from audit.grounding import split_draft  # noqa: E402
from eval_harness import (  # noqa: E402
    ReplayClient,
    align_rows,
    run_full_flow,
    score_atomic,
)
from llm_check import LLMClient, LLMError, schema, validate_schema  # noqa: E402
from materials import Run, write_json  # noqa: E402
from main import extract_selected  # noqa: E402

GOLD_PATH = ROOT / "data" / "eval" / "semantic_gold.json"
FIELDS = ("company_name", "period_year", "metric", "value", "unit", "operator", "claim_type", "kind")

TRAVEL_ENV = Path(r"D:\Desktop\智能旅行\backend\.env")


def load_travel_env() -> dict:
    """读智能旅行 .env 里的模型密钥，只进内存不打印。"""
    if not TRAVEL_ENV.is_file():
        return {}
    picked = {}
    for line in TRAVEL_ENV.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in {"MIMO_API_KEY", "DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "LLM_API_KEY"}:
            picked[k] = v
    return picked


def pick_provider(picked: dict) -> tuple[str, str, str]:
    """密钥 → (base_url, model, key)。优先用户显式 env，再 DeepSeek，再智谱免费档。"""
    key = os.environ.get("LLM_API_KEY") or os.environ.get("MIMO_API_KEY") or ""
    base = os.environ.get("LLM_BASE_URL") or ""
    model = os.environ.get("LLM_MODEL") or ""
    if key:
        return base or "https://api.deepseek.com/v1", model or "deepseek-chat", key
    if picked.get("DEEPSEEK_API_KEY"):
        return base or "https://api.deepseek.com/v1", model or "deepseek-chat", picked["DEEPSEEK_API_KEY"]
    if picked.get("ZHIPU_API_KEY"):
        return base or "https://open.bigmodel.cn/api/paas/v4", model or "glm-4-flash", picked["ZHIPU_API_KEY"]
    if picked.get("MIMO_API_KEY"):
        return base or "https://api.xiaomimimo.com/v1", model or "mimo", picked["MIMO_API_KEY"]
    return "", "", ""


def gold_items_for(sentence: dict, idx: int) -> list[dict]:
    """把金标句上的字段填成协议 item（用于 dry-run 自检的固定协议样例）。"""
    if "atoms" in sentence:
        items = []
        inherited = {key: value for key, value in sentence.items() if key not in {"atoms", "text", "id"}}
        for atom_index, atom in enumerate(sentence["atoms"], 1):
            built = gold_items_for({**inherited, **atom}, idx)
            for item in built:
                item["claim_id"] = f"G{idx}_{atom_index}"
            items.extend(built)
        return items
    if sentence.get("gold") in {"qualitative", "ambiguous_metric"} and not sentence.get("value"):
        return []
    company = sentence.get("company_name", "贵州茅台")
    metric = sentence.get("metric", "revenue")
    # 归母类指标的口径是 parent_shareholders；自检样例按协议正确声明，避免把口径冲突当模型错误。
    scope = sentence.get("scope") or ("parent_shareholders"
                                      if metric in {"parent_net_profit", "adjusted_parent_net_profit"}
                                      else "consolidated")
    item = {
        "claim_id": f"G{idx}",
        "sentence_id": idx,
        "quote": sentence["text"],
        "context_quote": None,
        "claim_type": sentence.get("claim_type", "amount" if sentence.get("value") else "qualitative"),
        "company_name": company,
        "period_year": sentence.get("period_year"),
        "period_kind": "annual",
        "metric_text": sentence.get("metric_text", "营业收入"),
        "metric": metric,
        "scope": scope,
        "kind": sentence.get("kind", "amount"),
        "value": sentence.get("value"),
        "unit": sentence.get("unit"),
        "operator": sentence.get("operator", "eq"),
        "tolerance_pct": sentence.get("tolerance_pct"),
        "direction": sentence.get("direction", "unknown"),
        "currency": "CNY",
        "qualifier": sentence.get("qualifier", "exact"),
        "plain_claim": sentence["text"][:40],
        "is_forecast": bool(sentence.get("is_forecast")),
        "ambiguity": sentence.get("ambiguity", "none"),
        "verification_action": sentence.get("verification_action", "compare_amount"),
    }
    return [item]


def expect_for_sentence(sentence: dict) -> dict:
    """金标句 → 评分期望；只有评分程序读这个函数。"""
    gold = sentence.get("gold")
    if gold == "should_judge":
        return {"kind": "judge", "error": bool(sentence.get("gold_error"))}
    if gold == "forecast_mark_only":
        return {"kind": "reason_code", "code": "forecast_marked_only"}
    return {"kind": "nonverdict"}


def statements_for(gold: dict) -> list[dict]:
    """按草稿句号对齐的原子陈述（含期望）；不进入草稿或提示词。"""
    statements = []
    for index, sentence in enumerate(gold["sentences"], 1):
        atoms = sentence.get("atoms", [sentence])
        inherited = {key: value for key, value in sentence.items() if key not in {"atoms", "text", "id"}}
        for atom in atoms:
            gold_atom = {**inherited, **atom}
            statements.append({"id": atom.get("id"), "sentence_id": index,
                               "quote": atom["text"], "expect": expect_for_sentence(gold_atom)})
    return statements


def score_fields(pred: dict, gold: dict) -> dict:
    """数值句评 8 字段；预测/定性只评 claim_type/公司/年度，避免 0% 误伤。"""
    numeric = bool(gold.get("value")) and gold.get("claim_type") not in {"forecast", "qualitative"}
    fields = FIELDS if numeric else ("company_name", "period_year", "claim_type")
    hits = {"_fields": list(fields)}
    for f in fields:
        gv, pv = gold.get(f), pred.get(f)
        if f == "company_name":
            ok = (gv == pv) or (not gv and not pv)
        elif f == "claim_type" and (gold.get("is_forecast") or gold.get("claim_type") == "forecast"):
            ok = pv == "forecast" or bool(pred.get("is_forecast"))
        elif f in {"value", "unit", "operator"}:
            ok = (gv or None) == (pv or None)
        else:
            ok = gv == pv
        hits[f] = bool(ok)
    hits["all"] = all(hits[f] for f in fields)
    return hits


def field_rows(bundle: dict, gold: dict) -> dict:
    """所有金标原子与解析项一对一对齐；多余预测按零分计入字段均准。"""
    preds = ((bundle.get("parsed") or {}).get("items") or [])
    gold_items, identities, empty = [], [], []
    for index, sentence in enumerate(gold["sentences"], 1):
        items = gold_items_for(sentence, index)
        if not items:
            empty.append((index, sentence.get("id")))
        for atom_index, item in enumerate(items):
            gold_items.append(item)
            atoms = sentence.get("atoms") or [sentence]
            identities.append(atoms[atom_index].get("id"))
    aligned, extras = align_rows(preds, gold_items)
    rows = []
    for ident, item, pred in zip(identities, gold_items, aligned):
        hits = score_fields(pred, item) if pred is not None else {}
        fields = hits.get("_fields") or list(FIELDS)
        rows.append({"id": ident, "sentence_id": item["sentence_id"],
                     "quote": item["quote"], "extracted": pred is not None,
                     "items": int(pred is not None),
                     "field_acc": sum(bool(hits.get(f)) for f in fields) / len(fields),
                     "exact": bool(hits.get("all")), "note": None if pred is not None else "漏拆/无法唯一对齐"})
    for sid, ident in empty:
        unexpected = any(pred.get("sentence_id") == sid for pred in preds)
        rows.append({"id": ident, "sentence_id": sid, "extracted": False, "items": 0,
                     "field_acc": 0.0 if unexpected else 1.0, "exact": not unexpected,
                     "note": "金标无主张"})
    denominator = len(rows) + len(extras)
    return {"rows": rows, "extra_predictions": len(extras), "extras": extras,
            "field_avg": round(sum(row["field_acc"] for row in rows) / denominator, 4) if denominator else 0,
            "exact_match": sum(bool(row["exact"]) for row in rows),
            "extracted": sum(bool(row["extracted"]) for row in rows), "total": len(rows)}


def rehearsal_score(gold: dict, facts: list[dict], *, mutate=None) -> dict:
    """自检：金标协议样例 / 扰动样例各跑一次完整 check_text，验证评测机与流程接线。"""
    draft = "\n".join(sentence["text"] for sentence in gold["sentences"])
    sentences = split_draft(draft)
    items = []
    for index, sentence in enumerate(sentences, 1):
        built = gold_items_for({"text": sentence["text"],
                                **gold["sentences"][index - 1]}, index)
        if not built:
            continue
        for item in built:
            items.append(mutate(item) if mutate else item)
    with tempfile.TemporaryDirectory(prefix="e5c_rehearsal_") as temp:
        run = Run(Path(temp), "e5c_rehearsal", {})
        bundle = run_full_flow(draft, facts, run, ReplayClient(items=items), use_loop=False)
    scored = score_atomic(bundle, statements_for(gold))
    fields = field_rows(bundle, gold)
    return {"bundle_status": bundle.get("status"), "chunks": len(bundle.get("chunks") or []),
            "atomic": scored["summary"], "rows": scored["rows"],
            "fields": {key: fields[key] for key in ("field_avg", "exact_match", "extracted", "total", "extra_predictions")}}


def live_score(client, gold: dict, facts: list[dict], run, *, use_loop: bool) -> dict:
    """真模型：草稿交给当前 check_text；金标只用于评分。"""
    draft = "\n".join(sentence["text"] for sentence in gold["sentences"])
    try:
        bundle = run_full_flow(draft, facts, run, client, use_loop=use_loop)
    except LLMError as exc:
        bundle = {"status": "failed", "reason": str(exc), "checks": [],
                  "unfinished_sentence_ids": list(range(1, len(gold["sentences"]) + 1)),
                  "sentences": [{"sentence_id": i} for i in range(1, len(gold["sentences"]) + 1)]}
    scored = score_atomic(bundle, statements_for(gold))
    fields = field_rows(bundle, gold)
    tools_used = bundle.get("tools_used") or []
    return {"name": f"E5c live（{bundle.get('mode') or 'failed'}）",
            "mode": bundle.get("mode"), "bundle_status": bundle.get("status"),
            "binding_sha256": bundle.get("binding_sha256"),
            "checkpoint_file": bundle.get("checkpoint_file"),
            "reason": bundle.get("reason"), "chunks": len(bundle.get("chunks") or []),
            "failed_chunks": len(bundle.get("failed_chunks") or []),
            "schema_valid": all(row["outcome"] != "failed" for row in scored["rows"]) and bool(bundle.get("checks")),
            "tools_used": tools_used,
            "atomic": scored["summary"], "rows": scored["rows"],
            "fields": {key: fields[key] for key in ("field_avg", "exact_match", "extracted", "total", "extra_predictions")},
            "field_rows": fields["rows"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="原子陈述端到端评测")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-travel-env", action="store_true")
    parser.add_argument("--no-loop", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    dry, from_travel, use_loop = args.dry_run, args.from_travel_env, not args.no_loop
    out = args.output_dir.resolve() if args.output_dir else Path(tempfile.mkdtemp(prefix="fin_e5c_eval_"))
    out.mkdir(parents=True, exist_ok=True)
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))

    run = Run(out, "run_llm_eval", {"dry_run": dry, "from_travel": from_travel,
                                           "use_loop": use_loop})
    print(f"输出目录：{out}；运行记录：{run.folder}", flush=True)
    facts = []
    for code, year in (("600519", 2024), ("000858", 2023)):
        _m, got, _f = extract_selected(ROOT, run, code, year, False)
        facts.extend(got)

    report = ["# E5c · 模型拆句准确率（统一走 check_text）", "",
              f"- 金标句 **{len(gold['sentences'])}** 条；证据 **{len(facts)}** 条。",
              f"- 字段集合：{', '.join(FIELDS)}",
              "- 评分口径：逐条原子陈述；漏拆、超时、弃答与失败单独计数，不计入正确。", ""]

    if dry:
        def mutate(item):
            item = json.loads(json.dumps(item))
            item["value"] = "9999"
            item["metric"] = "revenue" if item["metric"] != "revenue" else "parent_net_profit"
            item["operator"] = "exceed"
            return item

        perfect = rehearsal_score(gold, facts)
        perturbed = rehearsal_score(gold, facts, mutate=mutate)
        result = {"name": "E5c-dryrun 评测机自检", "perfect_parse": perfect,
                  "perturbed_parse": perturbed,
                  "note": "不测真模型；验证完整流程与打分：完美解析应≈100%，扰动应明显下降"}
        write_json(out / "e5c_dryrun.json", result)
        report += ["## 评测机自检（--dry-run）", "",
                   "| 解析质量 | 判定对/应判 | 弃答 | 漏拆 | 失败 | 字段均准 | 全字段正确 |",
                   "|---|---|---|---|---|---|---|"]
        for label, item in (("金标解析", perfect), ("扰动解析", perturbed)):
            atomic = item["atomic"]
            report.append(f"| {label} | {atomic['correct']}/{atomic['expected_judge']} "
                          f"| {atomic['abstain']} | {atomic['omitted']} | "
                          f"{atomic['failed'] + atomic['timeout']} "
                          f"| {item['fields']['field_avg']:.2%} "
                          f"| {item['fields']['exact_match']}/{item['fields']['total']} |")
        report += ["", result["note"], ""]
        (out / "e5c_report.md").write_text("\n".join(report), encoding="utf-8")
        print(f"dry-run 判定 {perfect['atomic']['correct']}/{perfect['atomic']['expected_judge']} "
              f"vs 扰动 {perturbed['atomic']['correct']}/{perturbed['atomic']['expected_judge']}")
        print(f"报告 {out / 'e5c_report.md'}")
        run.finish(status="ok", mode="dry_run")
        return 0

    # 真模型
    picked = load_travel_env() if from_travel else {}
    base, model, key = pick_provider(picked)
    if not key:
        print("未配置 LLM_API_KEY；请设环境变量或使用 --from-travel-env / --dry-run")
        return 2

    client = LLMClient(base, model, key, "json_object")
    live = live_score(client, gold, facts, run, use_loop=use_loop)
    tag = {"json_multi_step": "loop", "fallback_single_shot": "single_fallback",
           "fallback_single_shot_tools_preserved": "single_fallback",
           "native_tool_calls": "native", "single_shot": "single"}.get(live.get("mode"), "single")
    write_json(out / f"e5c_live_{tag}.json", live)
    atomic = live["atomic"]
    report += [f"## 真模型（{live.get('mode') or 'failed'}）", "",
               f"- 模型：{model} @ {client.host}",
               f"- 核查流程状态：{live.get('bundle_status')}"
               f"（分块 {live.get('chunks')}，失败分块 {live.get('failed_chunks')}）",
               f"- 工具调用：{len(live.get('tools_used') or [])} 次；"
               f"用量：{atomic.get('tokens_charged')} 计费 token、"
               f"尝试 {atomic.get('attempts')} 次、耗时 {atomic.get('active_seconds')} 秒",
               "", "| 维度 | 结果 |", "|---|---|",
               f"| 判定正确 | **{atomic['correct']}/{atomic['judged']}**"
               f"（已判定正确率 {atomic['accuracy']}） |",
               f"| 应判覆盖 | **{atomic['correct']}/{atomic['expected_judge']}**"
               f"（正确覆盖 {atomic['correct_coverage']}） |",
               f"| 合理弃答 | **{atomic['abstain_expected']}/{atomic['abstain']}** |",
               f"| 漏拆 | **{atomic['omitted']}** |",
               f"| 超时 / 失败 | **{atomic['timeout']} / {atomic['failed']}** |",
               f"| 字段均准 | **{live['fields']['field_avg']:.2%}** |",
               f"| 全字段完全一致 | **{live['fields']['exact_match']}/{live['fields']['total']}** |",
               f"| 成功拆出 | **{live['fields']['extracted']}/{live['fields']['total']}** |",
               "", "### 逐条原子陈述", "",
               "| ID | 结局 | 状态 | 金标 | 证据 | ✓ |", "|---|---|---|---|---|---|"]
    for row in live["rows"]:
        report.append(f"| {row['id']} | {row['outcome']} | {row.get('status') or '—'} "
                      f"| {row['expect'].get('status') or row['expect'].get('kind')} "
                      f"| {'✓' if row['evidence_ids'] else '—'} "
                      f"| {'✓' if row['ok'] else ('✗' if row['ok'] is False else '—')} |")
    report += ["", "口径：功能评测与真实模型结果分开；单次运行不能当作业务准确率，"
                   "至少三次重复运行后再报告稳定性。", ""]
    (out / f"e5c_report_{tag}.md").write_text("\n".join(report), encoding="utf-8")
    run.finish(status="ok", mode=live.get("mode"), correct=atomic["correct"],
               judged=atomic["judged"], total=atomic["total"])
    print(f"E5c 判定 {atomic['correct']}/{atomic['judged']} · "
          f"漏拆 {atomic['omitted']} · 弃答 {atomic['abstain']} · 失败 {atomic['failed']}")
    print(f"报告 {out / f'e5c_report_{tag}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
