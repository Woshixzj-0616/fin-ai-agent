"""从文字层年报提取财务证据：words 级稳健解析 + 表格线网格解析双通道。

合流口径（2026-10-02）：
  - words 级（words.py）为主路径 —— 主仓 extract_metrics.py 那套版式解析，70/70 稳健；
  - 表格线网格（find_tables）为回退 —— 保留 bbox/调整列/披露同比的精细字段，护住 24 条 gold；
  - 指标目录 4 → 14，另从「合并利润表」补 `营业总收入`（口径陷阱指标，摘要表常不列）。
证据 schema 不变：value 可计算字符串、normalized 统一为元、每条带页码与 bbox。
"""
from __future__ import annotations

import re
from decimal import Decimal

import pymupdf

from materials import Run, sha256, within
from finance import NON_AMOUNT_UNITS, convert, decimal, text
from words import (
    AMOUNT_UNITS, HEADER_MAX_ABOVE, LABEL_SIT, METRIC_UNIT, PAREN_RE,
    YEAR_CELL_RE, YEAR_COL_TOL,
    classify_unit, common_label_unit, declared_unit, heading_ys,
    match_metric_at, norm, page_rows, row_unit, tail_carry, to_num,
    year_header_groups,
)

# 指标目录：key → (中文名, 别名列表)。别名按「长的在前」写，避免短名吃掉长名。
# 前 4 个 key 是 gold 参考答案在用的，**不能改名**。
# 别名同时覆盖 PDF 标签与口语问法（「营收」「加权ROE」），抽取与问答共用一份目录。
METRICS: dict[str, tuple[str, list[str]]] = {
    "total_revenue": ("营业总收入", ["营业总收入"]),
    "revenue": ("营业收入", ["营业收入", "营收"]),
    "parent_net_profit": ("归母净利润", [
        "归属于上市公司股东的净利润", "归属于母公司股东的净利润",
        "归属于母公司所有者的净利润", "归属于本行股东的净利润",
        "归属于本公司股东的净利润",
        "归母净利润", "归母净利"]),
    "adjusted_parent_net_profit": ("扣非归母净利润", [
        "归属于上市公司股东的扣除非经常性损益的净利润",
        "归属于母公司股东的扣除非经常性损益的净利润",
        "归属于母公司所有者的扣除非经常性损益的净利润",
        "扣除非经常性损益后的归属于上市公司股东的净利润",
        "扣除非经常性损益后归属于上市公司股东的净利润",
        "扣除非经常性损益后归属于母公司股东的净利润",
        "扣除非经常性损益后归属于本行股东的净利润",
        "归属于本公司股东的扣除非经常性损益的净利润",
        "归属于母公司股东的扣除非经常性损益后的净利润",
        "扣除非经常性损益后归属于本公司股东的净利润",
        "扣非归母净利润", "扣非归母净利", "扣非净利润", "扣非净利"]),
    "operating_cash_flow": ("经营现金流净额", [
        "经营活动产生的现金流量净额", "经营现金流净额", "经营活动现金流量净额"]),
    "basic_eps": ("基本每股收益", ["基本每股收益", "每股收益"]),
    "diluted_eps": ("稀释每股收益", ["稀释每股收益"]),
    "deducted_basic_eps": ("扣非基本每股收益", [
        "扣除非经常性损益后的基本每股收益", "扣非基本每股收益", "扣非每股收益", "扣非EPS"]),
    "weighted_roe": ("加权平均净资产收益率", [
        "加权平均净资产收益率", "净资产收益率（加权平均）", "净资产收益率(加权平均)",
        "加权ROE", "净资产收益率"]),
    "deducted_weighted_roe": ("扣非加权平均净资产收益率", [
        "扣除非经常性损益后的加权平均净资产收益率",
        "扣非加权平均净资产收益率", "扣非加权ROE", "扣非ROE"]),
    "total_assets": ("总资产", ["总资产", "资产总额", "资产总计"]),
    "parent_equity": ("归母净资产", [
        "归属于上市公司股东的净资产", "归属于母公司股东的净资产",
        "归属于母公司所有者权益", "归属于上市公司股东的所有者权益",
        "归属于本行股东权益", "归属于母公司股东权益", "归属于本公司股东权益",
        "归属于本行股东的净资产", "归属于本公司股东的净资产",
        "归母净资产", "归母权益"]),
    "book_value_per_share": ("每股净资产", [
        "归属于上市公司股东的每股净资产", "归属于母公司股东的每股净资产",
        "归属于上市公司普通股股东的每股净资产", "归属于本行普通股股东的每股净资产",
        "每股净资产"]),
    "revenue_after_deduction": ("营业收入扣除后金额", [
        "营业收入扣除后金额", "扣除后营业收入"]),  # 蓝筹样本无此行（退市风险警示股才有），保留给现场新数据
}

