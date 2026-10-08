"""Annual-report preflight search owned by module one."""

from __future__ import annotations

from typing import Any, Callable


MAX_BUSINESS_EXTRA_PAGES = 8

MAX_BUSINESS_EXTRA_CHARS = 14000

BUSINESS_SECTION_SEARCHES = {
    "business_model": ("主营业务分析", "商业模式"),
    "industry_context": ("行业发展", "竞争格局"),
    "strategy_competitiveness": ("发展战略", "核心竞争力"),
    "revenue_structure": ("分产品收入", "分地区收入"),
    "dependencies_risks": ("前五名客户", "主要供应商", "风险因素"),
    "major_changes": ("重大变化", "产能建设"),
}


def _business_preflight_search(
    *,
    search_pages: Callable[[str], list[dict[str, Any]]],
    get_page: Callable[[int], str | None],
    already_provided: set[int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Search the required module-one sections before asking the model to synthesize them."""
    sections: dict[str, dict[str, Any]] = {}
    trace: list[dict[str, Any]] = []
    ranked_pages: dict[int, tuple[int, str]] = {}
    for key, queries in BUSINESS_SECTION_SEARCHES.items():
        hits_by_page: dict[int, dict[str, Any]] = {}
        for query in queries:
            hits = search_pages(query)
            for hit in hits[:4]:
                try:
                    page_number = int(hit["page"])
                except (KeyError, TypeError, ValueError):
                    continue
                if page_number < 1:
                    continue
                score = int(hit.get("score", 0) or 0)
                prior = hits_by_page.get(page_number)
                if prior is None or score > int(prior.get("score", 0) or 0):
                    hits_by_page[page_number] = hit
                if page_number not in ranked_pages or score > ranked_pages[page_number][0]:
                    ranked_pages[page_number] = (score, str(hit.get("text", "")))
        ordered = sorted(hits_by_page.values(), key=lambda item: (-int(item.get("score", 0) or 0), int(item["page"])))
        candidate_pages = [int(item["page"]) for item in ordered[:4]]
        sections[key] = {
            "queries": list(queries),
            "candidate_pages": candidate_pages,
            "search_hit_count": len(hits_by_page),
        }
        trace.append(
            {
                "tool": "module1_preflight_search",
                "arguments": {"section": key, "queries": list(queries)},
                "pages": candidate_pages,
                "result": {"candidate_page_count": len(hits_by_page)},
            }
        )

    selected_extra: list[int] = []
    # Give each section its strongest candidate first, then use remaining room for follow-up hits.
    max_candidates = max((len(item["candidate_pages"]) for item in sections.values()), default=0)
    for index in range(max_candidates):
        for section in sections.values():
            candidates = section["candidate_pages"]
            if index >= len(candidates):
                continue
            page_number = candidates[index]
            if page_number in already_provided or page_number in selected_extra:
                continue
            if len(selected_extra) >= MAX_BUSINESS_EXTRA_PAGES:
                break
            selected_extra.append(page_number)
        if len(selected_extra) >= MAX_BUSINESS_EXTRA_PAGES:
            break

    extra_pages: list[dict[str, Any]] = []
    remaining_chars = MAX_BUSINESS_EXTRA_CHARS
    for page_number in selected_extra:
        text = get_page(page_number)
        if not text or remaining_chars <= 0:
            continue
        excerpt = str(text)[: min(3200, remaining_chars)].strip()
        if excerpt:
            extra_pages.append({"page": page_number, "text": excerpt})
            remaining_chars -= len(excerpt)
    for section in sections.values():
        section["provided_pages"] = [page for page in section["candidate_pages"] if page in already_provided or page in selected_extra]
    retrieval = {
        "sections": sections,
        "note": "检索结果仅用于定位候选页；未命中不代表年报未披露。",
    }
    return retrieval, extra_pages, trace
