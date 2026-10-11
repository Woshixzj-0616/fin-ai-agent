"""年报正文检索工具。"""
from __future__ import annotations

from audit.grounding import resolve_company
from audit.tools.evidence import _err


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
