"""Mechanical PDF text extraction and page selection for the first prototype."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pymupdf


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 800
MAX_MODEL_PAGES = 12
MAX_CHARS_PER_PAGE = 3200
MAX_TOTAL_CHARS = 26000

KEYWORDS = (
    "主要会计数据和财务指标",
    "主要会计数据",
    "营业收入",
    "归属于上市公司股东的净利润",
    "扣除非经常性损益",
    "经营活动产生的现金流量净额",
    "营业总收入",
    "现金流量净额",
)

BUSINESS_KEYWORDS = {
    "主营业务分析": 16,
    "公司业务概要": 14,
    "经营情况讨论与分析": 13,
    "营业收入构成": 12,
    "分行业": 10,
    "分产品": 10,
    "分地区": 10,
    "主要业务": 8,
    "销售模式": 6,
    "产销量": 6,
    "产能": 4,
    "前五名客户": 8,
    "主要供应商": 6,
    "重大变化": 4,
}

PROFIT_KEYWORDS = {
    "合并利润表": 18,
    "利润表": 10,
    "营业总收入": 14,
    "营业收入": 8,
    "营业总成本": 12,
    "营业利润": 10,
    "利润总额": 10,
    "归属于母公司所有者的净利润": 10,
    "扣除非经常性损益后的净利润": 10,
    "非经常性损益": 8,
    "所得税费用": 6,
    "少数股东损益": 5,
    "分产品": 4,
    "分行业": 4,
}


class PdfInputError(ValueError):
    """A user-facing input or text extraction error."""


@dataclass
class ExtractedPdf:
    page_count: int
    selected_pages: list[dict[str, int | str]]
    table_layout_pages: list[int] = field(default_factory=list)
    table_count: int = 0


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[\t\u00a0 ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_relevant_pages(content: bytes) -> ExtractedPdf:
    if len(content) > MAX_UPLOAD_BYTES:
        raise PdfInputError("文件超过 25 MB，请先压缩 PDF 后重试。")
    if not content.startswith(b"%PDF-"):
        raise PdfInputError("上传内容不是有效的 PDF 文件。")

    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise PdfInputError("无法打开这份 PDF，请确认文件没有损坏或加密。") from exc

    try:
        page_count = document.page_count
        if page_count < 1:
            raise PdfInputError("PDF 中没有可读取的页面。")
        if page_count > MAX_PDF_PAGES:
            raise PdfInputError(f"PDF 共 {page_count} 页，当前原型最多处理 {MAX_PDF_PAGES} 页。")

        pages: list[tuple[int, str, int]] = []
        for index in range(page_count):
            text = _clean_text(document.load_page(index).get_text("text"))
            if text:
                score = sum(text.count(keyword) for keyword in KEYWORDS)
                if index == 0:
                    score += 4
                if "主要会计数据和财务指标" in text:
                    score += 12
                pages.append((index + 1, text, score))

        if not pages:
            raise PdfInputError(
                "这份 PDF 没有可提取的文字，可能是扫描件。阶段一暂不处理扫描 PDF。"
            )

        # Always include the first readable page, then the most relevant pages.
        first_page = pages[0]
        ranked = sorted(pages, key=lambda item: (item[2], -item[0]), reverse=True)
        chosen: dict[int, tuple[int, str, int]] = {first_page[0]: first_page}
        for item in ranked:
            if len(chosen) >= MAX_MODEL_PAGES:
                break
            chosen[item[0]] = item
        selected = sorted(chosen.values(), key=lambda item: item[0])

        bounded: list[dict[str, int | str]] = []
        remaining = MAX_TOTAL_CHARS
        for page_number, text, _score in selected:
            excerpt = text[: min(MAX_CHARS_PER_PAGE, remaining)].strip()
            if excerpt:
                bounded.append({"page": page_number, "text": excerpt})
                remaining -= len(excerpt)
            if remaining <= 0:
                break

        return ExtractedPdf(page_count=page_count, selected_pages=bounded)
    finally:
        document.close()


def _append_table_layout(
    page: Any,
    page_number: int,
    text: str,
    keywords: tuple[str, ...],
) -> tuple[str, int]:
    """Append bounded row/column data for relevant pages when PyMuPDF finds tables."""
    compact_text = re.sub(r"\s+", "", text)
    compact_keywords = tuple(re.sub(r"\s+", "", term) for term in keywords)
    if not compact_keywords or not any(term in compact_text for term in compact_keywords):
        return text, 0
    find_tables = getattr(page, "find_tables", None)
    if not callable(find_tables):
        return text, 0
    def serialize_tables(tables: Any) -> list[str]:
        blocks: list[str] = []
        for table_index, table in enumerate(tables or [], start=1):
            if len(blocks) >= 6:
                break
            try:
                extracted_rows = table.extract() or []
                rows = [
                    [str(cell or "").replace("\n", " ").strip() for cell in row]
                    for row in extracted_rows
                    if isinstance(row, (list, tuple))
                ]
                if len(rows) < 2:
                    continue
                if sum(len(row) for row in rows) > 300:
                    continue
                table_text = re.sub(r"\s+", "", "".join("".join(row) for row in rows))
                if not any(term in table_text for term in compact_keywords):
                    continue

                row_objects = getattr(table, "rows", []) or []
                cell_bboxes: list[list[list[float] | None]] = []
                for row_index, row in enumerate(rows):
                    source_cells = getattr(row_objects[row_index], "cells", []) if row_index < len(row_objects) else []
                    box_row: list[list[float] | None] = []
                    for column_index in range(len(row)):
                        rect = source_cells[column_index] if column_index < len(source_cells) else None
                        if rect is None:
                            box_row.append(None)
                        else:
                            try:
                                box_row.append([round(float(value), 1) for value in rect])
                            except (TypeError, ValueError):
                                box_row.append(None)
                    cell_bboxes.append(box_row)

                bbox = getattr(table, "bbox", None)
                try:
                    table_bbox = [round(float(value), 1) for value in bbox] if bbox is not None else None
                except (TypeError, ValueError):
                    table_bbox = None
                payload = json.dumps(
                    {"bbox": table_bbox, "rows": rows, "cell_bboxes": cell_bboxes},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                blocks.append(f"[PDF_TABLE_LAYOUT] page={page_number} table={table_index} {payload}")
            except Exception:
                # A malformed or unsupported table must not make the whole report unreadable.
                continue
        return blocks

    blocks: list[str] = []
    for options in ({}, {"strategy": "text"}):
        try:
            found = find_tables(**options)
        except Exception:
            continue
        blocks = serialize_tables(getattr(found, "tables", []))
        if blocks:
            break
    if not blocks:
        return text, 0
    return text + "\n" + "\n".join(blocks), len(blocks)


def extract_all_pages(content: bytes, *, table_layout_keywords: tuple[str, ...] = ()) -> ExtractedPdf:
    """Extract and retain text for every page so later questions can search beyond the first pass."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise PdfInputError("文件超过 25 MB，请先压缩 PDF 后重试。")
    if not content.startswith(b"%PDF-"):
        raise PdfInputError("上传内容不是有效的 PDF 文件。")
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise PdfInputError("无法打开这份 PDF，请确认文件没有损坏或加密。") from exc
    try:
        page_count = document.page_count
        if page_count < 1:
            raise PdfInputError("PDF 中没有可读取的页面。")
        if page_count > MAX_PDF_PAGES:
            raise PdfInputError(f"PDF 共 {page_count} 页，当前最多处理 {MAX_PDF_PAGES} 页。")
        pages: list[dict[str, int | str]] = []
        table_layout_pages: list[int] = []
        table_count = 0
        for index in range(page_count):
            page_number = index + 1
            page = document.load_page(index)
            text = _clean_text(page.get_text("text"))
            if table_layout_keywords:
                text, found_count = _append_table_layout(page, page_number, text, table_layout_keywords)
                if found_count:
                    table_layout_pages.append(page_number)
                    table_count += found_count
            pages.append({"page": page_number, "text": text})
        if not any(page["text"] for page in pages):
            raise PdfInputError("这份 PDF 没有可提取的文字，可能是扫描件；当前版本暂不处理扫描 PDF。")
        return ExtractedPdf(
            page_count=page_count,
            selected_pages=pages,
            table_layout_pages=table_layout_pages,
            table_count=table_count,
        )
    finally:
        document.close()