# 可数值裁决子集：check_claim 能对这 8 个出确定性对错；其余只做取数/溯源。
ADJUDICABLE = frozenset({
    "revenue", "total_revenue", "parent_net_profit", "adjusted_parent_net_profit",
    "operating_cash_flow", "basic_eps", "weighted_roe", "total_assets",
})

# 别名长优先，同长按目录序 —— 「营业总收入」先于「营业收入」，「扣非基本每股收益」先于「基本每股收益」。
ALIASES: list[tuple[str, str, int]] = []
for _idx, (_key, (_name, _names)) in enumerate(METRICS.items()):
    for _alias in _names:
        ALIASES.append((_alias, _key, _idx))
ALIASES.sort(key=lambda t: (-len(t[0]), t[2]))

# 抽不出来就报错的指标（gold 与核查器的最小集合）；其余缺了只记录，不阻断。
REQUIRED = ("revenue", "parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow")
PARENT_METRICS = {"parent_net_profit", "adjusted_parent_net_profit", "parent_equity"}
CONSOLIDATED_METRICS = {"revenue", "operating_cash_flow", "total_revenue",
                        "revenue_after_deduction", "total_assets"}


def default_scope(metric: str) -> tuple[str, dict | None]:
    """项目默认上下文：利润/权益类归母、收入/现金流/资产合并口径。旁证成功后再升级为 corroborated。"""
    if metric in PARENT_METRICS:
        return "parent_shareholders", {"basis": "explicit_metric_label"}
    if metric in CONSOLIDATED_METRICS:
        return "consolidated", {"basis": "project_default_consolidated"}
    return "unknown", None


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


# ─────────────────────────── words 级主路径 ───────────────────────────

# 折行标签的搜索窗与「同处一行」容差见 words.py；这里只管取数。
UNIT_IN_LABEL_RE = re.compile(r"[（(](?:人民币)?(亿元|万元|千元|百万元|元)[）)]")


def _emit_words_fact(*, material, page, metric, label, label_fragments, raw, value,
                     unit, year, year_cell, adjustment_cell, reported_yoy_cell,
                     value_cell, table_rows, page_currency, method):
    """把 words 级解析出的一格，落成一条与网格路径同 schema 的证据。"""
    if unit in NON_AMOUNT_UNITS or unit is None:
        normalized = value
        normalized_unit = unit
    else:
        normalized = convert(value, unit) if value is not None else None
        normalized_unit = "元"
    table_id = f"p{page.number + 1}_w"
    adjustment_kind = {"调整前": "before", "调整后": "after"}.get(
        adjustment_cell["text"] if adjustment_cell else "", "as_reported")
    identity = f"{material['document_id']}|{table_id}|{metric}|{year}|{adjustment_kind}"
    scope, scope_base = default_scope(metric)
    scope_evidence = ({**scope_base, "quote": label, "page": page.number + 1}
                      if scope_base else None)
    issues = []
    if unit is None:
        issues.append("unit_unknown")
    if not page_currency:
        issues.append("currency_unknown")
    if value is None:
        issues.append("value_missing")
    label_bbox = []
    if label_fragments:
        label_bbox = [min(f["x0"] for f in label_fragments), min(f["y0"] for f in label_fragments),
                      max(f["x1"] for f in label_fragments), max(f["y1"] for f in label_fragments)]
        label_bbox = [round(float(n), 3) for n in label_bbox]
    parent_metric = metric in PARENT_METRICS
    row_y = value_cell["yc"]
    table_bbox = [round(float(min(c["x0"] for r in table_rows for c in r["cells"])), 3),
                  round(float(min(c["y0"] for r in table_rows for c in r["cells"])), 3),
                  round(float(max(c["x1"] for r in table_rows for c in r["cells"])), 3),
                  round(float(max(c["y1"] for r in table_rows for c in r["cells"])), 3)]
    return {
        "schema_version": 1, "evidence_id": sha256(identity.encode())[:24],
        "document_id": material["document_id"],
        "company_code": material["company_code"], "company_name": material["company_name"],
        "metric": metric, "metric_name": METRICS[metric][0], "original_label": label,
        "report_year": material["report_year"], "period_year": year,
        "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
        "period_kind": "annual", "duration_months": 12,
        "value": text(value), "raw_value": raw, "unit": unit,
        "normalized_value": text(normalized), "normalized_unit": normalized_unit,
        "currency": page_currency["currency"] if page_currency else None,
        "currency_evidence": page_currency,
        "scope": scope, "scope_evidence": scope_evidence,
        "adjustment": adjustment_kind,
        "comparison_group": material["document_id"] + "|" + table_id,
        "source_file": material["local_file"], "source_sha256": material["sha256"],
        "announcement_id": material["announcement_id"],
        "source_url": material["source_url"], "page": page.number + 1,
        "page_label": page.get_label(), "table_id": table_id,
        "table_bbox": table_bbox, "value_bbox": cell_bbox(value_cell),
        "label_bbox": label_bbox,
        "year_header": {"text": year_cell["text"], "bbox": cell_bbox(year_cell)},
        "adjustment_header": ({"text": adjustment_cell["text"], "bbox": cell_bbox(adjustment_cell)}
                              if adjustment_cell else None),
        "reported_yoy": reported_yoy_cell,
        "has_adjustment_columns": any(
            c["text"] in {"调整前", "调整后"} for r in table_rows for c in r["cells"]),
        "issues": issues, "extraction_method": method,
    }


