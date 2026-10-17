"""Solvency-specific first-page selection and query guidance."""

from __future__ import annotations

import re
from typing import Any

from backend.pdf_reader import MAX_CHARS_PER_PAGE, MAX_MODEL_PAGES, MAX_TOTAL_CHARS


SOLVENCY_KEYWORDS = {
    "合并资产负债表": 18,
    "资产负债表": 12,
    "货币资金": 12,
    "受限资金": 12,
    "受限资产": 10,
    "短期借款": 12,
    "长期借款": 12,
    "一年内到期的非流动负债": 14,
    "应付债券": 12,
    "租赁负债": 10,
    "流动性风险": 14,
    "到期期限": 12,
    "利息支出": 9,
    "已获利息倍数": 9,
    "利息保障倍数": 9,
    "偿还债务支付的现金": 8,
    "取得借款收到的现金": 8,
    "重大承诺": 8,
    "对外担保": 8,
    "授信额度": 7,
    "抵押": 5,
    "质押": 5,
    "合并利润表": 15,
    "利润总额": 12,
    "利息费用": 12,
    "费用化利息": 12,
    "合并现金流量表": 16,
    "经营活动产生的现金流量净额": 18,
    "经营活动现金流量净额": 16,
}

SOLVENCY_TABLE_LAYOUT_TERMS = (
    "货币资金", "借款", "债券", "到期", "租赁", "流动负债", "融资",
    "担保", "现金流", "利息", "资产总计", "负债合计", "流动资产", "所有者权益",
)

PAGE_GROUPS = (
    ("debt_notes", ("短期借款", "长期借款", "应付债券", "一年内到期的非流动负债", "租赁负债"), 3),
    ("cash_notes", ("受限资金", "受限资产", "货币资金", "保证金", "定期存款"), 2),
    ("maturity_and_commitments", ("流动性风险", "剩余期限", "到期期限", "重大承诺", "授信额度", "对外担保"), 2),
)

SEARCH_STARTERS = (
    "合并资产负债表 货币资金 短期借款 长期借款 一年内到期的非流动负债",
    "借款 分类 金额 利率 期限 抵押 担保 附注",
    "应付债券 债券 到期 回售 利息",
    "租赁负债 一年内到期 租赁付款",
    "货币资金 受限资金 保证金 定期存款 现金等价物",
    "流动性风险 剩余到期期限 未折现 合同现金流",
    "合并现金流量表 经营活动产生的现金流量净额",
    "合并利润表 利润总额 财务费用 利息费用 利息支出",
    "支付利息 偿还债务支付的现金 取得借款收到的现金",
    "授信额度 尚未使用 资本承诺 担保 期后事项",
)


