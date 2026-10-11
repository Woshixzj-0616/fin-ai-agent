"""草稿拆句与公司名接地。"""
from __future__ import annotations

import re

from audit.prompt import MAX_DRAFT_CHARS
from audit.schema import COMPANIES, LLMError
from backend_runtime import load_config, sentence_rows


def split_draft(draft: str) -> list[dict]:
    try:
        return [{"sentence_id": row["sentence_id"], "text": row["text"]}
                for row in sentence_rows(draft, load_config())]
    except ValueError as exc:
        raise LLMError(str(exc)) from None


def resolve_company(name, source: str, draft: str, facts: list[dict] | None = None) -> str | None:
    """公司名 → 证券代码。必须同时：①在本句引文或整份草稿里能指到 ②能对应到本次已加载材料。

    只认原文写法（错别字不纠正）；公司可以写在草稿开头（如「贵州茅台2024年报…」），
    后续句子用模型补全的 company_name 时，允许在整份草稿范围内溯源，不限句级引文。
    不设固定白名单：任何已登记材料的公司都能解析；已知别名表只作软匹配辅助。
    """
    if not name or not isinstance(name, str):
        return None
    if name not in source and name not in draft:
        return None

    def match_facts() -> str | None:
        if not facts:
            return None
        hits: set[str] = set()
        for fact in facts:
            code = fact.get("company_code") or ""
            cname = fact.get("company_name") or ""
            if not code:
                continue
            if name == code or (cname and (name == cname or name in cname or cname in name)):
                hits.add(code)
        return hits.pop() if len(hits) == 1 else None

    hit = match_facts()
    if hit:
        return hit

    if re.fullmatch(r"\d{6}", name):
        return name if facts is None or any(f.get("company_code") == name for f in facts) else None

    code = next((c for c, names in COMPANIES.items() if name in [c, *names]), None)
    if code and (facts is None or any(f.get("company_code") == code for f in facts)):
        return code
    return None