def _adjustment_cell_for(value_cell: dict, rows: list[dict]) -> dict | None:
    """数值列上方最近的「调整前/调整后」表头格。"""
    xc = (value_cell["x0"] + value_cell["x1"]) / 2
    best = None
    for r in rows:
        for c in r["cells"]:
            if c["text"] in {"调整前", "调整后"} and c["y1"] <= value_cell["y0"] + 1:
                cx = (c["x0"] + c["x1"]) / 2
                if abs(cx - xc) <= YEAR_COL_TOL * 1.5:
                    d = value_cell["y0"] - c["y1"]
                    if best is None or d < best[0]:
                        best = (d, c)
    return best[1] if best else None


def _reported_yoy_for(row: dict, rows: list[dict], label: str) -> dict | None:
    """披露同比：表头带「比…增减」或 % 的列，行内取百分数。

    年报里标签常折行，同比百分数可能落在**邻近行**（扣非那行的 19.05 就在金额行上面一行），
    所以要在 |Δyc| ≤ LABEL_SIT 的行里一起找。
    """
    def is_rate(t: str, v) -> bool:
        if v is None or t in {"-", "—", "–"}:
            return False
        return t.endswith("%") or t.endswith("％") or ("." in t and abs(v) < 200)

    # 找表头：任意上方行里含「增减」或「比…年」的格子
    yoy_headers = []
    # 年份/调整列头：用于排除「值被误判成同比」（如 49.93 落在调整前列下）
    value_headers = []
    for r in rows:
        if r["yc"] >= row["yc"]:
            continue
        for h in r["cells"]:
            ht = norm(h["text"])
            if "增减" in ht or ("比" in ht and ("上年" in ht or "同期" in ht or "年" in ht)):
                yoy_headers.append(h)
            elif re.match(r"20\d{2}年", ht) or ht in {"调整前", "调整后", "调整数"}:
                value_headers.append(h)
    if not yoy_headers:
        return None

    def under_value_header(xc: float) -> bool:
        return any(h["x0"] - 2 <= xc <= h["x1"] + 2 for h in value_headers)

    # 找数值：本行与邻近折行里的百分数格子，x 对齐某个 yoy 表头
    # 优先 % 显式带符号的；裸数字回退时跳过年份/调整列下的格子
    bare = None
    for r in rows:
        if abs(r["yc"] - row["yc"]) > LABEL_SIT:
            continue
        for c in r["cells"]:
            t = norm(c["text"])
            v = to_num(t)
            if not is_rate(t, v):
                continue
            xc = (c["x0"] + c["x1"]) / 2
            for h in yoy_headers:
                if not (h["x0"] - 2 <= xc <= h["x1"] + 2):
                    continue
                rate = re.match(r"^(-?\d+(?:\.\d+)?)", t)
                if not rate:
                    continue
                hit = {"value": rate[1], "unit": "%", "bbox": cell_bbox(c),
                       "header": {"text": h["text"], "bbox": cell_bbox(h)}}
                if t.endswith("%") or t.endswith("％"):
                    return hit
                if bare is None and not under_value_header(xc):
                    bare = hit
    return bare


