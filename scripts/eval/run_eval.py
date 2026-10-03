"""错误注入评测：真实抽取证据 → 按证据造准确/注错陈述 → check_claim → P/R。

口径（测的是**确定性核查器**，不是抽取器）：
- 准确项的值来自本次抽出的证据，期望「证据支持」
- 注错项在证据值上做受控扰动，期望「确认错误」
- 带 issues 的证据不进主评测（单独报覆盖率）

用法（仓库根）：python scripts/eval/run_eval.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from finance import check_claim, decimal, evidence_yoy, select_previous, text  # noqa: E402
from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402
from main import extract_selected  # noqa: E402

EVAL_CODES = ["600519", "000858", "000333", "600276", "002594", "600036"]
EVAL_YEARS = [2024, 2023]
CORE = {"revenue", "parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow"}
SCOPE = {
    "revenue": "consolidated", "operating_cash_flow": "consolidated",
    "parent_net_profit": "parent_shareholders", "adjusted_parent_net_profit": "parent_shareholders",
}
METRIC_CN = {
    "revenue": "营业收入", "parent_net_profit": "归母净利润",
    "adjusted_parent_net_profit": "扣非归母净利润", "operating_cash_flow": "经营现金流净额",
}
UNIT_CN = {"元": "元", "亿元": "亿元", "万元": "万元", "%": "%"}


def q2(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def to_yi(value, unit: str) -> tuple[str, str] | None:
    """证据值 → 草稿常用的（数值, 单位）。金额统一成亿元。"""
    d = decimal(value)
    if d is None:
        return None
    if unit == "%":
        return q2(d), "%"
    factors = {"元": Decimal("100000000"), "万元": Decimal("10000"), "亿元": Decimal("1")}
    if unit not in factors:
        return None
    return q2(d / factors[unit]), "亿元"


def build_from_facts(facts: list[dict]) -> list[dict]:
    claims = []
    seq = 0

    def add(fact, kind, value, unit, expect, error_type, sentence):
        nonlocal seq
        seq += 1
        claims.append({
            "id": f"E{seq:03d}", "sentence": sentence, "kind": kind,
            "company_code": fact["company_code"], "company_name": fact.get("company_name", ""),
            "source_report_year": fact["report_year"], "period_year": fact["period_year"],
            "period_kind": "annual", "metric": fact["metric"],
            "scope": SCOPE[fact["metric"]], "currency": "CNY",
            "value": value, "unit": unit,
            "gold_expect": expect, "error_type": error_type,
            "evidence_id": fact["evidence_id"], "evidence_page": fact.get("page"),
        })

    index = {}
    for f in facts:
        if f["metric"] in CORE and f.get("adjustment") == "as_reported" and not f.get("issues"):
            index[(f["company_code"], f["report_year"], f["period_year"], f["metric"])] = f

    for (code, year, period, metric), fact in sorted(index.items()):
        if period != year:
            continue  # 主评测只用「本年列」原值
        name = fact.get("company_name") or code
        cn = METRIC_CN[metric]
        pair = to_yi(fact["value"], fact["unit"] or "元")
        if not pair:
            continue
        base, unit = pair
        # 1) 准确金额
        add(fact, "amount", base, unit, "证据支持", "accurate",
            f"{period}年，{name}{cn}为{base}{unit}。")
        # 2) 错值
        wrong = q2(Decimal(base) * Decimal("1.15")) if unit != "%" else q2(Decimal(base) + Decimal("3.33"))
        if wrong == base:
            wrong = q2(Decimal(base) + Decimal("1.01"))
        add(fact, "amount", wrong, unit, "确认错误", "error_value",
            f"{period}年，{name}{cn}为{wrong}{unit}。")
        # 3) 错单位（金额项才注入）
        if unit == "亿元":
            add(fact, "amount", base, "万元", "确认错误", "error_unit",
                f"{period}年，{name}{cn}为{base}万元。")
        # 4) 错年份：把上一年数值写成本年
        prev = index.get((code, year, period - 1, metric))
        if prev:
            prev_pair = to_yi(prev["value"], prev["unit"] or "元")
            if prev_pair and prev_pair[1] == unit:
                add(fact, "amount", prev_pair[0], unit, "确认错误", "error_year",
                    f"{period}年，{name}{cn}为{prev_pair[0]}{unit}。")
        # 5) 同比：准确 + 符号翻转（check_claim 认带符号的数）
        if prev:
            yoy = evidence_yoy(fact, prev)
            if yoy.get("status") == "ok":
                y = decimal(yoy["value"])
                yoy_s = q2(y)
                add(fact, "yoy", yoy_s, "%", "证据支持", "accurate",
                    f"{period}年，{name}{cn}同比增长{yoy_s}%。" if y >= 0
                    else f"{period}年，{name}{cn}同比下降{q2(-y)}%。")
                flipped = q2(-y)
                direction = "下降" if y >= 0 else "增长"
                add(fact, "yoy", flipped, "%", "确认错误", "error_sign",
                    f"{period}年，{name}{cn}同比{direction}{q2(abs(y))}%。")
    return claims


def score(rows: list[dict]) -> dict:
    tp = fp = fn = tn = 0
    for r in rows:
        actual_error = r["gold_expect"] == "确认错误"
        predicted_error = r["predicted"] == "确认错误"
        if actual_error and predicted_error:
            tp += 1
        elif not actual_error and predicted_error:
            fp += 1
        elif actual_error and not predicted_error:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def main() -> int:
    run = Run(AGENT_ROOT, "run_eval", {"codes": EVAL_CODES, "years": EVAL_YEARS})
    print(f"运行记录：{run.folder}", flush=True)
    facts, failures, skipped_issues = [], [], 0
    for code in EVAL_CODES:
        for year in EVAL_YEARS:
            try:
                _m, got, failed = extract_selected(AGENT_ROOT, run, code, year, False)
                facts.extend(got)
                failures.extend(failed or [])
                dirty = sum(1 for f in got if f.get("issues"))
                skipped_issues += dirty
                print(f"  抽取 {code} {year}：证据 {len(got)} 条（其中待复核 {dirty}）", flush=True)
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": code, "year": year, "error": str(exc)})
                print(f"  抽取 {code} {year} 失败：{exc}", flush=True)

    claims = build_from_facts(facts)
    clean_facts = [f for f in facts if f["metric"] in CORE and not f.get("issues")]
    results = []
    for claim in claims:
        checked = check_claim(claim, facts)
        ok = (claim["gold_expect"] == "确认错误") == (checked["status"] == "确认错误")
        results.append({
            "id": claim["id"], "company_code": claim["company_code"],
            "source_report_year": claim["source_report_year"], "metric": claim["metric"],
            "kind": claim["kind"], "error_type": claim["error_type"],
            "gold_expect": claim["gold_expect"], "predicted": checked["status"],
            "reason": checked.get("reason"), "evidence_ids": checked.get("evidence_ids"),
            "match_expect": ok,
        })

    overall = score(results)
    by_type = {et: score([r for r in results if r["error_type"] == et])
               for et in sorted({r["error_type"] for r in results})}
    wrong = [r for r in results if not r["match_expect"]]
    coverage = {
        "fact_total": len(facts), "fact_clean_core": len(clean_facts),
        "fact_with_issues": skipped_issues,
        "claim_count": len(results),
    }

    report = ["# 错误注入评测报告", "",
              f"- 评测项 **{len(results)}** 条（由 **{len(clean_facts)}** 条干净核心证据生成）。",
              f"- 抽取证据 **{len(facts)}** 条，其中带待复核字段 **{skipped_issues}** 条（未进主评测）。",
              f"- gold = 本次抽取的证据值；注错 = 在证据值上受控扰动。测的是**确定性核查器**判错能力。", "",
              "## 总体", "",
              "| 指标 | 值 |", "|---|---|",
              f"| 精确率 Precision | **{overall['precision']:.2%}**（TP {overall['tp']} / FP {overall['fp']}） |",
              f"| 召回率 Recall | **{overall['recall']:.2%}**（TP {overall['tp']} / FN {overall['fn']}） |",
              f"| F1 | **{overall['f1']:.4f}** |",
              f"| 真负例 TN | {overall['tn']} |", "",
              "## 分错误类型", "",
              "| 错误类型 | 条数 | 精确率 | 召回率 | 说明 |", "|---|---|---|---|---|"]
    meaning = {"accurate": "应放行（证据支持）", "error_value": "数值被篡改",
               "error_unit": "单位写错（数量级）", "error_year": "年份张冠李戴",
               "error_sign": "同比方向写反"}
    for et, sc in by_type.items():
        n = sum(1 for r in results if r["error_type"] == et)
        if et == "accurate":
            report.append(f"| {et} | {n} | — | 放行 {sc['tn']}/{n} | {meaning.get(et, '')} |")
        else:
            report.append(f"| {et} | {n} | {sc['precision']:.2%} | {sc['recall']:.2%} | {meaning.get(et, '')} |")
    report += ["", "## 与期望不一致的项", ""]
    if not wrong:
        report.append("（无 —— 全部与期望一致）")
    else:
        report += ["| id | 公司 | 年 | 指标 | 类型 | 期望 | 实际 | 原因 |", "|---|---|---|---|---|---|---|---|"]
        for r in wrong[:40]:
            report.append(f"| {r['id']} | {r['company_code']} | {r['source_report_year']} | {r['metric']} | "
                          f"{r['error_type']} | {r['gold_expect']} | {r['predicted']} | {r.get('reason') or '—'} |")
        if len(wrong) > 40:
            report.append(f"| … | | | | | | | 共 {len(wrong)} 条，见 eval_results.json |")
    report += ["", "## 覆盖率（抽取侧）", "",
               f"- 核心指标干净证据：{coverage['fact_clean_core']} / {coverage['fact_total']}",
               f"- 带 issues 未进主评测：{coverage['fact_with_issues']} 条（多为 unit_unknown / scope_unknown）", "",
               "## 口径限制", "",
               "- 只量结构化陈述的确定性核查；不覆盖 LLM 拆句。",
               "- 「确认错误」以外的状态（证据不足 / 口径冲突）一律算未报错。",
               "- 错单位只在金额项注入；比率（%）单位固定。", ""]

    out_dir = ROOT / "results" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "eval_report.md").write_text("\n".join(report), encoding="utf-8")
    write_json(out_dir / "eval_results.json", {
        "overall": overall, "by_type": by_type, "results": results,
        "coverage": coverage, "failures": failures,
    })
    (out_dir / "eval_claims_used.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=1), encoding="utf-8")
    run.event("eval_completed", **overall, claims=len(results))
    run.finish(status="ok" if not failures else "partial_failure",
               claims=len(results), **overall)
    print(f"精确率 {overall['precision']:.2%} · 召回率 {overall['recall']:.2%} · F1 {overall['f1']:.4f}")
    print(f"不一致 {len(wrong)} 条；报告 {out_dir / 'eval_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
