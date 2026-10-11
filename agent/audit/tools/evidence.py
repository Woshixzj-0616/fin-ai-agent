"""证据目录与证据择优。"""
from __future__ import annotations

from audit.grounding import resolve_company
from audit.schema import COMPANIES, METRIC_NOTE, METRICS, UNITS, companies_from_facts
from finance import evidence_issues


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
    groups: dict[tuple, list[dict]] = {}
    for f in matches:
        groups.setdefault(_fact_identity(f), []).append(f)
    collapsed = [min(g, key=_fact_quality) for g in groups.values()]
    if len(collapsed) == 1:
        return collapsed[0], []
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
        probe, _ = _pick_fact(matches)
        if probe is not None and evidence_issues(probe):
            wider = [f for f in facts
                     if f.get("company_code") == code and f.get("metric") == metric
                     and f.get("period_year") == int(period_year)
                     and f.get("adjustment") != "before"]
            wide_best, _ = _pick_fact(wider)
            if wide_best is not None and not evidence_issues(wide_best):
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