def extract_via_words(page, report_year, material, currency_note, carry_cols=None):
    """words 级抽一页，返回 (facts, carry_cols)。"""
    rows = page_rows(page)
    for r in rows:
        for c in r["cells"]:
            v = to_num(c["text"])
            if v is not None:
                c["num"] = v
                c["xc"] = (c["x0"] + c["x1"]) / 2
    num_rows = [r for r in rows if sum(1 for c in r["cells"] if "num" in c) >= 2]
    if not num_rows:
        return [], None

    groups = year_header_groups(rows, report_year)
    if not groups and not carry_cols:
        return [], None
    heads = heading_ys(rows)
    page_declared = declared_unit(rows) or common_label_unit(rows)
    above_compact = norm(" ".join("".join(c["text"] for c in r["cells"]) for r in rows[:8]))
    page_currency = ({"currency": "CNY", "page": page.number + 1, "quote": above_compact,
                      "basis": "table_unit_heading"} if "人民币" in above_compact else currency_note)

    num_left = min(c["x0"] for r in num_rows for c in r["cells"] if "num" in c)
    frags = [c for r in rows for c in r["cells"]
             if "num" not in c and c["x0"] < num_left + 1.5 and 0 < len(c["text"]) <= 40]

    buckets = [[] for _ in num_rows]
    for f in frags:
        best = min(range(len(num_rows)), key=lambda i: abs(num_rows[i]["yc"] - f["yc"]))
        buckets[best].append(f)
    for b in buckets:
        b.sort(key=lambda c: c["yc"])

    def label_options(i):
        own = buckets[i]
        out = [own]
        if i + 1 < len(buckets):
            base = num_rows[i + 1]["yc"]
            for d in (1, 2):
                nxt = buckets[i + 1][:d]
                if len(nxt) == d and all(abs(f["yc"] - base) > LABEL_SIT for f in nxt):
                    out.append(own + nxt)
        if i > 0:
            base = num_rows[i - 1]["yc"]
            for d in (1, 2):
                prev = buckets[i - 1][-d:]
                if len(prev) == d and all(abs(f["yc"] - base) > LABEL_SIT for f in prev):
                    out.append(prev + own)
        return out

    facts = []
    found_metrics: dict[str, int] = {}   # metric -> 行号，供表序推断用
    unmatched_rows: list[tuple[int, str, dict]] = []  # (行号, 标签, 行)
    for i, row in enumerate(num_rows):
        strict = metric = chosen_frags = None
        for fs in label_options(i):
            text_joined = norm("".join(f["text"] for f in fs))
            m, pos = match_metric_at(text_joined, ALIASES)
            if m and m not in found_metrics:
                strict, metric, chosen_frags = text_joined[pos:], m, fs
                break
        if metric is None:
            raw_label = norm("".join(f["text"] for f in buckets[i]))
            if raw_label:
                unmatched_rows.append((i, raw_label, row))
            continue
        found_metrics[metric] = i
        label = strict
        above = [g for g in groups if 2.0 < row["yc"] - g[0] <= HEADER_MAX_ABOVE
                 and not any(g[0] < hy < row["yc"] for hy in heads)]
        if above:
            cols = above[-1][1]
        elif carry_cols and not any(hy < row["yc"] for hy in heads):
            cols = carry_cols
        else:
            cols = None
        nums = [c for c in row["cells"] if "num" in c]
        row_unit_val = (METRIC_UNIT.get(metric) or row_unit(strict)
                        or row_unit(label) or page_declared)
        yoy_cell = _reported_yoy_for(row, rows, label)
        # 逐个数值格定年份与调整列 —— 同一财年可能并排「调整前/调整后」两列，都要留。
        yoy_xc = ((yoy_cell["bbox"][0] + yoy_cell["bbox"][2]) / 2) if yoy_cell else None
        for value_cell in nums:
            if yoy_xc is not None and abs(value_cell["xc"] - yoy_xc) <= YEAR_COL_TOL / 2:
                continue  # 披露同比列，不是金额
            yr = year_cell = None
            best_d = None
            for xc, y, yc in cols or []:
                d = abs(value_cell["xc"] - xc)
                if d <= YEAR_COL_TOL and (best_d is None or d < best_d):
                    best_d, yr, year_cell = d, y, yc
            if yr is None:
                continue
            # 调整前/调整后只适用于比较年度；本年列永远是 as_reported。
            adj = _adjustment_cell_for(value_cell, rows) if yr < material["report_year"] else None
            facts.append(_emit_words_fact(
                material=material, page=page, metric=metric, label=label,
                label_fragments=chosen_frags or [], raw=value_cell["raw"],
                value=_decimal_cell(value_cell["text"]), unit=row_unit_val,
                year=yr, year_cell=year_cell, adjustment_cell=adj,
                reported_yoy_cell=yoy_cell, value_cell=value_cell,
                table_rows=rows, page_currency=page_currency,
                method="words_geometry"))

    # ── 标签残缺时的表序推断 ──
    # 摘要表固定「归母净利润 → 扣非归母净利润」相邻。个别年报（恒瑞 2022）PDF 文字层
    # 把扣非标签截断成「归属于上市公司」，别名匹配吃不到。此时若归母已命中、扣非仍缺，
    # 且紧随其后有一行标签以「归属于」开头却没匹配上 ⇒ 推断为扣非，**必须打 issues 标记**。
    PARENT_PREFIX = ("归属于上市公司", "归属于母公司", "归属于本行", "归属于本公司")
    if ("parent_net_profit" in found_metrics
            and "adjusted_parent_net_profit" not in found_metrics):
        pnp_idx = found_metrics["parent_net_profit"]
        for idx, raw_label, row in unmatched_rows:
            if idx <= pnp_idx or not raw_label.startswith(PARENT_PREFIX):
                continue
            strict = raw_label
            row_unit_val = (METRIC_UNIT.get("adjusted_parent_net_profit")
                            or row_unit(strict) or page_declared)
            yoy_cell = _reported_yoy_for(row, rows, strict)
            above = [g for g in groups if 2.0 < row["yc"] - g[0] <= HEADER_MAX_ABOVE
                     and not any(g[0] < hy < row["yc"] for hy in heads)]
            cols = above[-1][1] if above else (carry_cols if not any(
                hy < row["yc"] for hy in heads) else None)
            yoy_xc = ((yoy_cell["bbox"][0] + yoy_cell["bbox"][2]) / 2) if yoy_cell else None
            for value_cell in [c for c in row["cells"] if "num" in c]:
                if yoy_xc is not None and abs(value_cell["xc"] - yoy_xc) <= YEAR_COL_TOL / 2:
                    continue
                yr = year_cell = None
                best_d = None
                for xc, y, yc in cols or []:
                    d = abs(value_cell["xc"] - xc)
                    if d <= YEAR_COL_TOL and (best_d is None or d < best_d):
                        best_d, yr, year_cell = d, y, yc
                if yr is None:
                    continue
                adj = _adjustment_cell_for(value_cell, rows) if yr < material["report_year"] else None
                fact = _emit_words_fact(
                    material=material, page=page, metric="adjusted_parent_net_profit",
                    label=strict + "（标签文字层残缺，按表序推断）",
                    label_fragments=[c for c in row["cells"] if c.get("num") is None],
                    raw=value_cell["raw"], value=_decimal_cell(value_cell["text"]),
                    unit=row_unit_val, year=yr, year_cell=year_cell, adjustment_cell=adj,
                    reported_yoy_cell=yoy_cell, value_cell=value_cell,
                    table_rows=rows, page_currency=page_currency,
                    method="words_order_inference")
                fact["issues"].append("label_truncated_inferred_by_row_order")
                facts.append(fact)
            if any(f["metric"] == "adjusted_parent_net_profit" for f in facts):
                break
    return facts, tail_carry(rows, report_year)


