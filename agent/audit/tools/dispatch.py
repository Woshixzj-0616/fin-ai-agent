"""只读工具的唯一调度表。"""
from __future__ import annotations

from audit.tools.compare import compare_claim, compare_companies
from audit.tools.compute import compute_trend, compute_yoy
from audit.tools.evidence import _err, find_evidence, list_catalog
from audit.tools.search import search_text


DISPATCH_HANDLERS = {
    "list_catalog": list_catalog,
    "find_evidence": find_evidence,
    "compute_yoy": compute_yoy,
    "compare_claim": compare_claim,
    "compare_companies": compare_companies,
    "compute_trend": compute_trend,
    "search_text": search_text,
}


def dispatch(name: str, facts: list[dict], arguments: dict,
             document_texts: dict | None = None, draft: str = "") -> dict:
    """JSON 多步协议的执行器。未知工具 / 参数错误一律 error，不抛栈。"""
    try:
        handler = DISPATCH_HANDLERS.get(name)
        if handler is None:
            return _err(f"未知工具：{name}")
        if name == "list_catalog":
            return handler(arguments.get("kind", "metrics"), facts)
        if name == "search_text":
            return handler(facts, document_texts, **arguments)
        return handler(facts, draft=draft, **arguments)
    except TypeError as exc:
        return _err(f"工具参数不合法：{exc}", tool=name, arguments=arguments)
    except (ValueError, ArithmeticError, KeyError) as exc:
        return _err(f"工具执行失败：{exc}", tool=name)
