"""给语义层用的只读工具：证据与算术的唯一出口。

约定
----
- 全部纯函数，不写库、不发网络；
- **带 evidence_id 的返回才叫证据**；
- `compare_claim` 是唯一能产生「对/错」的入口（经 finance.check_claim）。
"""
from __future__ import annotations

from finance import check_claim, compare_amount, decimal, evidence_yoy, select_previous, text
from llm_check import COMPANIES, METRIC_NOTE, METRICS, UNITS, resolve_company

TOOL_NAMES = (
    "list_catalog",
    "find_evidence",
    "compute_yoy",
    "compare_claim",
    "search_text",
)


def tool_specs() -> list[dict]:
    """OpenAI 兼容的 tools 列表；JSON 多步协议里也用同一套 name/parameters。"""
    return [
        {
            "name": "list_catalog",
            "description": "列出公司白名单、指标键名与别名、单位与运算符枚举。只读。",
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
            "name": "search_text",
            "description": "在年报全文搜短语（≤40字），用于理解。命中结果**不能**单独作为数值裁决依据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "source_report_year": {"type": "integer"},
                    "query": {"type": "string"},
                },
                "required": ["company_name_or_code", "source_report_year", "query"],
            },
        },
    ]


def _err(message: str, **extra) -> dict:
    return {"status": "error", "error": message, **extra}


def list_catalog(kind: str) -> dict:
    if kind == "companies":
        return {"status": "ok", "kind": kind,
                "companies": {code: names for code, names in COMPANIES.items()}}
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


def find_evidence(facts: list[dict], *, company_name_or_code: str, metric: str,
                  period_year: int, source_report_year: int | None = None,
                  draft: str = "") -> dict:
    code = resolve_company(company_name_or_code, company_name_or_code, draft) \
        or (company_name_or_code if company_name_or_code in COMPANIES else None)
    if not code:
        return _err("公司无法映射到白名单", company=company_name_or_code)
    if metric not in METRICS:
        return _err("指标不在目录", metric=metric)
    report_year = int(source_report_year or period_year)
    matches = [f for f in facts
               if f.get("company_code") == code and f.get("metric") == metric
               and f.get("period_year") == int(period_year)
               and f.get("report_year") == report_year
               and f.get("adjustment") != "before"]
    if not matches:
        return {"status": "empty", "count": 0, "items": [],
                "hint": "无该组合证据；不要猜测数值"}
    if len(matches) > 1:
        return {"status": "ambiguous", "count": len(matches),
                "evidence_ids": [f["evidence_id"] for f in matches],
                "hint": "多条证据，请收窄条件或人工裁定"}
    fact = matches[0]
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
        "issues": fact.get("issues") or [],
    }]}


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
    code = resolve_company(company_name_or_code, company_name_or_code, draft) \
        or company_name_or_code
    value = claimed_value
    if kind == "yoy" and direction == "down" and decimal(claimed_value) and decimal(claimed_value) > 0:
        value = "-" + str(decimal(claimed_value))
    claim = {
        "id": "tool", "sentence": "", "company_code": code,
        "period_year": int(period_year),
        "source_report_year": int(source_report_year or period_year),
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


def search_text(facts: list[dict], document_texts: dict | None = None, *,
                company_name_or_code: str, source_report_year: int, query: str) -> dict:
    """文档正文检索；document_texts 由调用方注入（code_year -> [page_text]）。"""
    if not query or len(query) > 40:
        return _err("query 需为 1–40 字")
    if not document_texts:
        return {"status": "empty", "hits": [], "hint": "未注入文档正文，无法全文检索"}
    code = company_name_or_code if company_name_or_code in COMPANIES else (
        resolve_company(company_name_or_code, query, query) or None)
    if not code:
        return _err("公司无法映射", company=company_name_or_code)
    key = f"{code}_{int(source_report_year)}"
    pages = document_texts.get(key) or []
    hits = [{"page": i + 1, "snippet": t[max(0, t.find(query) - 30): t.find(query) + len(query) + 30]}
            for i, t in enumerate(pages) if query in t]
    return {"status": "ok" if hits else "empty", "hits": hits[:8],
            "note": "search_text 命中不能单独作为数值裁决依据"}


def dispatch(name: str, facts: list[dict], arguments: dict,
             document_texts: dict | None = None, draft: str = "") -> dict:
    """JSON 多步协议的执行器。未知工具 / 参数错误一律 error，不抛栈。"""
    try:
        if name == "list_catalog":
            return list_catalog(arguments.get("kind", "metrics"))
        if name == "find_evidence":
            return find_evidence(facts, draft=draft, **arguments)
        if name == "compute_yoy":
            return compute_yoy(facts, draft=draft, **arguments)
        if name == "compare_claim":
            return compare_claim(facts, draft=draft, **arguments)
        if name == "search_text":
            return search_text(facts, document_texts, **arguments)
        return _err(f"未知工具：{name}")
    except TypeError as exc:
        return _err(f"工具参数不合法：{exc}", tool=name, arguments=arguments)
    except (ValueError, ArithmeticError, KeyError) as exc:
        return _err(f"工具执行失败：{exc}", tool=name)
