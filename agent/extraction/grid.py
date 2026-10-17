"""表格线网格回退路径及报表旁证。"""
from __future__ import annotations

import re
from decimal import Decimal

import pymupdf
from finance import NON_AMOUNT_UNITS, convert, decimal, text
from materials import sha256
from words import (
    METRIC_UNIT,
    match_metric_at,
    norm,
    row_unit,
)

from .catalog import ALIASES, METRICS, PARENT_METRICS, default_scope


def box(rect) -> list[float]:
    return [round(float(n), 3) for n in rect]


def _decimal_cell(raw: str):
    """单元格文本 → Decimal。剥掉千分位、百分号与括号负号；不是数字就返回 None。"""
    if raw is None:
        return None
    t = str(raw).strip().replace(",", "").replace("，", "")
    t = re.sub(r"[%％]$", "", t)
    return decimal(t) if t else None


def cell_bbox(c: dict) -> list[float]:
    return [round(float(c["x0"]), 3), round(float(c["y0"]), 3),
            round(float(c["x1"]), 3), round(float(c["y1"]), 3)]


# ─────────────────────────── 表格线网格路径（回退，护 gold） ───────────────────────────

def table_cells(table) -> list[dict]:
    unique = {}
    for texts, row in zip(table.extract(), table.rows):
        for raw, rect in zip(texts, row.cells):
            if rect is not None:
                unique[tuple(rect)] = {"raw": raw or "", "text": norm(raw or ""),
                                       "bbox": box(rect)}
    return list(unique.values())


def header_for(cell: dict, cells: list[dict], pattern: str) -> dict | None:
    """多层表头：找覆盖数值单元格横向中心、且位于它上方的最近表头。"""
    x0, y0, x1, _ = cell["bbox"]
    center = (x0 + x1) / 2
    candidates = [c for c in cells
                  if re.fullmatch(pattern, c["text"])
                  and c["bbox"][0] <= center <= c["bbox"][2]
                  and c["bbox"][3] <= y0 + 1]
    return max(candidates, key=lambda c: c["bbox"][3]) if candidates else None


def amount_cell(cell: dict) -> bool:
    value = cell["text"].replace(",", "").replace("，", "")
    return bool(re.fullmatch(r"[-−]?\d+(?:\.\d+)?|[（(]\d+(?:\.\d+)?[）)]", value))


def label_for(anchor: dict, cells: list[dict]) -> tuple[str, list[float]]:
    x0, y0, _, y1 = anchor["bbox"]
    parts = [c for c in cells if c["text"] and not amount_cell(c)
             and c["text"] not in {"-", "—", "–"}
             and c["bbox"][2] <= x0 + 1
             and c["bbox"][0] < x0 - 10
             and y0 <= (c["bbox"][1] + c["bbox"][3]) / 2 <= y1]
    parts.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
    if not parts:
        return "", []
    return "".join(c["text"] for c in parts), [
        min(c["bbox"][0] for c in parts), min(c["bbox"][1] for c in parts),
        max(c["bbox"][2] for c in parts), max(c["bbox"][3] for c in parts)]


def metric_for(label: str):
    clean = re.sub(r"[（(](?:人民币)?(?:亿|万)?元[）)]$", "", re.sub(r"\s+", "", label))
    return match_metric_at(clean, ALIASES)[0]


def document_currency(document, texts: list[str]) -> dict | None:
    for index, content in enumerate(texts):
        compact = norm(content)
        found = re.search(r"(?:以人民币.{0,20}(?:记账本位币|报告货币)|"
                          r"本(?:公司|财务报表).{0,12}人民币.{0,12}(?:记账本位币|列报|列示))",
                          compact)
        if found:
            return {"currency": "CNY", "page": index + 1, "quote": found[0],
                    "basis": "document_currency_statement"}
    return None


def _statement_title(raw: str) -> str | None:
    """把各家报表标题归一到「合并/母公司 利润表/现金流量表」。

    美的写「2024 年度合并及公司利润表(续)」，茅台写「合并利润表」——只认
    完整小节标题，不认附注里顺带出现的「…利润表中…」。
    """
    title = norm(raw)
    # 先去「2024 年度 / 2024年」，再清序号；顺序反了会把年份数字当序号吃掉。
    title = re.sub(r"^\d{4}\s*年度?\s*", "", title)
    title = re.sub(r"^[\d一二三四五六七八九、.（()）\s]+", "", title)
    title = re.sub(r"[\s(（]*续[\s)）]*$", "", title)
    title = title.replace("及公司", "").replace("及母公司", "")
    m = re.fullmatch(r"((?:合并|母公司)(?:利润表|现金流量表))", title)
    return m[1] if m else None


