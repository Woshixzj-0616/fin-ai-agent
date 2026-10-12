"""年报正文检索工具。

正文按“公司_年度::材料ID”索引：同一公司同一年度的季报、半年报、年报各自独立，
不再写同一个键互相覆盖；旧的公司_年度键仍然可读，但只在候选唯一时使用。
"""
from __future__ import annotations

import re

from audit.grounding import resolve_company
from audit.tools.evidence import _err

DOCUMENT_KEY_SEPARATOR = "::"


def document_key(company_code: str, report_year, document_id) -> str:
    """材料身份键；没有材料 ID 时退化为旧的公司_年度键。"""
    head = f"{company_code}_{int(report_year)}"
    return f"{head}{DOCUMENT_KEY_SEPARATOR}{document_id}" if document_id else head


def parse_document_key(key: str) -> dict | None:
    """解析身份键；旧的公司_年度键返回 None（按旧口径回退）。"""
    if not isinstance(key, str) or DOCUMENT_KEY_SEPARATOR not in key:
        return None
    head, _, document_id = key.partition(DOCUMENT_KEY_SEPARATOR)
    company, _, year = head.rpartition("_")
    if not re.fullmatch(r"\d{6}", company) or not re.fullmatch(r"\d{4}", year):
        return None
    return {"company_code": company, "report_year": int(year),
            "document_id": document_id or None}


def document_index(facts: list[dict] | None) -> list[dict]:
    """按材料身份汇总本次运行加载的报告：公司、年度、报告类型、期间、文件与指纹。"""
    groups: dict[str, dict] = {}
    for fact in facts or []:
        identity = fact.get("document_id") or fact.get("source_sha256") or "unknown"
        entry = groups.setdefault(identity, {
            "identity": identity, "document_id": fact.get("document_id"),
            "source_sha256": fact.get("source_sha256"),
            "company_code": fact.get("company_code"), "company_name": fact.get("company_name"),
            "report_year": fact.get("report_year"), "source_file": fact.get("source_file"),
            "period_kinds": set(), "periods": set(), "pages": []})
        if fact.get("period_year") == fact.get("report_year"):
            if fact.get("period_kind"):
                entry["period_kinds"].add(fact["period_kind"])
            if fact.get("period_start") or fact.get("period_end"):
                entry["periods"].add((fact.get("period_start"), fact.get("period_end")))
        if isinstance(fact.get("page"), int):
            entry["pages"].append(fact["page"])
    index = []
    for entry in groups.values():
        kinds = sorted(entry.pop("period_kinds"))
        periods = sorted(period for period in entry.pop("periods") if any(period))
        index.append({**entry,
                      "period_kind": kinds[0] if len(kinds) == 1 else None,
                      "period_kinds": kinds,
                      "period_start": periods[0][0] if periods else None,
                      "period_end": periods[0][1] if periods else None,
                      "pages": max(entry["pages"]) if entry["pages"] else None})
    return sorted(index, key=lambda item: (str(item.get("company_code")), item.get("report_year") or 0,
                                           str(item.get("document_id") or "")))


def _keys_for(document: dict, code: str, year: int) -> list[str]:
    keys = []
    if document.get("document_id"):
        keys.append(document_key(code, year, document["document_id"]))
    keys.append(f"{code}_{year}")
    return keys


def resolve_document(facts: list[dict] | None, document_texts: dict, *, code: str, report_year: int,
                     report_kind: str | None = None, document_id: str | None = None,
                     document_key_value: str | None = None) -> dict:
    """定位唯一材料：显式材料 ID/报告类型优先，多候选且未明确期间时保持歧义。"""
    year = int(report_year)
    candidates = [doc for doc in document_index(facts)
                  if doc.get("company_code") == code and doc.get("report_year") == year]
    if document_id:
        narrowed = [doc for doc in candidates if doc.get("document_id") == document_id]
        if not narrowed:
            narrowed = [doc for doc in candidates if doc.get("document_id") is None]
        candidates = narrowed
    if report_kind and [doc for doc in candidates if doc.get("period_kind")]:
        candidates = [doc for doc in candidates if doc.get("period_kind") == report_kind]
    if document_key_value:
        if document_key_value not in document_texts:
            return {"status": "empty", "hint": f"正文索引里没有 {document_key_value}"}
        parsed = parse_document_key(document_key_value) or {}
        document = next((doc for doc in candidates
                         if doc.get("document_id") == parsed.get("document_id")), None)
        if document is None:
            document = next((entry for entry in document_index(facts)
                             if entry.get("document_id") == parsed.get("document_id")), {})
        return {"status": "ok", "key": document_key_value, "document": document}
    available = []
    for document in candidates:
        key = next((item for item in _keys_for(document, code, year) if item in document_texts), None)
        if key:
            available.append({**document, "key": key})
    if len(available) == 1:
        chosen = available[0]
        return {"status": "ok", "key": chosen["key"], "document": chosen}
    if len(available) > 1:
        return {"status": "ambiguous", "candidates": [
            {"document_id": doc.get("document_id"), "report_year": doc.get("report_year"),
             "period_kind": doc.get("period_kind"), "period_start": doc.get("period_start"),
             "period_end": doc.get("period_end"), "source_file": doc.get("source_file"),
             "key": doc["key"]} for doc in available],
            "hint": "同一公司同一年度有多份报告正文；请给出 document_id 或报告类型后再检索"}
    legacy = f"{code}_{year}"
    if legacy in document_texts:
        return {"status": "ok", "key": legacy,
                "document": candidates[0] if len(candidates) == 1 else {}}
    return {"status": "empty", "candidates": [
        {"document_id": doc.get("document_id"), "report_year": doc.get("report_year"),
         "period_kind": doc.get("period_kind"), "source_file": doc.get("source_file")}
        for doc in candidates], "hint": f"未找到 {code} {year} 的正文页文本"}


