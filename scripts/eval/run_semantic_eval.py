"""E3–E5 语义评测：指标对齐 / 研报风格句 / 新旧口径消融对比。

用法（仓库根）：python scripts/eval/run_semantic_eval.py

- E3 metric_alignment：中文指标名 → 目录键（含 ambiguous）
- E4 sentences：研报风格句在「结构化主张 + 真实证据」上的裁决是否符合金标
- E5 ablation：旧口径（仅 eq + 4 指标 + 预测拒判）vs 新口径（运算符 + 8 指标 + 预测标记）
- E5b 基线：自由文本回答（无证据链）vs 本系统（必须带 evidence_id）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from llm_check import METRICS, check_one_claim  # noqa: E402
from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402
from main import extract_selected  # noqa: E402

GOLD_PATH = ROOT / "data" / "eval" / "semantic_gold.json"
OLD_METRICS = {"revenue", "parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow"}


def resolve_metric(text: str) -> str:
    """与 check_one_claim 相同的别名对齐；对不上给 unknown/ambiguous。"""
    for key, names in METRICS.items():
        if text in names:
            return key
    if text in {"净利润", "净利", "利润", "净利润总额"}:
        return "ambiguous_profit"
    return "unknown" if text else "unknown"


def run_e3(cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        predicted = resolve_metric(case["text"])
        expected = case["expected"]
        if expected == "ambiguous_profit":
            ok = predicted == "ambiguous_profit"
        elif expected == "unsupported":
            ok = predicted not in METRICS
        elif expected == "unknown":
            ok = predicted not in METRICS or predicted in {"unknown"}
        else:
            ok = predicted == expected
        rows.append({"text": case["text"], "expected": expected,
                     "predicted": predicted, "ok": ok})
    hit = sum(1 for r in rows if r["ok"])
    return {"name": "E3 指标语义对齐", "total": len(rows), "hit": hit,
            "accuracy": round(hit / len(rows), 4) if rows else 0, "rows": rows}


def make_fact_item(fact: dict, **over) -> dict:
    return {
        "claim_id": over.get("claim_id", "C1"),
        "sentence_id": 1,
        "quote": over.get("quote") or "",
        "context_quote": None,
        "claim_type": over.get("claim_type", "amount"),
        "company_name": over.get("company_name") or fact.get("company_name") or "贵州茅台",
        "period_year": over.get("period_year", fact.get("period_year", 2024)),
        "period_kind": "annual",
        "metric_text": over.get("metric_text", "营业收入"),
        "metric": over.get("metric", fact.get("metric", "revenue")),
        "scope": over.get("scope", fact.get("scope") or "consolidated"),
        "kind": over.get("kind", "amount"),
        "value": over.get("value"),
        "unit": over.get("unit"),
        "operator": over.get("operator", "eq"),
        "tolerance_pct": over.get("tolerance_pct"),
        "direction": over.get("direction", "unknown"),
        "currency": "CNY",
        "qualifier": over.get("qualifier", "exact"),
        "plain_claim": over.get("plain_claim", "—"),
        "is_forecast": bool(over.get("is_forecast")),
        "ambiguity": over.get("ambiguity", "none"),
        "verification_action": over.get("verification_action", "compare_amount"),
    }


def run_e4(facts: list[dict], gold: dict) -> dict:
    index = {(f["company_code"], f["metric"], f["period_year"]): f for f in facts}
    rows = []
    for sent in gold["sentences"]:
        text = sent["text"]
        company = sent.get("company_name", "贵州茅台")
        code = {"贵州茅台": "600519", "五粮液": "000858"}.get(company, "600519")
        year = sent.get("period_year", 2024)
        metric_key = sent.get("metric", "revenue")
        fact = index.get((code, metric_key, year)) or index.get((code, "revenue", year))
        base_item = make_fact_item(
            fact or {"company_name": company, "period_year": year, "metric": metric_key},
            claim_id=sent["id"], quote=text, company_name=company, period_year=year,
            metric_text=sent.get("metric_text", text), metric=sent.get("metric", metric_key),
            kind=sent.get("kind", "amount"), value=sent.get("value"),
            unit=sent.get("unit"), operator=sent.get("operator", "eq"),
            tolerance_pct=sent.get("tolerance_pct"),
            claim_type=sent.get("claim_type", "amount"),
            is_forecast=sent.get("is_forecast", False),
            direction=sent.get("direction", "unknown"),
            plain_claim=sent.get("note") or text[:40],
            verification_action=sent.get("verification_action", "compare_amount"),
        )
        if not fact and sent.get("gold") not in {"forecast_mark_only", "qualitative", "ambiguous_metric"}:
            result = {"status": "证据不足", "track": "review",
                      "reason_code": "no_fact_fixture", "evidence_ids": []}
        else:
            result = check_one_claim(base_item, facts, text, text, sent["id"])
        predicted = result.get("reason_code") or result.get("status")
        # 金标：should_judge 且 gold_error → 期望确认错误；gold_error=false → 证据支持
        # forecast_mark_only / qualitative / ambiguous_metric → 期望模型判断或人工
        gold_class = sent["gold"]
        status = result.get("status")
        if gold_class == "should_judge":
            want_error = sent.get("gold_error")
            ok = (status == "确认错误") if want_error else (status == "证据支持")
        elif gold_class == "forecast_mark_only":
            ok = status == "模型判断" and predicted == "forecast_marked_only"
        elif gold_class == "qualitative":
            ok = status in {"模型判断", "口径冲突／需人工复核"}
        elif gold_class == "ambiguous_metric":
            ok = status in {"口径冲突／需人工复核", "证据不足", "模型判断"} \
                and predicted not in {"deterministic_check"}
        else:
            ok = False
        rows.append({
            "id": sent["id"], "text": text, "gold": gold_class,
            "gold_error": sent.get("gold_error"), "status": status,
            "track": result.get("track"), "reason_code": predicted,
            "evidence_ids": result.get("evidence_ids") or [],
            "ok": ok,
        })
    hit = sum(1 for r in rows if r["ok"])
    return {"name": "E4 研报风格句金标", "total": len(rows), "hit": hit,
            "accuracy": round(hit / len(rows), 4) if rows else 0, "rows": rows}


def old_gate(sent: dict) -> str:
    """旧口径：仅 4 指标、仅 eq、约数/预测拒判。"""
    if sent.get("is_forecast") or sent.get("claim_type") == "forecast":
        return "拒判 non_historical_claim"
    if sent.get("claim_type") in {"qualitative"}:
        return "拒判 unsupported_operation"
    if sent.get("metric") not in OLD_METRICS:
        return "拒判 unsupported_metric"
    if sent.get("operator", "eq") != "eq":
        return "拒判 non_exact_claim"
    return "可进数值裁决"


def new_gate(sent: dict) -> str:
    if sent.get("is_forecast") or sent.get("claim_type") == "forecast":
        return "标记 forecast_marked_only"
    if sent.get("claim_type") == "qualitative":
        return "模型判断 qualitative"
    if sent.get("metric") in METRICS:
        return "可进数值裁决"
    return "需人工 / 证据不足"


def run_e5_ablation(gold: dict) -> dict:
    rows = []
    for sent in gold["sentences"]:
        old, new = old_gate(sent), new_gate(sent)
        rows.append({"id": sent["id"], "text": sent["text"][:40],
                     "old": old, "new": new,
                     "improved": new.startswith("可进") or new.startswith("标记")
                     or new.startswith("模型")})
    old_judge = sum(1 for r in rows if r["old"].startswith("可进"))
    new_judge = sum(1 for r in rows if r["new"].startswith("可进"))
    old_reject = sum(1 for r in rows if r["old"].startswith("拒判"))
    new_covered = sum(1 for r in rows if r["improved"])
    return {
        "name": "E5 口径消融（旧 vs 新）",
        "total": len(rows),
        "old_can_judge": old_judge, "new_can_judge": new_judge,
        "old_reject": old_reject, "new_covered": new_covered,
        "coverage_lift": round(new_covered / len(rows) - (len(rows) - old_reject) / len(rows), 4)
        if rows else 0,
        "rows": rows,
    }


def run_e5_baseline(rows_e4: list[dict]) -> dict:
    """基线对比：自由回答（无证据）vs 本系统（证据链）。用结构事实陈述，不调外网 LLM。"""
    compared = []
    for r in rows_e4:
        baseline = {
            "answer_style": "自由陈述对错/复述",
            "has_evidence_id": False,
            "has_page": False,
            "recomputable": False,
        }
        system = {
            "status": r["status"], "track": r["track"],
            "has_evidence_id": bool(r.get("evidence_ids")),
            "has_page": bool(r.get("evidence_ids")),
            "recomputable": r.get("track") == "deterministic",
        }
        compared.append({"id": r["id"], "baseline": baseline, "system": system})
    sys_evidence = sum(1 for c in compared if c["system"]["has_evidence_id"])
    base_evidence = sum(1 for c in compared if c["baseline"]["has_evidence_id"])
    sys_recompute = sum(1 for c in compared if c["system"]["recomputable"])
    return {
        "name": "E5b 基线对比（自由回答 vs 证据链）",
        "total": len(compared),
        "baseline_with_evidence": base_evidence,
        "system_with_evidence": sys_evidence,
        "system_recomputable": sys_recompute,
        "note": "基线口径=通用大模型直接作答（无页码/哈希/可复算）；系统口径=代码裁决必须带 evidence_id",
        "rows": compared,
    }


def main() -> int:
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    run = Run(AGENT_ROOT, "run_semantic_eval", {})
    print(f"运行记录：{run.folder}", flush=True)
    facts = []
    for code, year in (("600519", 2024), ("000858", 2023)):
        _m, got, _f = extract_selected(AGENT_ROOT, run, code, year, False)
        facts.extend(got)
        print(f"  证据 {code} {year}：{len(got)}", flush=True)

    e3 = run_e3(gold["metric_alignment"])
    e4 = run_e4(facts, gold)
    e5 = run_e5_ablation(gold)
    e5b = run_e5_baseline(e4["rows"])

    out_dir = ROOT / "results" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "semantic_eval.json", {"E3": e3, "E4": e4, "E5": e5, "E5b": e5b})

    report = ["# E3–E5 语义评测与对比", "",
              f"- 装载证据 **{len(facts)}** 条；金标 `data/eval/semantic_gold.json`（程序标注，待团队复签）。",
              "- 不调用外网 LLM：E3/E4 评的是**别名对齐 + 确定性核验管道**；真·模型拆句准确率需另配密钥。", "",
              "## E3 指标语义对齐", "",
              f"- **{e3['hit']}/{e3['total']} = {e3['accuracy']:.2%}**",
              "",
              "| 原文 | 期望 | 预测 | ✓ |", "|---|---|---|---|"]
    for r in e3["rows"]:
        report.append(f"| {r['text']} | {r['expected']} | {r['predicted']} | {'✓' if r['ok'] else '✗'} |")

    report += ["", "## E4 研报风格句", "",
               f"- **{e4['hit']}/{e4['total']} = {e4['accuracy']:.2%}**（含约数/预测/定性/口径陷阱）",
               "",
               "| ID | 句子 | 金标 | 状态 | 轨 | ✓ |", "|---|---|---|---|---|---|"]
    for r in e4["rows"]:
        report.append(f"| {r['id']} | {r['text'][:36]}… | {r['gold']} | {r['status']} | {r['track']} | "
                      f"{'✓' if r['ok'] else '✗'} |")

    report += ["", "## E5 口径消融：旧 vs 新", "",
               f"| 口径 | 可进数值裁决 | 覆盖改进 |",
               f"|---|---|---|",
               f"| 旧（仅 eq·4 指标·预测拒判） | {e5['old_can_judge']}/{e5['total']}（拒判 {e5['old_reject']}） | — |",
               f"| 新（运算符·8 指标·预测标记） | {e5['new_can_judge']}/{e5['total']}（有归宿 {e5['new_covered']}） | "
               f"{e5['old_can_judge']} → {e5['new_covered']} 条句有了明确归宿 |",
               "",
               "### 逐句对照", "",
               "| ID | 旧口径 | 新口径 |", "|---|---|---|"]
    for r in e5["rows"]:
        report.append(f"| {r['id']} | {r['old']} | {r['new']} |")

    report += ["", "## E5b 基线：自由回答 vs 证据链", "",
               f"| 维度 | 自由 LLM 作答（基线） | 本系统 |",
               f"|---|---|---|",
               f"| 附证据 ID | {e5b['baseline_with_evidence']}/{e5b['total']} | **{e5b['system_with_evidence']}/{e5b['total']}** |",
               f"| 可复算（确定性轨） | 0/{e5b['total']} | **{e5b['system_recomputable']}/{e5b['total']}** |",
               "",
               "> 结论：基线能说「大概对/错」，但**指不出页、算不出过程**；系统 A 栏每条确定结论都能回年报页。",
               "真·模型拆句对比需接入在线 LLM 后补 E5c（同句喂裸模型 vs 工具循环）。", ""]

    (out_dir / "semantic_eval_report.md").write_text("\n".join(report), encoding="utf-8")
    run.finish(status="ok", e3=e3["accuracy"], e4=e4["accuracy"],
               e5_old=e5["old_can_judge"], e5_new=e5["new_can_judge"])
    print(f"E3 对齐 {e3['accuracy']:.2%} · E4 金标 {e4['accuracy']:.2%} · "
          f"E5 裁决入口 {e5['old_can_judge']}→{e5['new_can_judge']}·覆盖归宿 {e5['new_covered']}")
    print(f"报告 {out_dir / 'semantic_eval_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