def select_initial_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    readable = [page for page in pages if page.get("text")]
    if not readable:
        return []

    def score(page: dict[str, Any]) -> int:
        text = str(page.get("text", ""))
        return sum(min(text.count(word), 3) * weight for word, weight in SOLVENCY_KEYWORDS.items())

    chosen: dict[int, dict[str, Any]] = {}
    # Prioritize the three consolidated statements directly; keyword scoring
    # alone tends to over-rank narrative pages and subsidiary disclosures.
    for statement_title in ("合并资产负债表", "合并利润表", "合并现金流量表"):
        matches = [
            page for page in readable
            if any(line.strip() == statement_title for line in str(page.get("text", "")).splitlines())
        ]
        if matches:
            page = max(matches, key=lambda item: score(item))
            chosen[int(page["page"])] = page
            if statement_title == "合并资产负债表":
                next_page = int(page["page"]) + 1
                continuation = next((item for item in readable if int(item.get("page", 0)) == next_page), None)
                if continuation and any(term in str(continuation.get("text", "")) for term in ("流动负债：", "负债合计", "应付债券")):
                    chosen[next_page] = continuation
            else:
                next_page = int(page["page"]) + 1
                continuation = next((item for item in readable if int(item.get("page", 0)) == next_page), None)
                continuation_terms = (
                    ("利润总额", "所得税费用", "净利润")
                    if statement_title == "合并利润表"
                    else ("经营活动产生的现金流量净额", "投资活动产生的现金流量净额", "筹资活动产生的现金流量净额")
                )
                if continuation and any(term in str(continuation.get("text", "")) for term in continuation_terms):
                    chosen[next_page] = continuation

    # Surface the current-year contractual maturity table early. Comparative
    # pages can otherwise dominate keyword ranking and use the read-page budget.
    maturity_candidates: list[tuple[int, dict[str, Any]]] = []
    for page in readable:
        compact = re.sub(r"\s+", "", str(page.get("text", "")))
        if "未折现合同金额" not in compact and "剩余到期期限" not in compact:
            continue
        years = [int(value) for value in re.findall(r"(20\d{2})年12月31日", compact)]
        if years:
            maturity_candidates.append((max(years), page))
    current_maturity_year = max((year for year, _ in maturity_candidates), default=0)
    if current_maturity_year:
        current_candidates = [
            page for year, page in maturity_candidates
            if year == current_maturity_year and int(page["page"]) not in chosen
        ]
        if current_candidates and len(chosen) < MAX_MODEL_PAGES:
            preferred = max(
                current_candidates,
                key=lambda page: str(page.get("text", "")).count("流动性风险"),
            )
            chosen[int(preferred["page"])] = preferred

    for _name, keywords, quota in PAGE_GROUPS:
        ranked_group = sorted(
            readable,
            key=lambda page: (
                sum(min(str(page.get("text", "")).count(word), 3) * SOLVENCY_KEYWORDS.get(word, 8) for word in keywords),
                -int(page.get("page", 0)),
            ),
            reverse=True,
        )
        selected = 0
        for page in ranked_group:
            group_score = sum(min(str(page.get("text", "")).count(word), 3) for word in keywords)
            page_number = int(page["page"])
            compact = re.sub(r"\s+", "", str(page.get("text", "")))
            if (
                current_maturity_year
                and ("未折现合同金额" in compact or "剩余到期期限" in compact)
                and f"{current_maturity_year}年12月31日" not in compact
            ):
                continue
            if group_score <= 0 or page_number in chosen:
                continue
            chosen[page_number] = page
            selected += 1
            if selected >= quota or len(chosen) >= MAX_MODEL_PAGES:
                break
        if len(chosen) >= MAX_MODEL_PAGES:
            break

    ranked = sorted(readable, key=lambda page: (score(page), -int(page.get("page", 0))), reverse=True)
    for page in ranked:
        if score(page) <= 0:
            continue
        page_number = int(page["page"])
        compact = re.sub(r"\s+", "", str(page.get("text", "")))
        if (
            current_maturity_year
            and ("未折现合同金额" in compact or "剩余到期期限" in compact)
            and f"{current_maturity_year}年12月31日" not in compact
        ):
            continue
        if page_number in chosen:
            continue
        if len(chosen) >= MAX_MODEL_PAGES:
            break
        chosen[page_number] = page
        if len(chosen) >= MAX_MODEL_PAGES:
            break
    if not chosen:
        return []

    result: list[dict[str, Any]] = []
    page_char_budget = min(MAX_CHARS_PER_PAGE, max(1, MAX_TOTAL_CHARS // max(1, len(chosen))))
    remaining = MAX_TOTAL_CHARS
    for page in sorted(chosen.values(), key=lambda item: int(item["page"])):
        full_text = str(page["text"])
        layout_lines = re.findall(r"^\[PDF_TABLE_LAYOUT\][^\n]*$", full_text, flags=re.MULTILINE)
        body = re.sub(r"\n?\[PDF_TABLE_LAYOUT\][^\n]*", "", full_text).strip()
        excerpt = body[: min(page_char_budget, remaining)].strip()
        if excerpt or layout_lines:
            combined = "\n".join(part for part in (excerpt, *layout_lines) if part)
            result.append({"page": int(page["page"]), "text": combined})
            # Layout blocks are complete JSON records; preserve them in full and
            # charge the ordinary text excerpt against the existing text budget.
            remaining -= len(excerpt)
        if remaining <= 0:
            break
    return result