def extract_words_document(document, material: dict, currency_note) -> list[dict]:
    """扫全篇主要会计数据页，words 级合并提取。"""
    merged: dict[tuple, dict] = {}
    carry, prev_used = None, False
    for i in range(len(document)):
        content = document[i].get_text()
        hit = ("主要会计数据" in content) or ("主要财务指标" in content)
        if not hit and not prev_used:
            carry = None
            continue
        found, carry = extract_via_words(document[i], material["report_year"],
                                         material, currency_note, carry if prev_used else None)
        if not found:
            prev_used = False
            continue
        prev_used = True
        for rec in found:
            key = (rec["metric"], rec["period_year"], rec["adjustment"])
            if key not in merged:
                merged[key] = rec
    return list(merged.values())


# ─────────────────────────── 营业总收入：合并利润表补抽 ───────────────────────────

TOTAL_REV_LABEL_RE = re.compile(r"^(?:一、)?营业总收入")


def extract_total_revenue(document, material: dict, currency_note) -> list[dict]:
    """从合并利润表补 `营业总收入`。摘要表常不列这一行（茅台 2024 差营业收入 32.4 亿）。"""
    facts = []
    for index, page in enumerate(document):
        content = norm(page.get_text())
        if "营业总收入" not in content or "利润表" not in content:
            continue
        rows = page_rows(page)
        for r in rows:
            for c in r["cells"]:
                c["num"] = to_num(c["text"])
                if c["num"] is not None:
                    c["xc"] = (c["x0"] + c["x1"]) / 2
        # 标签格：整格以「营业总收入」开头（含「一、营业总收入」）
        label_cell = None
        for r in rows:
            for c in r["cells"]:
                t = norm(c["text"])
                if TOTAL_REV_LABEL_RE.match(t) and to_num(t) is None:
                    label_cell = c
                    break
            if label_cell:
                break
        if not label_cell:
            continue
        num_row = next((r for r in rows if r["yc"] >= label_cell["yc"] - 2
                        and sum(1 for c in r["cells"] if c.get("num") is not None) >= 1
                        and abs(r["yc"] - label_cell["yc"]) <= 12), None)
        if not num_row:
            continue
        nums = [c for c in num_row["cells"] if c.get("num") is not None]
        if not nums:
            continue
        # 年份表头：利润表常见「2024年度/2023年度」或「本期/上期」
        groups = year_header_groups(rows, material["report_year"])
        yoy = None
        if groups:
            cols = min(groups, key=lambda g: abs(g[0] - label_cell["yc"]))[1]
            year_map = [(yr, cell) for _, yr, cell in cols]
        else:
            # 重述附注也可能出现「合并利润表」，但只列上年调整前后。
            # 没有可确认年份时，仅接受明确的本期/上期表头，不能把第一列硬认作本年。
            nearby_headers = [norm("".join(c["text"] for c in r["cells"])) for r in rows
                              if 0 < label_cell["yc"] - r["yc"] < 100]
            if not any("本期" in h and "上期" in h for h in nearby_headers):
                continue
            # 本期/上期：按列序左→右 = 本期→上期
            year_map = []
            ordered = sorted(nums, key=lambda c: c["x0"])
            for k, cell in enumerate(ordered[:2]):
                yr = material["report_year"] - k
                year_map.append((yr, {"text": f"{yr}年度", "bbox": cell_bbox(cell),
                                      "x0": cell["x0"], "x1": cell["x1"],
                                      "y0": cell["y0"], "y1": cell["y1"]}))
        for yr, year_cell in year_map:
            xc = (year_cell["x0"] + year_cell["x1"]) / 2 if "x0" in year_cell else \
                (year_cell["bbox"][0] + year_cell["bbox"][2]) / 2
            best = None
            for c in nums:
                d = abs(c["xc"] - xc)
                if best is None or d < best[0]:
                    best = (d, c)
            if best is None:
                continue
            value_cell = best[1]
            unit = row_unit(norm(label_cell["text"])) or declared_unit(rows) or "元"
            facts.append(_emit_words_fact(
                material=material, page=page, metric="total_revenue",
                label=norm(label_cell["text"]), label_fragments=[label_cell],
                raw=value_cell["raw"], value=_decimal_cell(value_cell["text"]), unit=unit,
                year=yr, year_cell=year_cell, adjustment_cell=None,
                reported_yoy_cell=yoy, value_cell=value_cell,
                table_rows=rows, page_currency=currency_note,
                method="income_statement_total_revenue"))
    return facts


