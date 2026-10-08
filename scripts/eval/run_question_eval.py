"""问题集评测：用真实证据跑问答 / 工具 / 检索 /（可选）LLM 草稿核查。

口径（诚实边界）
----------------
- 金标**派生自本次抽取证据**，不是独立人工盲测；程序自动打分。
- QA 只测「确定性受限问答」；不测自由对话文采。
- 工具类测 verdict / evidence_id 是否符合证据。
- 检索类测 top-k 是否命中期望页关键词（相关段落召回，不是裁决）。
- LLM 草稿类需 `LLM_API_KEY`，无凭证则 skip 并标注。

用法（仓库根）：
    python scripts/eval/run_question_eval.py
    python scripts/eval/run_question_eval.py --with-llm
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent"))

from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402
from qa import answer  # noqa: E402
from tools import compare_claim, compare_companies, compute_trend, find_evidence, search_text  # noqa: E402
from finance import decimal  # noqa: E402

CORE = ("revenue", "parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow")
CN = {
    "revenue": "营业收入", "parent_net_profit": "归母净利润",
    "adjusted_parent_net_profit": "扣非归母净利润", "operating_cash_flow": "经营现金流净额",
}


def load_facts() -> list[dict]:
    path = ROOT / "results" / "evidence.json"
    if not path.is_file():
        raise SystemExit("缺少 results/evidence.json，请先跑抽取（如 scripts/full_extract_demo.py）")
    facts = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(facts, dict):
        facts = facts.get("facts", [])
    return facts


def pick_fact(facts, code: str, metric: str, year: int):
    matches = [f for f in facts
               if f.get("company_code") == code and f.get("metric") == metric
               and f.get("period_year") == year and f.get("adjustment") != "before"
               and not (f.get("issues") or [])]
    # 优先归一化到元的完整值
    matches.sort(key=lambda f: (0 if f.get("normalized_value") else 1, f.get("evidence_id") or ""))
    return matches[0] if matches else None


def norm_amount(fact: dict) -> tuple[str, str, str] | None:
    """返回 (value_in_unit, unit, yuan_value_str)。优先 normalized_*。"""
    raw = fact.get("normalized_value") or fact.get("value")
    unit = fact.get("normalized_unit") or fact.get("unit") or "元"
    d = decimal(raw)
    if d is None:
        return None
    # 归一化单位应为元；展示用亿元
    if unit in {"%", "％"}:
        return format(d, "f"), "%", format(d, "f")
    yuan = d if unit in {"元", None} else d  # normalized 期望已是元
    if fact.get("normalized_unit") == "元":
        return format(yuan / 100000000, ".2f"), "亿元", format(yuan, "f")
    # 未归一化：按原单位传给 compare_claim
    return format(d, "f"), unit, format(d, "f")


def build_gold(facts: list[dict]) -> list[dict]:
    """从证据派生问题金标。"""
    cases: list[dict] = []
    codes = sorted({f.get("company_code") for f in facts if f.get("company_code")})[:6]
    for code in codes:
        name = next((f.get("company_name") or code for f in facts
                     if f.get("company_code") == code), code)
        for metric in CORE:
            f = pick_fact(facts, code, metric, 2024)
            if not f:
                continue
            amount = norm_amount(f)
            if not amount:
                continue
            yi_val, unit, yuan_or_native = amount
            cases.append({
                "id": f"qa_value_{code}_{metric}_2024",
                "type": "qa_value",
                "question": f"{name}2024年{CN[metric]}是多少？",
                "expect": {
                    "status": "ok",
                    "any_of": [
                        yi_val if unit == "亿元" else None,
                        str(f.get("value")),
                        str(f.get("normalized_value")),
                    ],
                    "evidence_id": f["evidence_id"],
                },
            })
            prev = pick_fact(facts, code, metric, 2023)
            if prev:
                cur_v = decimal(f.get("normalized_value") or f.get("value"))
                prev_v = decimal(prev.get("normalized_value") or prev.get("value"))
                if prev_v and cur_v is not None:
                    yoy = (cur_v - prev_v) / prev_v * 100
                    cases.append({
                        "id": f"qa_yoy_{code}_{metric}_2024",
                        "type": "qa_yoy",
                        "question": f"{name}2024年{CN[metric]}同比是多少？",
                        "expect": {"status": "ok", "yoy_approx": format(yoy, ".2f")},
                    })
            # 用证据原单位+原值做精确主张；注错用归一后金额 ±1 亿
            claim_unit = f.get("normalized_unit") or f.get("unit") or "元"
            claim_value = str(f.get("normalized_value") or f.get("value"))
            if claim_unit not in {"%", "％"}:
                try:
                    claim_value = format(decimal(claim_value), "f")
                except Exception:  # noqa: BLE001
                    pass
            wrong_val = format(float(claim_value) + 1e8, ".2f") if claim_unit == "元" else \
                format(float(claim_value) + 1, ".2f")
            scope = "consolidated" if metric in {"revenue", "operating_cash_flow"} else "parent_shareholders"
            cases.append({
                "id": f"claim_ok_{code}_{metric}_2024",
                "type": "tool_compare_claim",
                "args": {
                    "company_name_or_code": name, "metric": metric, "period_year": 2024,
                    "kind": "amount", "claimed_value": claim_value, "claimed_unit": claim_unit,
                    "operator": "eq", "scope": scope,
                },
                "expect": {"verdict": "evidence_supported"},
            })
            cases.append({
                "id": f"claim_bad_{code}_{metric}_2024",
                "type": "tool_compare_claim",
                "args": {
                    "company_name_or_code": name, "metric": metric, "period_year": 2024,
                    "kind": "amount", "claimed_value": wrong_val, "claimed_unit": claim_unit,
                    "operator": "eq", "scope": scope,
                },
                "expect": {"verdict": "confirmed_error"},
            })

    # 跨公司
    if len(codes) >= 2:
        a, b = codes[0], codes[1]
        fa, fb = pick_fact(facts, a, "revenue", 2024), pick_fact(facts, b, "revenue", 2024)
        if fa and fb:
            va = decimal(fa.get("normalized_value") or fa.get("value"))
            vb = decimal(fb.get("normalized_value") or fb.get("value"))
            if va is not None and vb is not None and va != vb:
                op = "exceed" if va > vb else "below"
                cases.append({
                    "id": f"compare_{a}_{b}_revenue_2024",
                    "type": "tool_compare_companies",
                    "args": {
                        "company_a": fa.get("company_name") or a,
                        "company_b": fb.get("company_name") or b,
                        "metric": "revenue", "period_year": 2024, "operator": op,
                    },
                    "expect": {"verdict": "evidence_supported"},
                })

    # 趋势
    for code in codes[:3]:
        if all(pick_fact(facts, code, "revenue", y) for y in (2022, 2023, 2024)):
            name = next((f.get("company_name") or code for f in facts
                         if f.get("company_code") == code), code)
            cases.append({
                "id": f"trend_{code}_revenue",
                "type": "tool_compute_trend",
                "args": {"company_name_or_code": name, "metric": "revenue",
                         "start_year": 2022, "end_year": 2024},
                "expect": {"status": "ok"},
            })

    # 拒答 / 空证据
    cases.append({
        "id": "qa_other_company_reject",
        "type": "qa_reject",
        "question": "中国石油2024年营业收入是多少？",
        "expect": {"status_not": "ok"},
    })
    cases.append({
        "id": "qa_unknown_metric",
        "type": "qa_insufficient",
        "question": "贵州茅台2019年商誉是多少？",
        "expect": {"status_in": ["insufficient_evidence", "out_of_scope"]},
    })

    # 检索
    cases.append({
        "id": "retrieval_revenue_yoy",
        "type": "retrieval",
        "query": "营业收入同比",
        "expect": {"min_hits": 1},
    })
    cases.append({
        "id": "retrieval_margin",
        "type": "retrieval",
        "query": "毛利率",
        "expect": {"min_hits": 1},
    })
    return cases


def run_case(case: dict, facts: list[dict], document_texts: dict | None) -> dict:
    t = case["type"]
    try:
        if t in {"qa_value", "qa_yoy", "qa_insufficient"}:
            out = answer(case["question"], facts, use_llm=False)
            exp = case["expect"]
            if exp.get("status_in"):
                ok = out.get("status") in exp["status_in"]
                return {"ok": ok, "detail": {"status": out.get("status")}}
            ok = out.get("status") == exp.get("status")
            detail = {"status": out.get("status")}
            if exp.get("status") == "ok":
                ans = out.get("answer") or ""
                if exp.get("any_of"):
                    ok = ok and any(str(x) in ans for x in exp["any_of"] if x)
                    detail["answer"] = ans[:80]
                if exp.get("yoy_approx"):
                    target = exp["yoy_approx"]
                    # 允许四舍五入到 1 位小数
                    variants = {target, target.rstrip("0").rstrip("."),
                                format(float(target), ".1f"), format(float(target), ".0f")}
                    ok = ok and any(v in ans for v in variants if v)
                    detail["answer"] = ans[:80]
                ok = ok and bool(out.get("evidence_ids"))
            return {"ok": ok, "detail": detail}

        if t == "qa_reject":
            out = answer(case["question"], facts, use_llm=False)
            ans = out.get("answer") or ""
            status = out.get("status")
            # 问题点名的公司不在本次材料 → 不应给出另一家公司的确定数值
            ok = status != "ok" or any(k in ans for k in
                                       ("无法对应", "不足", "不同", "拒绝", "不是", "未加载", "本次"))
            return {"ok": ok, "detail": {"status": status, "answer": ans[:60]}}

        if t == "tool_compare_claim":
            out = compare_claim(facts, draft="", **case["args"])
            ok = out.get("verdict") == case["expect"].get("verdict")
            return {"ok": ok, "detail": {"verdict": out.get("verdict"),
                                         "evidence_ids": out.get("evidence_ids")}}

        if t == "tool_compare_companies":
            out = compare_companies(facts, draft="", **case["args"])
            ok = out.get("verdict") == case["expect"].get("verdict")
            return {"ok": ok, "detail": {"verdict": out.get("verdict")}}

        if t == "tool_compute_trend":
            out = compute_trend(facts, draft="", **case["args"])
            ok = out.get("status") == case["expect"].get("status")
            return {"ok": ok, "detail": {"status": out.get("status"),
                                         "years": out.get("years")}}

        if t == "retrieval":
            if not document_texts:
                return {"ok": None, "detail": {"skip": "no document_texts"}}
            out = search_text(facts, document_texts,
                              company_name_or_code="贵州茅台", source_report_year=2024,
                              query=case["query"])
            hits = out.get("hits") or []
            ok = out.get("status") == "ok" and len(hits) >= case["expect"].get("min_hits", 1)
            return {"ok": ok, "detail": {"status": out.get("status"), "hits": len(hits),
                                         "channel": out.get("channel")}}

        return {"ok": None, "detail": {"skip": f"unknown type {t}"}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": {"error": f"{type(exc).__name__}: {exc}"}}


def run_llm_draft_block(facts: list[dict], run: Run) -> dict | None:
    """真实 LLM 草稿核查：3 正确 + 1 故意错。"""
    import os
    if not os.environ.get("LLM_API_KEY"):
        return {"skipped": True, "reason": "未配置 LLM_API_KEY"}
    from llm_check import LLMClient, check_text
    f = pick_fact(facts, "600519", "revenue", 2024) or pick_fact(facts, "600519", "revenue", 2025)
    if not f:
        return {"skipped": True, "reason": "缺少茅台证据"}
    name = f.get("company_name") or "贵州茅台"
    year = f.get("period_year") or 2024
    amount = norm_amount(f)
    val, unit, _ = amount if amount else ("", "亿元", "")
    fp = pick_fact(facts, "600519", "parent_net_profit", year)
    fe = pick_fact(facts, "600519", "basic_eps", year)
    parts = [f"{name}{year}年实现营业收入约{val}{unit}。"]
    if fp:
        pm = norm_amount(fp)
        if pm:
            parts.append(f"归属于上市公司股东的净利润为{pm[0]}{pm[1]}。")
    if fe:
        parts.append(f"基本每股收益为{fe.get('value')}元/股。")
    parts.append(f"其中营业收入为{float(val) + 1:.2f}亿元，这一数字需要核查。")
    draft_path = run.folder / "question_eval_draft.txt"
    draft_path.write_text("".join(parts), encoding="utf-8")
    client = LLMClient.from_environment()
    bundle = check_text(draft_path, facts, run, client, use_loop=True)
    counts = bundle.get("counts") or {}
    ok = (counts.get("证据支持", 0) >= 2 and counts.get("确认错误", 0) >= 1)
    return {
        "skipped": False,
        "mode": bundle.get("mode"),
        "counts": counts,
        "tools_used": len(bundle.get("tools_used") or []),
        "ok": ok,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-llm", action="store_true", help="跑真实 LLM 草稿核查")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试）")
    args = parser.parse_args()

    facts = load_facts()
    cases = build_gold(facts)
    if args.limit:
        cases = cases[: args.limit]

    # 可选：从真实 PDF 前几页建检索索引
    document_texts = None
    try:
        import pymupdf
        from materials import load_materials
        mats = [m for m in load_materials(ROOT)
                if m.get("company_code") == "600519" and m.get("report_year") == 2024]
        if mats:
            with pymupdf.open(ROOT / mats[0]["local_file"]) as doc:
                document_texts = {"600519_2024": [p.get_text() for p in doc]}
    except Exception as exc:  # noqa: BLE001
        print(f"检索文档未注入：{exc}", flush=True)

    run = Run(ROOT, "question-eval", {"cases": len(cases), "with_llm": args.with_llm})
    rows = []
    for i, case in enumerate(cases, 1):
        result = run_case(case, facts, document_texts)
        rows.append({**case, **result})
        print(f"[{i:02d}/{len(cases)}] {case['id']:40s} "
              f"{'PASS' if result['ok'] else ('SKIP' if result['ok'] is None else 'FAIL')} "
              f"{json.dumps(result.get('detail') or {}, ensure_ascii=False)[:70]}", flush=True)

    llm_block = None
    if args.with_llm:
        print("== LLM 草稿核查 ==", flush=True)
        llm_block = run_llm_draft_block(facts, run)
        print(json.dumps(llm_block, ensure_ascii=False), flush=True)

    scored = [r for r in rows if r["ok"] is not None]
    passed = sum(1 for r in scored if r["ok"])
    failed = [r["id"] for r in scored if not r["ok"]]
    by_type: dict[str, dict] = {}
    for r in scored:
        bucket = by_type.setdefault(r["type"], {"pass": 0, "fail": 0})
        bucket["pass" if r["ok"] else "fail"] += 1

    report = {
        "schema_version": 1,
        "run_id": run.id,
        "total_cases": len(cases),
        "scored": len(scored),
        "passed": passed,
        "failed": len(scored) - passed,
        "skipped": len(rows) - len(scored),
        "pass_rate": round(passed / len(scored), 4) if scored else None,
        "by_type": by_type,
        "failed_ids": failed,
        "llm_draft": llm_block,
        "note": "金标派生自本次抽取证据，非独立人工盲测；见脚本 docstring 口径。",
    }
    write_json(run.output("question_eval.json"), {**report, "rows": rows})
    # 同步到 Pages 静态站，供「评测报告」页展示
    site_data = ROOT / "docs" / "data"
    if site_data.is_dir():
        write_json(site_data / "question_eval.json", report)
    md = ["# 问题集评测", "",
          f"- 用例 {len(cases)}；计分 {len(scored)}；通过 **{passed}**；失败 {report['failed']}；跳过 {report['skipped']}",
          f"- 通过率 **{report['pass_rate']}**",
          f"- 口径：{report['note']}", "",
          "## 分类型", "",
          "| 类型 | 通过 | 失败 |",
          "|---|---:|---:|"]
    for t, b in sorted(by_type.items()):
        md.append(f"| {t} | {b['pass']} | {b['fail']} |")
    if llm_block:
        md += ["", "## LLM 草稿核查", "",
               f"- skipped: {llm_block.get('skipped')}",
               f"- mode: `{llm_block.get('mode')}`",
               f"- counts: `{json.dumps(llm_block.get('counts') or {}, ensure_ascii=False)}`",
               f"- ok: {llm_block.get('ok')}"]
    if failed:
        md += ["", "## 失败用例", ""]
        md += [f"- `{i}`" for i in failed]
    run.output("question_eval.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    run.finish(status="ok" if not failed else "partial",
               scored=len(scored), passed=passed, failed=report["failed"],
               pass_rate=report["pass_rate"], llm=bool(llm_block and not llm_block.get("skipped")))
    print(f"\n== 汇总 == 通过 {passed}/{len(scored)} = {report['pass_rate']}  报告：{run.folder / 'question_eval.md'}",
          flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