def statement_sections(document) -> list[dict]:
    markers = []
    for index, page in enumerate(document):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                raw = "".join(span["text"] for span in line["spans"])
                title = _statement_title(raw)
                if title:
                    markers.append({"title": title, "page": index, "bbox": box(line["bbox"])})
    markers.sort(key=lambda item: (item["page"], item["bbox"][1]))
    for i, marker in enumerate(markers):
        marker["end"] = markers[i + 1] if i + 1 < len(markers) else None
    return markers


def corroborate_scope(document, sections: list[dict], fact: dict) -> dict | None:
    wanted = "合并利润表" if fact["metric"] in PARENT_METRICS or fact["metric"] in ("revenue", "total_revenue") \
        else "合并现金流量表"
    alias = METRICS[fact["metric"]][1][0]
    # 报表原文金额常是千元/万元；旁证按**归一到元**后的值去原文搜。
    value = decimal(fact.get("normalized_value"))
    if value is None:
        value = decimal(fact["value"])
        if value is not None and fact.get("unit") and fact["unit"] != "元":
            try:
                value = convert(fact["value"], fact["unit"], "元")
            except ValueError:
                return None
    if value is None:
        return None
    # 原文按报表单位印（元/千元/百万元），归一到元后位数对不上会搜不到
    # ⇒ 归一值与常见缩放写法都搜（摘要表元、利润表千元/百万元，同一数字不同印法）。
    candidates: list[str] = []
    scales = [Decimal(1), Decimal(1000), Decimal(10000), Decimal(1000000)]
    for scale in scales:
        v = value / scale
        candidates += [format(v, ",.2f"), format(v, ".2f"), format(v, ",.0f")]
        if v == v.to_integral_value():
            candidates.append(format(int(v), ","))
    raw = decimal(fact.get("raw_value") if fact.get("raw_value") not in (None, "") else fact.get("value"))
    if raw is not None:
        candidates += [format(raw, ",.2f"), format(raw, ".2f"), format(raw, ",")]
        trimmed = format(raw, ",f")
        if trimmed.endswith(".00"):
            candidates.append(trimmed[:-3])
    for section in sections:
        if section["title"] != wanted:
            continue
        end = section["end"]
        last = min(section["page"] + 4, end["page"] if end else len(document) - 1)
        for index in range(section["page"], last + 1):
            page = document[index]
            hits = []
            for form in candidates:
                hits = page.search_for(form)
                if hits:
                    break
            for hit in hits:
                if index == section["page"] and hit.y0 <= section["bbox"][3]:
                    continue
                if end and index == end["page"] and hit.y1 >= end["bbox"][1]:
                    continue
                label_region = pymupdf.Rect(page.rect.x0, hit.y0 - 18, hit.x0 - 1, hit.y1 + 18)
                label = re.sub(r"\s+", "", page.get_text(clip=label_region))
                if alias in label:
                    return {"scope": "consolidated", "basis": "corroborated_current_value_in_statement",
                            "page": index + 1, "value_bbox": box(hit), "row_context": label,
                            "section_title": wanted, "section_page": section["page"] + 1,
                            "section_bbox": section["bbox"]}
    return None


