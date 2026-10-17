"""Asset-specific document discovery for module three."""

from __future__ import annotations

import re
from typing import Any


INITIAL_PAGE_LIMIT = 18
PAGE_CHAR_LIMIT = 3_600
TOTAL_CHAR_LIMIT = 48_000

ASSET_PAGE_TERMS: dict[str, int] = {
    "合并资产负债表": 24,
    "资产负债表": 16,
    "应收账款": 18,
    "坏账准备": 18,
    "信用损失": 12,
    "预期信用损失": 12,
    "账龄": 10,
    "应收款项融资": 10,
    "应收票据": 8,
    "合同资产": 10,
    "其他应收款": 8,
    "预付款项": 7,
    "存货": 16,
    "存货跌价准备": 18,
    "原材料": 8,
    "在产品": 8,
    "库存商品": 8,
    "产成品": 8,
    "发出商品": 7,
    "固定资产": 13,
    "累计折旧": 10,
    "在建工程": 14,
    "工程项目": 7,
    "商誉": 8,
    "无形资产": 7,
    "投资性房地产": 6,
    "使用权资产": 6,
    "资产减值": 8,
    "长期待摊": 5,
    "营业收入": 5,
    "营业成本": 5,
    "产能利用率": 7,
    "项目进度": 5,
}

ASSET_SEARCH_QUERIES = (
    "合并资产负债表 期末余额 期初余额 单位",
    "应收账款 账面余额 坏账准备 账龄 客户集中度",
    "存货 原材料 在产品 产成品 存货跌价准备",
    "固定资产 在建工程 项目进度 转入固定资产",
    "资产减值 商誉 无形资产 期后回款",
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _page_score(text: str) -> int:
    return sum(min(text.count(term), 4) * weight for term, weight in ASSET_PAGE_TERMS.items())


def _focused_excerpt(text: str) -> str:
    if len(text) <= PAGE_CHAR_LIMIT:
        return text
    positions = [match.start() for match in re.finditer("|".join(map(re.escape, ASSET_PAGE_TERMS)), text)]
    if not positions:
        return text[:PAGE_CHAR_LIMIT]
    chunks: list[str] = []
    used: set[tuple[int, int]] = set()
    budget = PAGE_CHAR_LIMIT
    for position in positions:
        start = max(0, position - 1_000)
        end = min(len(text), position + 2_400)
        if any(start < old_end and end > old_start for old_start, old_end in used):
            continue
        chunk = text[start:end]
        if len(chunk) > budget:
            chunk = chunk[:budget]
        if chunk:
            chunks.append(("\n…\n" if chunks else "") + chunk)
            used.add((start, end))
            budget -= len(chunk)
        if budget <= 300:
            break
    return "".join(chunks)[:PAGE_CHAR_LIMIT]


def select_asset_pages(pages: list[dict[str, Any]], *, limit: int = INITIAL_PAGE_LIMIT) -> list[dict[str, Any]]:
    """Select a report-specific first batch; keep full page text available for follow-up reads."""
    readable = [page for page in pages if page.get("text")]
    if not readable:
        return []
    ranked = sorted(
        readable,
        key=lambda page: (_page_score(str(page["text"])), -int(page["page"])),
        reverse=True,
    )
    selected_pages = {int(readable[0]["page"])}
    for page in ranked:
        if len(selected_pages) >= max(1, limit):
            break
        selected_pages.add(int(page["page"]))
    selected = sorted(
        (page for page in readable if int(page["page"]) in selected_pages),
        key=lambda page: int(page["page"]),
    )
    result: list[dict[str, Any]] = []
    remaining = TOTAL_CHAR_LIMIT
    for page in selected:
        excerpt = _focused_excerpt(str(page["text"]))[:remaining]
        if excerpt:
            result.append({"page": int(page["page"]), "text": excerpt})
            remaining -= len(excerpt)
        if remaining <= 0:
            break
    return result


def discover_asset_pages(context: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use the common search recorder and an asset-specific ranker to build first-pass context."""
    candidates: dict[int, dict[str, Any]] = {
        int(page["page"]): page for page in select_asset_pages(context.pages)
    }
    search_log: list[dict[str, Any]] = []
    for query in ASSET_SEARCH_QUERIES:
        try:
            hits = context.search_pages(query, limit=5)
        except Exception as exc:
            search_log.append({"query": query, "error_type": type(exc).__name__, "pages": []})
            continue
        hit_pages: list[int] = []
        for hit in hits:
            try:
                page_number = int(hit["page"])
            except (KeyError, TypeError, ValueError):
                continue
            hit_pages.append(page_number)
            if page_number in candidates:
                continue
            full_page = context.read_page(page_number)
            text = full_page or str(hit.get("text") or "")
            if text:
                candidates[page_number] = {"page": page_number, "text": text}
        search_log.append({"query": query, "pages": hit_pages})

    ranked = sorted(
        candidates.values(),
        key=lambda page: (_page_score(str(page.get("text", ""))), -int(page["page"])),
        reverse=True,
    )
    chosen = {int(page["page"]): page for page in select_asset_pages(ranked)}
    # Add adjacent pages where a table heading or continuation page is likely to be split.
    all_pages = {int(page["page"]): page for page in context.pages if page.get("text")}
    for page_number in list(chosen):
        for neighbor in (page_number - 1, page_number + 1):
            page = all_pages.get(neighbor)
            if page and _page_score(str(page["text"])) >= 8 and len(chosen) < INITIAL_PAGE_LIMIT + 4:
                chosen.setdefault(neighbor, page)
    material = select_asset_pages(list(chosen.values()), limit=INITIAL_PAGE_LIMIT + 4)
    context.save_artifact("assets_discovery", {"queries": search_log, "candidate_pages": sorted(candidates)})
    return material, search_log

