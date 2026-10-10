"""给语义层用的只读工具：证据与算术的唯一出口。

约定
----
- 全部纯函数，不写库、不发网络；
- **带 evidence_id 的返回才叫证据**；
- `compare_claim` / `compare_companies` 是能产生「对/错」的入口（经本地 Decimal）。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext

from finance import (check_claim, compare_amount, convert, decimal, evidence_issues,
                     evidence_yoy, select_previous, text)
from llm_check import COMPANIES, METRIC_NOTE, METRICS, UNITS, companies_from_facts, resolve_company

TOOL_NAMES = (
    "list_catalog",
    "find_evidence",
    "compute_yoy",
    "compare_claim",
    "compare_companies",
    "compute_trend",
    "search_text",
)


def tool_specs() -> list[dict]:
    """OpenAI 兼容的 tools 列表；JSON 多步协议里也用同一套 name/parameters。"""
    return [
        {
            "name": "list_catalog",
            "description": "列出本次已加载公司、指标键名与别名、单位与运算符枚举。只读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["companies", "metrics", "units", "operators", "verdicts"]},
                },
                "required": ["kind"],
            },
        },
        {
            "name": "find_evidence",
            "description": "按公司、指标、年度取唯一年报证据（页码/原值/bbox）。0 条或多条时如实返回，禁止自行选一条。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string", "description": "目录键，如 revenue"},
                    "period_year": {"type": "integer"},
                    "source_report_year": {"type": "integer",
                                           "description": "缺省与 period_year 相同"},
                },
                "required": ["company_name_or_code", "metric", "period_year"],
            },
        },
        {
            "name": "compute_yoy",
            "description": "算年度同比（本地 Decimal）。返回计算式与参与的 evidence_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string"},
                    "year": {"type": "integer"},
                },
                "required": ["company_name_or_code", "metric", "year"],
            },
        },
        {
            "name": "compare_claim",
            "description": "把草稿数值主张与证据比对。**唯一**能产生 证据支持/确认错误 的工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string"},
                    "period_year": {"type": "integer"},
                    "kind": {"type": "string", "enum": ["amount", "yoy"]},
                    "claimed_value": {"type": "string"},
                    "claimed_unit": {"type": "string"},
                    "operator": {
                        "type": "string",
                        "enum": ["eq", "approx", "exceed", "at_least", "at_most", "below"],
                    },
                    "tolerance_pct": {"type": ["number", "null"],
                                      "description": "仅 approx；默认 2，上限 5"},
                    "direction": {"type": "string", "enum": ["up", "down", "unknown"]},
                    "scope": {"type": "string",
                              "enum": ["consolidated", "parent_shareholders",
                                       "parent_company", "unknown"]},
                },
                "required": ["company_name_or_code", "metric", "period_year", "kind",
                             "claimed_value", "claimed_unit", "operator"],
            },
        },
        {
            "name": "compare_companies",
            "description": "跨公司比较两家同指标同年数值（本地 Decimal）。可确定性裁决「茅台营收高于五粮液」类主张。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_a": {"type": "string", "description": "陈述主语公司（「A 高于 B」中的 A）"},
                    "company_b": {"type": "string", "description": "被比较公司"},
                    "metric": {"type": "string"},
                    "period_year": {"type": "integer"},
                    "operator": {"type": "string",
                                 "enum": ["exceed", "at_least", "at_most", "below", "eq"],
                                 "description": "exceed=A>B at_least=A>=B at_most=A<=B below=A<B eq=A≈B"},
                },
                "required": ["company_a", "company_b", "metric", "period_year", "operator"],
            },
        },
        {
            "name": "compute_trend",
            "description": "多年趋势：逐年同比 + CAGR + 是否连续增长/下降。覆盖「连续三年增长」「累计增长」类主张。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string"},
                    "start_year": {"type": "integer"},
                    "end_year": {"type": "integer"},
                },
                "required": ["company_name_or_code", "metric", "start_year", "end_year"],
            },
        },
        {
            "name": "search_text",
            "description": "在年报全文做语义检索（BM25/向量），返回相关段落。命中**不能**单独作为数值裁决依据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "source_report_year": {"type": "integer"},
                    "query": {"type": "string", "description": "检索问题或短语，≤40字"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 8，上限 20"},
                },
                "required": ["company_name_or_code", "source_report_year", "query"],
            },
        },
    ]


def _err(message: str, **extra) -> dict:
    return {"status": "error", "error": message, **extra}


def list_catalog(kind: str, facts: list[dict] | None = None) -> dict:
    if kind == "companies":
        loaded = companies_from_facts(facts) or COMPANIES
        return {"status": "ok", "kind": kind, "companies": loaded,
                "note": "来自本次已加载材料；新公司随材料自动入册，不靠固定白名单"}
    if kind == "metrics":
        return {"status": "ok", "kind": kind,
                "metrics": {k: {"aliases": v, "note": METRIC_NOTE.get(k)}
                            for k, v in METRICS.items()}}
    if kind == "units":
        return {"status": "ok", "kind": kind, "units": list(UNITS)}
    if kind == "operators":
        return {"status": "ok", "kind": kind,
                "operators": ["eq", "approx", "exceed", "at_least", "at_most", "below"],
                "default_tolerance_pct": 2, "max_tolerance_pct": 5}
    if kind == "verdicts":
        return {"status": "ok", "kind": kind,
                "deterministic": ["证据支持", "确认错误"],
                "model_track": ["模型判断"],
                "review": ["口径冲突／需人工复核", "证据不足"]}
    return _err(f"未知 kind：{kind}")


def _fact_quality(f: dict) -> tuple:
    """择优排序：问题少 > 有 bbox > 页码靠前（摘要表） > id 稳定。"""
    issues = evidence_issues(f)
    hard = sum(1 for i in issues if i in {
        "conflicting_extraction_values", "unit_unknown", "scope_unknown", "value_conflict"})
    return (hard, len(issues), 0 if f.get("value_bbox") else 1,
            f.get("page") if isinstance(f.get("page"), int) else 10**9,
            str(f.get("evidence_id") or ""))


def _fact_identity(f: dict) -> tuple:
    """同一数值/口径/调整列视为可合并重复（words+grid 同值双记等）。"""
    return (f.get("normalized_value") or f.get("value"),
            f.get("normalized_unit") or f.get("unit"),
            f.get("scope"), f.get("adjustment"), f.get("document_id"), f.get("source_sha256"),
            f.get("period_start"), f.get("period_end"))


def _pick_fact(matches: list[dict]) -> tuple[dict | None, list[dict]]:
    """多条候选时择优；值冲突才判不可自动择优。

    返回 (best_or_None, rest)。best 为 None 表示仍有冲突，应 ambiguous。
    """
    if not matches:
        return None, []
    if len(matches) > 1 and any(f.get("auto_usable") is False or f.get("review_reasons")
                              or "second_path_unresolved" in evidence_issues(f) for f in matches):
        return None, matches  # 同值重复不能洗掉明确复核限制。
    if len(matches) == 1:
        return matches[0], []
    # 先按身份分组：同值同口径只留质量问题最少的一条
    groups: dict[tuple, list[dict]] = {}
    for f in matches:
        groups.setdefault(_fact_identity(f), []).append(f)
    collapsed = [min(g, key=_fact_quality) for g in groups.values()]
    if len(collapsed) == 1:
        return collapsed[0], []
    # 多组：若仅一组无硬问题，选它；否则冲突
    clean = [f for f in collapsed
             if not any(i in (f.get("issues") or [])
                        for i in ("conflicting_extraction_values", "value_conflict",
                                  "unit_unknown", "scope_unknown"))]
    if len(clean) == 1:
        return clean[0], collapsed
    if not clean:
        return None, collapsed
    return None, collapsed


def find_evidence(facts: list[dict], *, company_name_or_code: str, metric: str,
                  period_year: int, source_report_year: int | None = None,
                  draft: str = "") -> dict:
    code = resolve_company(company_name_or_code, company_name_or_code, draft or company_name_or_code, facts)
    if not code:
        return _err("公司无法对应到本次已加载材料", company=company_name_or_code)
    if metric not in METRICS:
        return _err("指标不在目录", metric=metric)
    report_year = int(source_report_year or period_year)
    matches = [f for f in facts
               if f.get("company_code") == code and f.get("metric") == metric
               and f.get("period_year") == int(period_year)
               and f.get("report_year") == report_year
               and f.get("adjustment") != "before"]
    if matches:
        # 本报告年候选若全带 issues，放宽到其他报告年（如下年比较列的干净重述值）
        probe, _ = _pick_fact(matches)
        if probe is not None and (probe.get("issues") or []):
            wider = [f for f in facts
                     if f.get("company_code") == code and f.get("metric") == metric
                     and f.get("period_year") == int(period_year)
                     and f.get("adjustment") != "before"]
            wide_best, _ = _pick_fact(wider)
            if wide_best is not None and not (wide_best.get("issues") or []):
                matches = wider
    else:
        matches = [f for f in facts
                   if f.get("company_code") == code and f.get("metric") == metric
                   and f.get("period_year") == int(period_year)
                   and f.get("adjustment") != "before"]
    if not matches:
        return {"status": "empty", "count": 0, "items": [],
                "hint": "无该组合证据；不要猜测数值"}
    fact, rest = _pick_fact(matches)
    if fact is None:
        return {"status": "ambiguous", "count": len(matches),
                "evidence_ids": [f["evidence_id"] for f in rest or matches],
                "hint": "多条证据数值或口径冲突，不能静默选一条；请人工裁定"}
    extra = []
    if len(matches) > 1:
        extra = [f["evidence_id"] for f in matches if f["evidence_id"] != fact["evidence_id"]]
    return {"status": "ok", "count": 1, "items": [{
        "evidence_id": fact["evidence_id"],
        "company_code": fact["company_code"],
        "metric": fact["metric"],
        "period_year": fact["period_year"],
        "report_year": fact["report_year"],
        "value": fact.get("value"), "unit": fact.get("unit"),
        "normalized_value": fact.get("normalized_value"),
        "page": fact.get("page"), "value_bbox": fact.get("value_bbox"),
        "scope": fact.get("scope"), "adjustment": fact.get("adjustment"),
        "issues": evidence_issues(fact),
        "auto_usable": fact.get("auto_usable"), "review_reasons": fact.get("review_reasons", []),
        "second_path_check": fact.get("second_path_check"),
    }],
        **({"duplicate_evidence_ids": extra, "selection_note": "同值/同口径多条，已按质量问题择优"} if extra else {}),
    }


def compute_yoy(facts: list[dict], *, company_name_or_code: str, metric: str, year: int,
                draft: str = "") -> dict:
    located = find_evidence(facts, company_name_or_code=company_name_or_code, metric=metric,
                            period_year=int(year), draft=draft)
    if located.get("status") != "ok":
        return located
    fact = next(f for f in facts if f["evidence_id"] == located["items"][0]["evidence_id"])
    previous = select_previous(facts, fact)
    computation = evidence_yoy(fact, previous)
    return {"status": "ok" if computation.get("status") == "ok" else computation.get("status"),
            "yoy": computation, "current_evidence_id": fact["evidence_id"],
            "previous_evidence_id": (previous or {}).get("evidence_id")}


def compare_claim(facts: list[dict], *, company_name_or_code: str, metric: str,
                  period_year: int, kind: str, claimed_value: str, claimed_unit: str,
                  operator: str, tolerance_pct=None, direction: str = "unknown",
                  scope: str = "consolidated", source_report_year: int | None = None,
                  draft: str = "") -> dict:
    """唯一裁决入口：内部走 finance.check_claim，verdict 不由模型改写。"""
    located = find_evidence(facts, company_name_or_code=company_name_or_code, metric=metric,
                            period_year=int(period_year),
                            source_report_year=source_report_year, draft=draft)
    if located.get("status") != "ok":
        return {"verdict": "needs_review", "reason_code": "missing_evidence",
                "evidence_ids": [], "detail": located}
    code = located["items"][0]["company_code"]
    # check_claim 按 report_year 取唯一证据；采用 find_evidence 择优后的 report_year
    # （例如 2024 年报带 issues、2025 比较列同值干净时，避免锁回脏证据）
    chosen_report_year = located["items"][0].get("report_year") or int(source_report_year or period_year)
    value = claimed_value
    if kind == "yoy" and direction == "down" and decimal(claimed_value) and decimal(claimed_value) > 0:
        value = "-" + str(decimal(claimed_value))
    claim = {
        "id": "tool", "sentence": "", "company_code": code,
        "period_year": int(period_year),
        "source_report_year": int(source_report_year or chosen_report_year),
        "metric": metric, "kind": kind,
        "value": value, "unit": claimed_unit,
        "currency": "CNY", "scope": scope, "period_kind": "annual",
        "operator": operator, "tolerance_pct": tolerance_pct,
    }
    checked = check_claim(claim, facts)
    status = checked.get("status")
    verdict = {
        "证据支持": "evidence_supported",
        "确认错误": "confirmed_error",
    }.get(status, "needs_review")
    return {
        "verdict": verdict, "status": status,
        "reason_code": "deterministic_check" if verdict != "needs_review" else checked.get("reason"),
        "reason": checked.get("reason"),
        "evidence_ids": checked.get("evidence_ids") or [],
        "calculation": checked.get("calculation"),
        "expected": (checked.get("calculation") or {}).get("value")
        if status == "确认错误" else None,
    }


def _one_fact(facts: list[dict], code: str, metric: str, year: int) -> dict | None:
    """取该公司该指标该年的证据；同值多条择优，冲突则 None。"""
    matches = [f for f in facts
               if f.get("company_code") == code and f.get("metric") == metric
               and f.get("period_year") == int(year) and f.get("adjustment") != "before"]
    best, _ = _pick_fact(matches)
    return best


def _norm_value(fact: dict) -> Decimal | None:
    """归一化到元的数值；非金额单位直接取 value。"""
    from finance import NON_AMOUNT_UNITS
    v = decimal(fact.get("value"))
    if v is None:
        return None
    unit = fact.get("unit")
    if unit in NON_AMOUNT_UNITS or unit is None:
        return v
    try:
        return convert(v, unit, "元")
    except (ValueError, ArithmeticError):
        return v


def compare_companies(facts: list[dict], *, company_a: str, company_b: str,
                      metric: str, period_year: int, operator: str,
                      draft: str = "") -> dict:
    """跨公司同指标比较：A operator B，返回确定性裁决 + 双侧 evidence_id。"""
    ops = {"exceed": (">", lambda a, b: a > b),
           "at_least": (">=", lambda a, b: a >= b),
           "at_most": ("<=", lambda a, b: a <= b),
           "below": ("<", lambda a, b: a < b),
           "eq": ("≈", lambda a, b: abs(a - b) <= abs(b) * Decimal("0.02") if b else a == b)}
    if operator not in ops:
        return _err(f"不支持的比较运算符：{operator}")
    if metric not in METRICS:
        return _err("指标不在目录", metric=metric)
    code_a = resolve_company(company_a, company_a, draft or company_a, facts)
    code_b = resolve_company(company_b, company_b, draft or company_b, facts)
    if not code_a or not code_b:
        return {"verdict": "needs_review", "reason_code": "unresolved_company",
                "reason": "公司无法对应到本次已加载材料", "evidence_ids": []}
    if code_a == code_b:
        return _err("比较双方是同一公司")
    fact_a = _one_fact(facts, code_a, metric, int(period_year))
    fact_b = _one_fact(facts, code_b, metric, int(period_year))
    if not fact_a or not fact_b:
        missing = code_a if not fact_a else code_b
        return {"verdict": "needs_review", "reason_code": "missing_evidence",
                "reason": f"缺少 {missing} {period_year} 年「{METRICS[metric][0]}」的唯一年报证据",
                "evidence_ids": [f["evidence_id"] for f in (fact_a, fact_b) if f]}
    va, vb = _norm_value(fact_a), _norm_value(fact_b)
    if va is None or vb is None:
        return {"verdict": "needs_review", "reason_code": "value_missing",
                "reason": "证据数值缺失", "evidence_ids": [fact_a["evidence_id"], fact_b["evidence_id"]]}
    sym, fn = ops[operator]
    held = fn(va, vb)
    verdict = "evidence_supported" if held else "confirmed_error"
    name_a = fact_a.get("company_name") or code_a
    name_b = fact_b.get("company_name") or code_b
    name_m = METRICS[metric][0]
    return {
        "verdict": verdict, "operator": operator,
        "reason_code": "cross_company_comparison",
        "reason": f"{name_a} {period_year}年{name_m}={text(va)}，{name_b}={text(vb)}，"
                  f"判断 {name_a}{sym}{name_b} {'成立' if held else '不成立'}",
        "evidence_ids": [fact_a["evidence_id"], fact_b["evidence_id"]],
        "values": [{"company_code": code_a, "value": text(va), "evidence_id": fact_a["evidence_id"]},
                   {"company_code": code_b, "value": text(vb), "evidence_id": fact_b["evidence_id"]}],
    }


def compute_trend(facts: list[dict], *, company_name_or_code: str, metric: str,
                  start_year: int, end_year: int, draft: str = "") -> dict:
    """多年趋势：逐年同比 + CAGR + 单调性。覆盖「连续 N 年增长」「累计增长」类主张。"""
    if metric not in METRICS:
        return _err("指标不在目录", metric=metric)
    code = resolve_company(company_name_or_code, company_name_or_code, draft or company_name_or_code, facts)
    if not code:
        return {"status": "error", "error": "公司无法对应到本次已加载材料"}
    y0, y1 = int(start_year), int(end_year)
    if y1 - y0 < 2:
        return _err("趋势至少要 3 个年度（start_year 到 end_year 跨 2 年以上）")
    series = []
    for y in range(y0, y1 + 1):
        f = _one_fact(facts, code, metric, y)
        if not f:
            return {"status": "error", "error": f"缺少 {y} 年「{METRICS[metric][0]}」的唯一年报证据，无法算趋势"}
        v = _norm_value(f)
        if v is None:
            return {"status": "error", "error": f"{y} 年数值缺失"}
        series.append({"year": y, "value": text(v), "evidence_id": f["evidence_id"]})
    # 逐年同比
    yoy_rows = []
    for i in range(1, len(series)):
        prev, cur = Decimal(series[i - 1]["value"]), Decimal(series[i]["value"])
        if prev == 0:
            yoy_rows.append({"year": series[i]["year"], "status": "zero_base"})
            continue
        with localcontext() as ctx:
            ctx.prec = 40
            pct = (cur - prev) / prev * 100
        yoy_rows.append({"year": series[i]["year"],
                         "yoy_pct": text(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                         "evidence_ids": [series[i - 1]["evidence_id"], series[i]["evidence_id"]]})
    # CAGR
    first_v, last_v = Decimal(series[0]["value"]), Decimal(series[-1]["value"])
    n = len(series) - 1
    cagr = None
    if first_v > 0 and last_v > 0:
        with localcontext() as ctx:
            ctx.prec = 40
            cagr = ((last_v / first_v) ** (Decimal(1) / n) - 1) * 100
    total_pct = None
    if first_v != 0:
        with localcontext() as ctx:
            ctx.prec = 40
            total_pct = (last_v - first_v) / first_v * 100
    # 单调性
    values = [Decimal(s["value"]) for s in series]
    monotonic_up = all(values[i] < values[i + 1] for i in range(len(values) - 1))
    monotonic_down = all(values[i] > values[i + 1] for i in range(len(values) - 1))
    return {
        "status": "ok",
        "series": series,
        "yoy": yoy_rows,
        "cagr_pct": text(cagr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if cagr is not None else None,
        "total_change_pct": text(total_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if total_pct is not None else None,
        "monotonic_up": monotonic_up,
        "monotonic_down": monotonic_down,
        "years": [s["year"] for s in series],
        "evidence_ids": [s["evidence_id"] for s in series],
    }


def search_text(facts: list[dict], document_texts: dict | None = None, *,
                company_name_or_code: str, source_report_year: int, query: str,
                top_k: int = 8) -> dict:
    """文档正文语义检索（BM25 / 可选 embedding）。

    document_texts 由调用方注入（code_year -> [page_text]）。
    命中只是相关段落，**不能单独作为数值裁决依据**。
    """
    if not query or len(query) > 40:
        return _err("query 需为 1–40 字")
    if not document_texts:
        return {"status": "empty", "hits": [], "hint": "未注入文档正文，无法全文检索"}
    code = resolve_company(company_name_or_code, company_name_or_code, company_name_or_code, facts)
    if not code:
        return _err("公司无法对应到本次已加载材料", company=company_name_or_code)
    key = f"{code}_{int(source_report_year)}"
    pages = document_texts.get(key) or []
    if not pages:
        return {"status": "empty", "hits": [], "hint": f"未找到 {key} 的正文页文本"}

    from retrieval import build_index
    index = build_index(key, pages)
    hits = index.search(query, k=max(1, min(int(top_k), 20)))

    # 保留精确子串命中，标记 match=exact 便于快速定位
    for i, t in enumerate(pages):
        pos = t.find(query) if isinstance(t, str) else -1
        if pos >= 0:
            snippet = t[max(0, pos - 30): pos + len(query) + 30]
            if not any(h.get("page") == i + 1 and h.get("snippet") == snippet for h in hits):
                hits.insert(0, {"score": 1.0, "page": i + 1, "snippet": snippet,
                                "match": "exact", "channel": "substring"})
    return {"status": "ok" if hits else "empty",
            "hits": hits[: max(1, min(int(top_k), 20))],
            "channel": "embedding" if hits and hits[0].get("channel") == "embedding" else "bm25",
            "note": "search_text 命中为相关段落检索，不能单独作为数值裁决依据；数值必须走 compare_claim"}


def dispatch(name: str, facts: list[dict], arguments: dict,
             document_texts: dict | None = None, draft: str = "") -> dict:
    """JSON 多步协议的执行器。未知工具 / 参数错误一律 error，不抛栈。"""
    try:
        if name == "list_catalog":
            return list_catalog(arguments.get("kind", "metrics"), facts)
        if name == "find_evidence":
            return find_evidence(facts, draft=draft, **arguments)
        if name == "compute_yoy":
            return compute_yoy(facts, draft=draft, **arguments)
        if name == "compare_claim":
            return compare_claim(facts, draft=draft, **arguments)
        if name == "compare_companies":
            return compare_companies(facts, draft=draft, **arguments)
        if name == "compute_trend":
            return compute_trend(facts, draft=draft, **arguments)
        if name == "search_text":
            return search_text(facts, document_texts, **arguments)
        return _err(f"未知工具：{name}")
    except TypeError as exc:
        return _err(f"工具参数不合法：{exc}", tool=name, arguments=arguments)
    except (ValueError, ArithmeticError, KeyError) as exc:
        return _err(f"工具执行失败：{exc}", tool=name)