def search_text(facts: list[dict], document_texts: dict | None = None, *,
                company_name_or_code: str, source_report_year: int, query: str,
                top_k: int = 8, report_kind: str | None = None,
                document_id: str | None = None, document_key: str | None = None) -> dict:
    """文档正文语义检索（BM25 / 可选 embedding）。

    document_texts 由调用方注入（公司_年度::材料ID -> [page_text]，兼容旧的公司_年度键）。
    命中只是相关段落，**不能单独作为数值裁决依据**；命中带公司、期间、文件与页码。
    """
    if not query or len(query) > 40:
        return _err("query 需为 1–40 字")
    if not document_texts:
        return {"status": "empty", "hits": [], "hint": "未注入文档正文，无法全文检索"}
    code = resolve_company(company_name_or_code, company_name_or_code, company_name_or_code, facts)
    if not code:
        return _err("公司无法对应到本次已加载材料", company=company_name_or_code)
    located = resolve_document(facts, document_texts, code=code, report_year=int(source_report_year),
                               report_kind=report_kind, document_id=document_id,
                               document_key_value=document_key)
    if located.get("status") != "ok":
        return {"status": located.get("status") or "empty", "hits": [],
                **{key: located[key] for key in ("hint", "candidates") if located.get(key)}}
    key = located["key"]
    document = located.get("document") or parse_document_key(key) or {}
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
    identity = {"company_code": code, "company_name": document.get("company_name"),
                "report_year": document.get("report_year") or int(source_report_year),
                "period_kind": document.get("period_kind"), "period_start": document.get("period_start"),
                "period_end": document.get("period_end"),
                "document_id": document.get("document_id"), "source_file": document.get("source_file")}
    hits = [{**hit, **{k: v for k, v in identity.items() if v is not None}} for hit in hits]
    return {"status": "ok" if hits else "empty",
            "hits": hits[: max(1, min(int(top_k), 20))],
            "document_key": key, **identity,
            "channel": "embedding" if hits and hits[0].get("channel") == "embedding" else "bm25",
            "note": "search_text 命中为相关段落检索，不能单独作为数值裁决依据；数值必须走 compare_claim"}


def run_documents(facts: list[dict] | None, document_texts: dict) -> list[dict]:
    """本次运行里实际可检索的报告；没有材料元数据时退回正文键本身（旧结果兼容）。"""
    documents = []
    for document in document_index(facts):
        code, year = document.get("company_code"), document.get("report_year")
        if not code or not year:
            continue
        key = next((item for item in _keys_for(document, code, year) if item in document_texts), None)
        if key:
            documents.append({**document, "key": key})
    if documents:
        return documents
    for key in sorted(document_texts):
        parsed = parse_document_key(key)
        if parsed:
            documents.append({"key": key, **parsed, "company_name": None})
            continue
        code, _, year = key.rpartition("_")
        documents.append({"key": key, "company_code": code or None,
                          "report_year": int(year) if year.isdigit() else None,
                          "document_id": None, "company_name": None})
    return documents


def search_run_text(facts: list[dict] | None, document_texts: dict, *, query: str,
                    top_k: int = 8, report_kind: str | None = None,
                    document_id: str | None = None) -> dict:
    """HTTP 层入口：未指定材料时逐份检索并合并，命中各自带公司、期间、文件与页码。"""
    if not document_texts:
        return {"status": "empty", "hits": [], "hint": "本次运行没有可检索的页文本"}
    documents = run_documents(facts, document_texts)
    if report_kind:
        known = [doc for doc in documents if doc.get("period_kind")]
        if known:
            documents = [doc for doc in documents if doc.get("period_kind") == report_kind]
    if document_id:
        documents = [doc for doc in documents if doc.get("document_id") == document_id]
    if not documents:
        return {"status": "empty", "hits": [],
                "hint": "没有与所选报告类型/材料 ID 匹配的正文"}
    limit = max(1, min(int(top_k), 20))
    merged, seen = [], set()
    for document in documents:
        code = document.get("company_code")
        year = document.get("report_year")
        if not code or not year:
            continue
        result = search_text(facts, document_texts, company_name_or_code=code,
                             source_report_year=int(year), query=query, top_k=limit,
                             document_key=document["key"])
        for hit in result.get("hits") or []:
            marker = (document["key"], hit.get("page"), hit.get("snippet"))
            if marker in seen:
                continue
            seen.add(marker)
            merged.append({**hit, "document_key": document["key"]})
    merged.sort(key=lambda hit: -float(hit.get("score") or 0))
    return {"status": "ok" if merged else "empty",
            "hits": merged[:limit],
            "documents": [{"document_key": doc["key"], "document_id": doc.get("document_id"),
                           "company_code": doc.get("company_code"), "company_name": doc.get("company_name"),
                           "report_year": doc.get("report_year"), "period_kind": doc.get("period_kind"),
                           "period_end": doc.get("period_end"), "source_file": doc.get("source_file")}
                          for doc in documents],
            "document_key": documents[0]["key"],
            "channel": "bm25",
            "note": "检索命中为相关段落，不能单独作为数值裁决依据；数值必须走 compare_claim"}


__all__ = ["DOCUMENT_KEY_SEPARATOR", "document_index", "document_key", "parse_document_key",
           "resolve_document", "run_documents", "search_run_text", "search_text"]
