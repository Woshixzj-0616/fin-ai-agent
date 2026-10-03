"""E5c · 模型拆句准确率评测。

评什么
------
1. **字段级**：公司/年度/指标/数值/单位/运算符/claim_type 与金标是否一致
2. **句覆盖**：该拆的句有没有拆出来
3. **端到端**：经本地裁决后，最终对错是否与金标一致
4. **对照**：单次 extract vs JSON 多步工具循环（同一批句）

用法（仓库根）：
  set LLM_BASE_URL=...  set LLM_MODEL=...  set LLM_API_KEY=...
  python scripts/eval/run_llm_eval.py

  # 若密钥在智能旅行 backend/.env（只读、不回显）：
  python scripts/eval/run_llm_eval.py --from-travel-env

无密钥时：--dry-run 用「金标解析 + 扰动解析」自检评测机（不算模型分）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from llm_check import LLMClient, LLMError, check_one_claim, check_payload, schema, split_draft  # noqa: E402
from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402
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
    """把金标句上的字段填成协议 item（用于精确解析对照与 dry-run）。"""
    if sentence.get("gold") in {"qualitative", "ambiguous_metric"} and not sentence.get("value"):
        return []
    company = sentence.get("company_name", "贵州茅台")
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
        "metric": sentence.get("metric", "revenue"),
        "scope": sentence.get("scope", "consolidated"),
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


class StubClient:
    """dry-run / 自检：按脚本模式返回金标或扰动解析。"""

    model, host, key, mode = "stub-gold", "local", "dry-run", "json_object"

    def __init__(self, mode="gold"):
        self.mode_name = mode

    def extract(self, sentences, run):
        items, unused = [], []
        for i, s in enumerate(sentences, 1):
            payload_items = gold_items_for({"text": s["text"], **{}}, i)
            # dry-run 用全量金标文件映射
            unused.append(i)
        return {"items": [], "unclaimed_sentences": [s["sentence_id"] for s in sentences]}

    def chat(self, messages, run):
        return json.dumps({"action": "submit_claims", "items": [], "unclaimed_sentences": []},
                          ensure_ascii=False)


def dry_run_score(gold: dict, facts: list[dict]) -> dict:
    """评测机自检：金标解析 vs 故意扰动，应得到 ~100% vs 明显下降。"""
    def batch(mutate=None):
        rows = []
        for i, sent in enumerate(gold["sentences"], 1):
            items = gold_items_for(sent, i)
            if not items:
                rows.append({"id": sent["id"], "field_acc": 1.0, "all": True, "e2e_ok": True,
                             "skip": True})
                continue
            item = json.loads(json.dumps(items[0]))
            if mutate:
                item = mutate(item)
            result = check_one_claim(item, facts, sent["text"], sent["text"], sent["id"])
            gold_items = gold_items_for(sent, i)[0]
            hits = score_fields(item, gold_items)
            if sent["gold"] == "should_judge":
                want = "确认错误" if sent.get("gold_error") else "证据支持"
                e2e_ok = result.get("status") == want
            elif sent["gold"] == "forecast_mark_only":
                e2e_ok = result.get("reason_code") == "forecast_marked_only"
            else:
                e2e_ok = result.get("status") in {"模型判断", "口径冲突／需人工复核"}
            n_fields = hits.get("_fields") or list(FIELDS)
            n_ok = sum(1 for f in n_fields if hits.get(f))
            rows.append({"id": sent["id"], "field_acc": n_ok / len(n_fields), "all": hits["all"],
                         "e2e_ok": e2e_ok, "reason": result.get("reason_code")})
        return rows

    def mutate(item):
        item = json.loads(json.dumps(item))
        item["value"] = "9999"
        item["metric"] = "revenue" if item["metric"] != "revenue" else "parent_net_profit"
        item["operator"] = "exceed"
        return item

    gold_rows, bad_rows = batch(None), batch(mutate)

    def summarize(rows):
        used = [r for r in rows if not r.get("skip")]
        field = sum(r["field_acc"] for r in used) / len(used) if used else 0
        exact = sum(1 for r in used if r["all"])
        e2e = sum(1 for r in used if r["e2e_ok"])
        return {"field_avg": round(field, 4), "exact_match": exact, "e2e_ok": e2e,
                "total": len(used)}

    return {"name": "E5c-dryrun 评测机自检",
            "perfect_parse": summarize(gold_rows),
            "perturbed_parse": summarize(bad_rows),
            "note": "不测真模型；验证打分逻辑：完美解析应≈100%，扰动应明显下降"}


def live_score(client, gold: dict, facts: list[dict], run, *, use_loop: bool) -> dict:
    draft = "\n".join(s["text"] for s in gold["sentences"])
    sentences = split_draft(draft)
    if use_loop and hasattr(client, "chat"):
        try:
            from agent_loop import run_loop
            out = run_loop(client, draft, facts, run, sentences=sentences)
            payload, mode = out["payload"], out["mode"]
            tools_used = out.get("tools_used") or []
        except LLMError as exc:
            run.event("loop_fallback", reason=str(exc))
            payload, mode, tools_used = client.extract(sentences, run), "fallback_single_shot", []
    else:
        payload, mode, tools_used = client.extract(sentences, run), "single_shot", []

    validate_ok = True
    try:
        from llm_check import validate_schema
        validate_schema(payload, schema())
    except LLMError:
        validate_ok = False

    # 按 sentence_id 对齐金标
    gold_by_id = {i: s for i, s in enumerate(gold["sentences"], 1)}
    pred_by_sid = {}
    for item in payload.get("items") or []:
        pred_by_sid.setdefault(item.get("sentence_id"), []).append(item)

    rows = []
    for sid, sent in gold_by_id.items():
        preds = pred_by_sid.get(sid) or []
        gitems = gold_items_for(sent, sid)
        if not gitems and not preds:
            rows.append({"id": sent["id"], "extracted": False, "field_acc": 1.0,
                         "exact": True, "e2e_ok": True, "note": "金标即无主张"})
            continue
        if not preds:
            rows.append({"id": sent["id"], "extracted": False, "field_acc": 0.0,
                         "exact": False, "e2e_ok": False, "note": "漏拆"})
            continue
        pred, gitem = preds[0], gitems[0] if gitems else {}
        hits = score_fields(pred, gitem) if gitem else {"all": False, "_fields": list(FIELDS)}
        result = check_one_claim(pred, facts, sent["text"], draft, sent["id"])
        if sent["gold"] == "should_judge":
            want = "确认错误" if sent.get("gold_error") else "证据支持"
            e2e_ok = result.get("status") == want
        elif sent["gold"] == "forecast_mark_only":
            e2e_ok = result.get("reason_code") == "forecast_marked_only"
        else:
            e2e_ok = result.get("status") in {"模型判断", "口径冲突／需人工复核", "证据不足"}
        n_fields = hits.get("_fields") or list(FIELDS)
        n_ok = sum(1 for f in n_fields if hits.get(f))
        rows.append({"id": sent["id"], "extracted": True, "field_acc": n_ok / len(n_fields),
                     "exact": hits.get("all"), "e2e_ok": e2e_ok,
                     "status": result.get("status"), "reason": result.get("reason_code")})

    used = rows
    field = sum(r["field_acc"] for r in used) / len(used) if used else 0
    return {
        "name": f"E5c live（{mode}）",
        "mode": mode, "schema_valid": validate_ok,
        "tools_used": tools_used,
        "field_avg": round(field, 4),
        "exact_match": sum(1 for r in used if r.get("exact")),
        "extracted": sum(1 for r in used if r.get("extracted")),
        "e2e_ok": sum(1 for r in used if r.get("e2e_ok")),
        "total": len(used),
        "rows": rows,
    }


def main() -> int:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    from_travel = "--from-travel-env" in args
    use_loop = "--no-loop" not in args

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    run = Run(AGENT_ROOT, "run_llm_eval", {"dry_run": dry, "from_travel": from_travel,
                                           "use_loop": use_loop})
    print(f"运行记录：{run.folder}", flush=True)
    facts = []
    for code, year in (("600519", 2024), ("000858", 2023)):
        _m, got, _f = extract_selected(AGENT_ROOT, run, code, year, False)
        facts.extend(got)

    report = ["# E5c · 模型拆句准确率", "",
              f"- 金标句 **{len(gold['sentences'])}** 条；证据 **{len(facts)}** 条。",
              f"- 字段集合：{', '.join(FIELDS)}", ""]

    if dry:
        result = dry_run_score(gold, facts)
        write_json(run.output("e5c_dryrun.json"), result)
        p, b = result["perfect_parse"], result["perturbed_parse"]
        report += ["## 评测机自检（--dry-run）", "",
                   "| 解析质量 | 字段均准 | 全字段正确 | 端到端对 |",
                   "|---|---|---|---|",
                   f"| 金标解析 | {p['field_avg']:.2%} | {p['exact_match']}/{p['total']} | {p['e2e_ok']}/{p['total']} |",
                   f"| 扰动解析 | {b['field_avg']:.2%} | {b['exact_match']}/{b['total']} | {b['e2e_ok']}/{b['total']} |",
                   "",
                   result["note"], ""]
        out = ROOT / "results" / "eval"
        out.mkdir(parents=True, exist_ok=True)
        (out / "e5c_report.md").write_text("\n".join(report), encoding="utf-8")
        print(f"dry-run 完美 {p['field_avg']:.2%} vs 扰动 {b['field_avg']:.2%}")
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
           "single_shot": "single"}.get(live.get("mode"), "single")
    write_json(run.output(f"e5c_live_{tag}.json"), live)

    report += [f"## 真模型（{live['mode']}）", "",
               f"- 模型：`{model}` @ `{client.host}`",
               f"- Schema 校验：{'通过' if live['schema_valid'] else '失败'}",
               f"- 工具调用：{len(live.get('tools_used') or [])} 次",
               "",
               "| 维度 | 结果 |",
               "|---|---|",
               f"| 字段均准 | **{live['field_avg']:.2%}** |",
               f"| 全字段完全一致 | **{live['exact_match']}/{live['total']}** |",
               f"| 成功拆出 | **{live['extracted']}/{live['total']}** |",
               f"| 端到端判定对 | **{live['e2e_ok']}/{live['total']}** |",
               "", "### 逐句", "",
               "| ID | 拆出 | 字段准 | 完全一致 | 端到端 | 状态 |", "|---|---|---|---|---|---|"]
    for r in live["rows"]:
        report.append(f"| {r['id']} | {'✓' if r.get('extracted') else '✗'} | "
                      f"{r.get('field_acc', 0):.0%} | {'✓' if r.get('exact') else '✗'} | "
                      f"{'✓' if r.get('e2e_ok') else '✗'} | {r.get('status') or r.get('note') or '—'} |")

    out = ROOT / "results" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"e5c_report_{tag}.md").write_text("\n".join(report), encoding="utf-8")
    run.finish(status="ok", mode=live["mode"], field_avg=live["field_avg"],
               exact=live["exact_match"], e2e=live["e2e_ok"], total=live["total"])
    print(f"E5c 字段均准 {live['field_avg']:.2%} · 完全一致 {live['exact_match']}/{live['total']} · "
          f"端到端 {live['e2e_ok']}/{live['total']}")
    print(f"报告 {out / f'e5c_report_{tag}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