# ─────────────────────────── 统一入口 ───────────────────────────

def _apply_scope(document, sections, facts, material):
    """对 revenue / total_revenue / operating_cash_flow 做合并口径旁证。"""
    for metric in ("revenue", "total_revenue", "operating_cash_flow"):
        current = next((f for f in facts if f["metric"] == metric
                        and f["period_year"] == material["report_year"]
                        and f["adjustment"] == "as_reported"), None)
        corroboration = corroborate_scope(document, sections, current) if current else None
        for fact in facts:
            if fact["metric"] != metric:
                continue
            if corroboration:
                fact["scope"] = "consolidated"
                fact["scope_evidence"] = {
                    **corroboration, "application": "same_summary_metric_row",
                    "confirmed_current_evidence_id": current["evidence_id"]}
            elif "scope_unknown" not in fact["issues"]:
                fact["issues"].append("scope_unknown")


def _dedupe(facts: list[dict], run=None) -> list[dict]:
    """同 (metric, period_year, adjustment) 只留一条；两通道都有时优先网格路径（护 gold），
    但缺字段要从输家补 —— 宁可字段更全（unit / reported_yoy / adjustment_header / 坐标框）。"""
    order = {"grid_cells_and_geometric_headers": 0, "words_geometry": 1,
             "words_order_inference": 2, "income_statement_total_revenue": 3}
    best: dict[tuple, dict] = {}
    for f in facts:
        key = (f["metric"], f["period_year"], f["adjustment"])
        old = best.get(key)
        if old is None:
            best[key] = f
            continue
        winner, loser = (old, f) if order.get(old["extraction_method"], 9) <= order.get(f["extraction_method"], 9) \
            else (f, old)
        if (winner.get("unit") and loser.get("unit") and
                decimal(winner.get("normalized_value")) != decimal(loser.get("normalized_value"))):
            winner["issues"] = list(dict.fromkeys(winner.get("issues", []) + ["conflicting_extraction_values"]))
        if run is not None:
            run.event("extraction_dedup", key=list(key), winner_evidence_id=winner["evidence_id"],
                      loser_evidence_id=loser["evidence_id"], rule="grid > words > order_inference > income_statement",
                      winner_method=winner["extraction_method"], loser_method=loser["extraction_method"],
                      winner_candidate={k: winner.get(k) for k in ("value", "unit", "normalized_value", "original_label", "page")},
                      loser_candidate={k: loser.get(k) for k in ("value", "unit", "normalized_value", "original_label", "page")},
                      conflict="conflicting_extraction_values" in winner.get("issues", []))
        if not winner.get("reported_yoy") and loser.get("reported_yoy"):
            winner["reported_yoy"] = loser["reported_yoy"]
        if not winner.get("adjustment_header") and loser.get("adjustment_header"):
            winner["adjustment_header"] = loser["adjustment_header"]
        # 网格路径常缺单位/标签框；words 认得出却因优先级被丢掉会假报 unit_unknown。
        if not winner.get("unit") and loser.get("unit"):
            winner["unit"] = loser["unit"]
            winner["unit_evidence"] = loser.get("unit_evidence") or {
                "unit": loser["unit"], "basis": loser.get("extraction_method")}
            if "unit_unknown" in winner.get("issues", []):
                winner["issues"] = [i for i in winner["issues"] if i != "unit_unknown"]
            # 单位补上后要按新单位重算归一值，否则旁证/比对仍按「原数=元」搜。
            raw_v = loser.get("value") or winner.get("value")
            if raw_v is not None and winner["unit"] not in NON_AMOUNT_UNITS:
                try:
                    winner["normalized_value"] = text(convert(raw_v, winner["unit"], "元"))
                    winner["normalized_unit"] = "元"
                except (ValueError, ArithmeticError):
                    pass
        if not winner.get("label_bbox") and loser.get("label_bbox"):
            winner["label_bbox"] = loser["label_bbox"]
        if not winner.get("original_label") and loser.get("original_label"):
            winner["original_label"] = loser["original_label"]
        best[key] = winner
    return list(best.values())