def select_initial_pages(
    pages: list[dict[str, Any]], analysis_module: str = "overview"
) -> list[dict[str, Any]]:
    """Choose a bounded first batch; the model can request more pages through tools."""
    readable = [page for page in pages if page.get("text")]
    if not readable:
        return []
    if analysis_module == "business":
        score_page = lambda page: sum(
            min(str(page["text"]).count(keyword), 3) * weight
            for keyword, weight in BUSINESS_KEYWORDS.items()
        ) + (4 if int(page["page"]) == 1 else 0)
    elif analysis_module == "profit":
        score_page = lambda page: sum(
            min(str(page["text"]).count(keyword), 3) * weight
            for keyword, weight in PROFIT_KEYWORDS.items()
        ) + (4 if int(page["page"]) == 1 else 0)
    else:
        score_page = lambda page: (
            sum(str(page["text"]).count(keyword) for keyword in KEYWORDS)
            + (12 if "主要会计数据和财务指标" in str(page["text"]) else 0)
            + (4 if int(page["page"]) == 1 else 0)
        )
    ranked = sorted(
        readable,
        key=lambda page: (score_page(page), -int(page["page"])),
        reverse=True,
    )
    selected = {int(readable[0]["page"]): readable[0]}
    for page in ranked:
        if len(selected) >= MAX_MODEL_PAGES:
            break
        selected[int(page["page"])] = page

    output: list[dict[str, Any]] = []
    remaining = MAX_TOTAL_CHARS
    for page in sorted(selected.values(), key=lambda item: int(item["page"])):
        text = str(page["text"])
        excerpt = text[: min(MAX_CHARS_PER_PAGE, remaining)].strip()
        if excerpt:
            output.append({"page": int(page["page"]), "text": excerpt})
            remaining -= len(excerpt)
        if remaining <= 0:
            break
    return output