def parse_table(page, table, table_index: int, material: dict, currency_note) -> list[dict]:
    cells = table_cells(table)
    above = page.get_text(clip=pymupdf.Rect(table.bbox[0], max(0, table.bbox[1] - 65),
                                           page.rect.x1, table.bbox[1]))
    above_compact = norm(above)
    unit_match = re.search(r"单位[:：]?(?:人民币)?(亿元|万元|元)", above_compact)
    page_currency = {"currency": "CNY", "page": page.number + 1, "quote": above_compact,
                     "basis": "table_unit_heading"} if "人民币" in above_compact else currency_note
    facts, seen_rows = [], set()
    for anchor in cells:
        if not amount_cell(anchor) or anchor["bbox"][2] - anchor["bbox"][0] < 25:
            continue
        if not header_for(anchor, cells, r"20\d{2}年(?:度)?"):
            continue
        label, label_bbox = label_for(anchor, cells)
        metric = metric_for(label)
        if not metric:
            continue
        row_key = (metric, anchor["bbox"][1])
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)
        row_cells = [c for c in cells if abs(c["bbox"][1] - anchor["bbox"][1]) < 1
                     and c["bbox"][0] >= label_bbox[2] - 1
                     and c["bbox"][2] - c["bbox"][0] >= 25]
        row_unit_m = re.search(r"[（(](?:人民币)?(亿元|万元|千元|百万元|元/股|元／股|元|%|％)[）)]", label)
        unit = row_unit_m[1] if row_unit_m else unit_match[1] if unit_match else None
        if unit:
            unit = {"％": "%", "元／股": "元/股"}.get(unit, unit)
        else:
            unit = row_unit(label) or METRIC_UNIT.get(metric)
        reported_yoy = None
        for cell in row_cells:
            rate_header = header_for(cell, cells, r".*本[年期].*比.*增减.*")
            rate = re.match(r"^(-?\d+(?:\.\d+)?)(%|％)?", cell["text"])
            if rate_header and rate and ("%" in rate_header["text"] or rate[2]):
                reported_yoy = {"value": rate[1], "unit": "%", "bbox": cell["bbox"],
                                "header": rate_header}
        for cell in row_cells:
            header = header_for(cell, cells, r"20\d{2}年(?:度)?")
            if not header:
                continue
            year = int(header["text"][:4])
            # 调整前/调整后只适用于比较年度；本年列永远是 as_reported。
            adjustment = header_for(cell, cells, r"调整前|调整后") if year < material["report_year"] else None
            adjustment_kind = {"调整前": "before", "调整后": "after"}.get(
                adjustment["text"] if adjustment else "", "as_reported")
            raw = cell["text"]
            if not amount_cell(cell) and raw not in {"", "-", "—", "–"}:
                continue
            value = _decimal_cell(raw)
            if unit in NON_AMOUNT_UNITS or unit is None:
                normalized, normalized_unit = value, unit
            else:
                normalized, normalized_unit = (convert(value, unit), "元") if value is not None else (None, "元")
            table_id = f"p{page.number + 1}_t{table_index}"
            identity = f"{material['document_id']}|{table_id}|{metric}|{year}|{adjustment_kind}"
            issues = []
            if unit is None:
                issues.append("unit_unknown")
            if not page_currency:
                issues.append("currency_unknown")
            if value is None:
                issues.append("value_missing")
            scope, scope_base = default_scope(metric)
            scope_evidence = ({**scope_base, "quote": label, "page": page.number + 1}
                              if scope_base else None)
            facts.append({
                "schema_version": 1, "evidence_id": sha256(identity.encode())[:24],
                "document_id": material["document_id"],
                "company_code": material["company_code"], "company_name": material["company_name"],
                "metric": metric, "metric_name": METRICS[metric][0], "original_label": label,
                "report_year": material["report_year"], "period_year": year,
                "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
                "period_kind": "annual", "duration_months": 12,
                "value": text(value), "raw_value": cell["raw"], "unit": unit,
                "normalized_value": text(normalized), "normalized_unit": normalized_unit,
                "currency": page_currency["currency"] if page_currency else None,
                "currency_evidence": page_currency,
                "scope": scope,
                "scope_evidence": scope_evidence,
                "adjustment": adjustment_kind,
                "comparison_group": material["document_id"] + "|" + table_id,
                "source_file": material["local_file"], "source_sha256": material["sha256"],
                "announcement_id": material["announcement_id"],
                "source_url": material["source_url"], "page": page.number + 1,
                "page_label": page.get_label(), "table_id": table_id,
                "table_bbox": box(table.bbox), "value_bbox": cell["bbox"],
                "label_bbox": label_bbox, "year_header": header,
                "adjustment_header": adjustment, "reported_yoy": reported_yoy,
                "has_adjustment_columns": any(c["text"] in {"调整前", "调整后"} for c in cells),
                "issues": issues, "extraction_method": "grid_cells_and_geometric_headers",
            })
    return facts