def _scanned_pdf_hint(document, texts: list[str]) -> str | None:
    """扫描版/无文字层：给出人话拒收原因，而不是抽到一半说版式不支持。"""
    pages = len(texts)
    if pages == 0:
        return "PDF 无法解析出任何页面，可能已损坏或加密，请换一份可读的年报 PDF。"
    nonempty = sum(1 for t in texts if len(norm(t)) >= 20)
    # 整本几乎无字 → 扫描件；只翻前 12 页加速
    sample = texts[:12]
    sample_ok = sum(1 for t in sample if len(norm(t)) >= 20)
    if nonempty == 0 or (sample and sample_ok == 0):
        return ("这份 PDF 几乎没有文字层（像是扫描版/影印件），当前版本不支持 OCR，"
                "无法抽取财务数据。请提供带文字层的年度报告 PDF；"
                "可在阅读器里选中表格里的数字来确认是否有文字层。")
    if nonempty < max(2, pages // 10):
        return (f"PDF 文字层过少（约 {nonempty}/{pages} 页可检索），更像影印件；"
                "若确认原档如此，请改用官方文字版年报。")
    return None


def extract_material(root, material: dict, run: Run) -> list[dict]:
    path = within(root, root / material["local_file"])
    blob = run.read(path)
    if sha256(blob) != material["sha256"]:
        raise ValueError("提取前指纹复核失败")
    with pymupdf.open(stream=blob, filetype="pdf") as document:
        texts = [page.get_text() for page in document]
        scanned = _scanned_pdf_hint(document, texts)
        if scanned:
            raise ValueError(scanned)
        from periods import report_period
        identity = report_period("".join(texts[:10]), material["report_year"])
        if identity["report_kind"] in {"quarter", "half"}:
            from interim import extract_interim
            return extract_interim(document, material, run)
        currency = document_currency(document, texts)

        # ── 通道 1：表格线网格（gold 口径） ──
        grid_facts: list[dict] = []
        grid_error = None
        try:
            candidates = []
            for index, content in enumerate(texts):
                compact = norm(content)
                if "主要会计数据" not in compact or "营业收入" not in compact:
                    continue
                page = document[index]
                for ti, table in enumerate(page.find_tables().tables):
                    facts = parse_table(page, table, ti, material, currency)
                    current = [f for f in facts if f["period_year"] == material["report_year"]]
                    count = len({f["metric"] for f in current})
                    if count:
                        candidates.append((count, facts))
            if candidates:
                best_count = max(count for count, _ in candidates)
                best = [facts for count, facts in candidates if count == best_count]
                signatures = {
                    tuple(sorted((f["metric"], f["period_year"], f["adjustment"], f["value"] or "")
                                 for f in facts)) for facts in best}
                if len(signatures) != 1:
                    raise ValueError("发现互相冲突的候选表，需要人工选择")
                grid_facts = best[0]
                keys = [(f["metric"], f["period_year"], f["adjustment"]) for f in grid_facts]
                if len(keys) != len(set(keys)):
                    raise ValueError("表头产生重复证据键，拒绝静默选列")
        except ValueError as exc:
            grid_error = str(exc)
            grid_facts = []

        # ── 通道 2：words 级（稳健） ──
        words_facts = extract_words_document(document, material, currency)

        # ── 通道 3：利润表补营业总收入 ──
        total_facts = extract_total_revenue(document, material, currency)

        for channel, channel_facts in (("grid", grid_facts), ("words", words_facts), ("income_statement", total_facts)):
            run.event("extraction_channel", document_id=material["document_id"], channel=channel,
                      count=len(channel_facts), evidence_ids=[f["evidence_id"] for f in channel_facts],
                      error=grid_error if channel == "grid" else None)
        facts = _dedupe(grid_facts + words_facts + total_facts, run)
        if not facts:
            raise ValueError(grid_error or (
                "未识别到「主要会计数据」摘要表。可能原因：①扫描版无文字层 ②版式过新/过偏 ③该 PDF 不是年度报告。"
                "请用阅读器打开确认表格内数字可选中复制，或换官方文字版年报。"))

        # 指标固有单位（元/股、%）兜底：版式没写也不该报 unit_unknown。
        for f in facts:
            if not f.get("unit") and f["metric"] in METRIC_UNIT:
                f["unit"] = METRIC_UNIT[f["metric"]]
                f["unit_evidence"] = {"unit": f["unit"], "basis": "metric_intrinsic_unit"}
                f["issues"] = [i for i in f.get("issues", []) if i != "unit_unknown"]

        sections = statement_sections(document)
        _apply_scope(document, sections, facts, material)

        # A 股年报默认人民币完整年度（项目口径）；页面/文档层没抓到币种声明时补默认，避免同比被「币种未明确」拦下。
        for f in facts:
            if not f.get("currency"):
                f["currency"] = "CNY"
                f["currency_evidence"] = {"currency": "CNY",
                                          "basis": "a_share_annual_report_default"}
                f["issues"] = [i for i in f["issues"] if i != "currency_unknown"]

        current_metrics = {f["metric"] for f in facts
                           if f["period_year"] == material["report_year"]
                           and f["adjustment"] in {"as_reported", "after"}}
        missing_required = [m for m in REQUIRED if m not in current_metrics]
        missing_optional = sorted(set(METRICS) - current_metrics)
        method_counts = {}
        for f in facts:
            method_counts[f["extraction_method"]] = method_counts.get(f["extraction_method"], 0) + 1
        run.event("extraction_completed", document_id=material["document_id"],
                  evidence_count=len(facts), missing_required=missing_required,
                  missing_current_metrics=missing_optional,
                  method_counts=method_counts,
                  needs_review=sum(bool(f["issues"]) for f in facts))
        if missing_required:
            raise ValueError("主要指标不完整：" + ",".join(missing_required))
        return facts