def rank_search_pages(
    pages: list[dict[str, Any]], query: str, *, limit: int = 4, excerpt_chars: int = 1800
) -> list[dict[str, Any]]:
    """Rank stored page text with exact phrases and overlapping Chinese character n-grams."""
    cleaned_query = _clean_text(query)
    if not cleaned_query:
        return []

    stop_words = {"请问", "请查", "帮我", "分析", "一下", "哪些", "什么", "如何", "为什么", "公司", "年报"}
    terms: set[str] = set()
    for match in re.findall(r"[A-Za-z0-9][A-Za-z0-9.%_-]*|[\u4e00-\u9fff]{2,}", cleaned_query):
        if match in stop_words:
            continue
        terms.add(match)
        if re.fullmatch(r"[\u4e00-\u9fff]+", match):
            for width in (3, 2):
                terms.update(match[index:index + width] for index in range(max(0, len(match) - width + 1)))

    ranked: list[tuple[int, int, str]] = []
    normalized_query = re.sub(r"\s+", "", cleaned_query)
    for page in pages:
        text = str(page.get("text", ""))
        if not text:
            continue
        normalized_text = re.sub(r"\s+", "", text)
        score = 0
        if normalized_query and normalized_query in normalized_text:
            score += 100
        for term in terms:
            count = normalized_text.count(re.sub(r"\s+", "", term))
            if count:
                score += min(count, 4) * (len(term) + 1)
        if score:
            ranked.append((score, int(page["page"]), text))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    results: list[dict[str, Any]] = []
    for score, page_number, text in ranked[: max(1, min(limit, 8))]:
        results.append({"page": page_number, "score": score, "text": text[:excerpt_chars]})
    return results
