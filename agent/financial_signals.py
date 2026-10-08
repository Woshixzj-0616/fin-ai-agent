"""可复算的财务观察与原因分析框架；不将信号当作因果或造假结论。"""
from __future__ import annotations

from finance import decimal, ratio, text


def extended_signals(facts: list[dict], rows: list[dict]) -> list[dict]:
    out, groups = [], {}
    for f in facts:
        if f.get("period_year") == f.get("report_year") and f.get("adjustment") != "before":
            groups.setdefault((f["document_id"], f.get("period_start"), f.get("period_end")), {}).setdefault(f["metric"], []).append(f)
    row_index = {r["evidence_id"]: r for r in rows}
    for (doc, start, end), options in groups.items():
        m = {k: v[0] for k, v in options.items() if len(v) == 1 and not v[0].get("issues")}
        p, a, c = (m.get(k) for k in ("parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow"))
        if p and a and (p.get("scope"), p.get("currency")) == (a.get("scope"), a.get("currency")):
            pv, av = decimal(p.get("normalized_value")), decimal(a.get("normalized_value"))
            if pv is not None and av is not None:
                contribution = ratio(pv - av, abs(pv), True)
                out.append({"document_id": doc, "type": "nonrecurring_profit_share",
                            "epistemic_type": "事实", "description": "归母与扣非差额占归母利润绝对值的比例",
                            "value": contribution.get("value"), "unit": "%", "calculation": contribution,
                            "evidence_ids": [p["evidence_id"], a["evidence_id"]],
                            "interpretation": "差额仅作为非经常性损益影响线索，具体项目须查损益明细；归母为零时比例未定义。"})
                ar = row_index.get(a["evidence_id"], {}).get("yoy", {})
                out.append({"document_id": doc, "type": "adjusted_profit_growth", "epistemic_type": "事实",
                            "description": "剔除非经常性损益后的归母利润同比", "value": ar.get("value"),
                            "unit": "%", "calculation": ar, "evidence_ids": ar.get("evidence_ids", [a["evidence_id"]]),
                            "interpretation": "使用扣非归母口径，不把扣非同比和归母同比混用。"})
        if p and c:
            pr, cr = (row_index.get(f["evidence_id"], {}).get("yoy", {}) for f in (p, c))
            if pr.get("status") == cr.get("status") == "ok":
                spread = decimal(pr["value"]) - decimal(cr["value"])
                opposite = decimal(pr["value"]) * decimal(cr["value"]) < 0
                out.append({"document_id": doc, "type": "profit_cash_growth_gap", "epistemic_type": "事实",
                            "description": "归母利润与经营现金流同比差（百分点）" + ("；增长方向相反" if opposite else ""),
                            "value": text(spread), "unit": "百分点", "opposite_direction": opposite,
                            "calculation": {"formula": "profit_yoy - cashflow_yoy", "operands": {
                                "profit_yoy": pr["value"], "cashflow_yoy": cr["value"]}},
                            "evidence_ids": list(dict.fromkeys(pr["evidence_ids"] + cr["evidence_ids"])),
                            "interpretation": "归母利润和合并现金流的归属口径不同；差值为观察线索，不能等同现金转化率。"})
        revenue = m.get("revenue")
        if revenue and p:
            rr = row_index.get(revenue["evidence_id"], {}).get("yoy", {})
            pr = row_index.get(p["evidence_id"], {}).get("yoy", {})
            out.append({"document_id": doc, "type": "performance_driver_framework", "epistemic_type": "推论",
                        "description": "业绩变化原因复核框架：收入规模 → 利润率 → 费用与非经常性项目 → 回款",
                        "evidence_ids": [revenue["evidence_id"], p["evidence_id"]],
                        "observations": {"revenue_yoy": rr, "profit_yoy": pr},
                        "interpretation": "已知事实只支持收入和利润变化。毛利率、费用率及回款原因缺少明细时列为待补证据，不自动作因果归因。"})
    # 相邻年报列示的同一历史期间可能因重述或报表口径变化而不同。
    for old in facts:
        if old.get("period_year") != old.get("report_year") or old.get("adjustment") == "before" or old.get("issues"):
            continue
        candidates = [f for f in facts if f.get("company_code") == old.get("company_code")
                      and f.get("metric") == old.get("metric") and f.get("period_year") == old.get("period_year")
                      and f.get("report_year") == old.get("report_year") + 1 and f.get("adjustment") != "before"
                      and f.get("scope") == old.get("scope") and f.get("currency") == old.get("currency")
                      and f.get("period_start") == old.get("period_start") and f.get("period_end") == old.get("period_end")
                      and not f.get("issues")]
        if len(candidates) == 1:
            new = candidates[0]
            a, b = decimal(old.get("normalized_value")), decimal(new.get("normalized_value"))
            if a is not None and b is not None and a != b:
                out.append({"document_id": new["document_id"], "type": "prior_period_changed",
                            "epistemic_type": "事实", "description": "相邻报告对同一历史期间列示的值发生变化",
                            "value": text(b-a), "unit": old.get("normalized_unit", old.get("unit")),
                            "evidence_ids": [old["evidence_id"], new["evidence_id"]],
                            "calculation": {"formula": "later_report_prior_value - original_report_value",
                                            "operands": {"original": text(a), "later": text(b)}},
                            "interpretation": "可能涉及重述、会计政策或统计口径变化，须查看更正及会计政策说明；不直接认定错误。"})
    # 连续性使用独立年度，不用同一年度的重复报告凑连续次数。
    gaps = [s for s in out if s["type"] == "profit_cash_growth_gap" and s["opposite_direction"]]
    chains = {}
    by_id = {f["evidence_id"]: f for f in facts}
    for s in gaps:
        f = by_id.get(s["evidence_ids"][0], {})
        if f.get("period_kind") == "annual":
            chains.setdefault(f.get("company_code"), {}).setdefault(f.get("period_year"), []).append(s)
    for company, years in chains.items():
        years = {y: items[0] for y, items in years.items() if len(items) == 1}
        ordered = sorted(y for y in years if isinstance(y, int))
        streak = []
        for y in ordered:
            streak = streak + [y] if streak and y == streak[-1] + 1 else [y]
            if len(streak) >= 2:
                out.append({"document_id": years[y]["document_id"], "type": "persistent_profit_cash_divergence",
                            "epistemic_type": "事实", "description": f"利润与现金流增长方向连续 {len(streak)} 个年度相反",
                            "years": streak[:], "evidence_ids": list(dict.fromkeys(
                                i for year in streak for i in years[year]["evidence_ids"])),
                            "interpretation": "连续性只按已加载的相邻年度观察；需要经营现金流明细验证其原因。"})
    return out
