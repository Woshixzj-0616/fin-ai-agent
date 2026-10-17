"""半年报/季报摘要表解析：按真实行列与期间表头取值，不按相邻行猜值。"""
from __future__ import annotations

import re

from finance import NON_AMOUNT_UNITS, convert, decimal, text
from materials import sha256
from periods import STOCK_METRICS, end_date, report_period
from words import HEADER_MAX_ABOVE, METRIC_UNIT, page_rows


def compact(raw):
    return re.sub(r"\s+", "", raw or "").replace("／", "/")


def disclosed_change(raw, header: dict, bbox, page: int):
    """增减列独立于数值列：保留原文类型、单位、精度和定位。"""
    value = compact(raw).replace("−", "-")
    title = compact(header.get("text"))
    if value in {"", "-", "—", "不适用"}:
        return None, "not_disclosed"
    points = "百分点" in value or "百分点" in title
    kind = "percentage_points" if points else "year_end" if re.search(r"上年度末|上年末", title) else "yoy" if re.search(r"同期|同比", title) else None
    match = re.fullmatch(r"(?:(增加|增长|减少|下降|降低))?([+-]?\d+(?:\.\d+)?)(?:个?百分点|[%％])?", value)
    if not kind or not match or (not points and not re.search(r"[%％]", title + value)):
        return None, "unresolved"
    number = decimal(match[2])
    if match[1] in {"减少", "下降", "降低"}:
        number = -abs(number)
    return {"value": text(number), "unit": "百分点" if points else "%", "comparison_kind": kind,
            "raw_text": raw, "raw_value": raw, "status": "ok",
            "decimals": max(0, -number.as_tuple().exponent),
            "bbox": bbox, "page": page, "header": header}, "extracted"

_EXPLICIT_RANGE = re.compile(
    r"(?:由)?((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日(?:起)?"
    r"\s*(?:至|—|－|-)\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日"
)
_EXPLICIT_AS_OF = re.compile(r"(?:于|截至)((?:19|20)\d{2})年(\d{1,2})月(\d{1,2})日")

def header_period(label: str, identity: dict, stock: bool) -> dict | None:
    label = compact(label)
    if re.search(r"比|增减|增长|变动|百分点", label):
        return None
    as_of = _EXPLICIT_AS_OF.search(label)
    if stock and as_of:
        year, month, day = map(int, as_of.groups())
        instant = f"{year}-{month:02d}-{day:02d}"
        return {"period_year": year, "period_start": instant, "period_end": instant,
                "period_kind": "instant", "duration_months": 0, "period_basis": "instant"}
    explicit_range = _EXPLICIT_RANGE.search(label)
    if explicit_range and not stock:
        start_year, start_month, start_day, end_year, end_month, end_day = explicit_range.groups()
        start_year = int(start_year)
        end_year = int(end_year or start_year)
        start_month, start_day, end_month, end_day = map(int, (start_month, start_day, end_month, end_day))
        from datetime import date as date_type
        try:
            start = date_type(start_year, start_month, start_day)
            end = date_type(end_year, end_month, end_day)
        except ValueError:
            return None
        if start > end:
            return None
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        kind = identity.get("report_kind") if identity.get("report_kind") in {"half", "quarter"} else (
            "half" if months == 6 else "quarter" if months == 3 else None)
        if kind is None:
            return None
        basis = "cumulative" if start.month == 1 and start.day == 1 else "single_quarter"
        return {"period_year": end.year, "period_start": start.isoformat(),
                "period_end": end.isoformat(), "period_kind": kind,
                "duration_months": months, "period_basis": basis}
    y, month = identity["report_year"], identity["report_month"]
    previous = bool(re.search(r"上年同期|上年年初至|上期", label))
    if stock:
        if re.search(r"上年度末|上年末|年初", label):
            y, month = y - 1, 12
        elif not re.search(r"本报告期末|本期末|期末|报告期末", label):
            return None
        end = end_date(y, month)
        return {"period_year": y, "period_start": end, "period_end": end,
                "period_kind": "instant", "duration_months": 0, "period_basis": "instant"}
    if ("期末" in label or "年度末" in label) and "年初至" not in label:
        return None
    if not re.search(r"本报告期|本期|上年同期|上期|年初至|1[-－—–至](?:3|6|9|12)月", label):
        return None
    if previous:
        y -= 1
    cumulative = "年初至" in label or identity["report_kind"] == "half" or month == 3
    start_month = 1 if cumulative else month - 2
    return {"period_year": y, "period_start": f"{y}-{start_month:02d}-01",
            "period_end": end_date(y, month),
            "period_kind": "half" if identity["report_kind"] == "half" else "quarter",
            "duration_months": month if cumulative else 3,
            "period_basis": "cumulative" if cumulative else "single_quarter"}


def extract_interim(document, material: dict, run) -> list[dict]:
    from extract import ALIASES, METRICS, box, default_scope, document_currency
    texts = [page.get_text() for page in document]
    identity = report_period("".join(texts[:10]), material["report_year"])
    if identity["report_kind"] not in {"half", "quarter"}:
        raise ValueError("未确认半年报/季报标题与期间")
    currency_note = document_currency(document, texts)
    facts, carry = [], None

    def metric_label(raw):
        label = compact(raw)
        clean = re.sub(r"[（(](?:人民币)?(?:亿元|万元|千元|百万元|元/股|元|%|％)[）)]$", "", label)
        return next((key for alias, key, _ in ALIASES if compact(alias) == clean), None)

    def emit(page, raw_label, raw_value, label_bbox, value_bbox, header, table_bbox, method, content,
             reported=None, disclosure_status="not_extracted"):
        metric = metric_label(raw_label)
        if not metric:
            return
        period = header_period(header["text"], identity, metric in STOCK_METRICS)
        if not period:
            return
        raw = compact(raw_value)
        if not re.fullmatch(r"[-+−]?\d[\d,，]*(?:\.\d+)?[%％]?|[（(][\d,，.]+[）)]|[-—]", raw):
            return
        unit_match = re.search(r"单位[:：]?(?:人民币)?(亿元|百万元|万元|千元|元)", compact(content))
        unit = METRIC_UNIT.get(metric) or (unit_match[1] if unit_match else None)
        label_unit = re.search(r"[（(](亿元|万元|元/股|元|%|％)[）)]", compact(raw_label))
        if label_unit:
            unit = label_unit[1].replace("％", "%")
        value = decimal(raw.rstrip("%％"))
        normalized = value if unit in NON_AMOUNT_UNITS or unit is None else convert(value, unit)
        currency = "CNY" if "人民币" in content else currency_note["currency"] if currency_note else None
        scope, scope_note = default_scope(metric)
        issues = ([] if unit else ["unit_unknown"]) + ([] if currency else ["currency_unknown"])
        if value is None:
            issues.append("value_missing")
        if scope == "unknown" and unit not in NON_AMOUNT_UNITS:
            issues.append("scope_unknown")
        ident = "|".join(map(str, (material["document_id"], metric, period["period_start"],
                                   period["period_end"], page.number+1, method, value_bbox)))
        facts.append({"schema_version": 2, "evidence_id": sha256(ident.encode())[:24],
                      "document_id": material["document_id"], "company_code": material["company_code"],
                      "company_name": material["company_name"], "report_year": material["report_year"],
                      "metric": metric, "metric_name": METRICS[metric][0], "original_label": raw_label,
                      **period, "value": text(value), "raw_value": raw, "unit": unit,
                      "normalized_value": text(normalized),
                      "normalized_unit": unit if unit in NON_AMOUNT_UNITS else "元" if unit else None,
                      "currency": currency, "currency_evidence": currency_note or {"basis": "page_currency_heading"},
                      "scope": scope, "scope_evidence": scope_note, "adjustment": "as_reported",
                      "comparison_group": material["document_id"] + "|" + period["period_basis"],
                      "source_file": material["local_file"], "source_sha256": material["sha256"],
                      "source_url": material["source_url"], "announcement_id": material["announcement_id"],
                      "page": page.number+1, "page_label": page.get_label(), "table_id": f"p{page.number+1}_interim",
                      "table_bbox": table_bbox, "value_bbox": value_bbox, "label_bbox": label_bbox,
                      "year_header": header, "reported_yoy": reported if period["period_year"] == identity["report_year"] else None,
                      "reported_change": reported if period["period_year"] == identity["report_year"] else None,
                      "disclosure_status": disclosure_status if period["period_year"] == identity["report_year"] else "not_applicable",
                      "comparison_kind": reported["comparison_kind"] if reported else
                      "year_end" if metric in STOCK_METRICS else
                      "percentage_points" if metric in {"weighted_roe", "deducted_weighted_roe"} else "yoy",
                      "adjustment_header": None,
                      "issues": issues, "extraction_method": method})

    for page, content in zip(document, texts):
        is_summary = any(s in compact(content) for s in (
            "主要会计数据", "主要财务指标", "主要财务数据"))
        continuation = carry if carry and carry["page"] == page.number - 1 else None
        if not is_summary and not continuation:
            carry = None
            continue
        grids = list(page.find_tables().tables)
        page_count, next_carry, recognized_grid = len(facts), None, False
        for table in grids:
            grid = table.extract()
            headers, rate_headers = {}, {}
            # 只允许紧邻上一页末尾显式表头、且列边界一致的续表。
            if continuation and table.rows and len(grid[0]) == continuation["columns"]:
                bounds = [tuple(round(v, 1) for v in (r[0], r[2])) if r else None for r in table.rows[0].cells]
                if bounds == continuation["bounds"] and not compact(grid[0][0]):
                    headers = continuation["headers"]
                    rate_headers = dict(continuation.get("rate_headers", {}))
                    for ci, previous_header in list(rate_headers.items()):
                        tail = grid[0][ci] if ci < len(grid[0]) else None
                        if tail:
                            rate_headers[ci] = {**previous_header, "text": previous_header["text"] + tail,
                                                "continuation_page": page.number + 1,
                                                "continuation_bbox": box(table.rows[0].cells[ci])}
                    recognized_grid = True
                    run.event("period_header_carry", page=page.number+1, source_page=page.number,
                              rule="adjacent_page_matching_column_boundaries", headers=headers)
            for ri, row in enumerate(grid):
                period_cells = {ci: {"text": label, "bbox": box(table.rows[ri].cells[ci])}
                                for ci, label in enumerate(row) if label and table.rows[ri].cells[ci]
                                and (header_period(label, identity, False) or header_period(label, identity, True))}
                if len(period_cells) >= 2:
                    headers = period_cells
                    rate_headers = {ci: {"text": label, "bbox": box(table.rows[ri].cells[ci]), "page": page.number+1}
                                    for ci, label in enumerate(row) if label and table.rows[ri].cells[ci]
                                    and ci not in period_cells and re.search(r"比|增减|增长|变动", compact(label))}
                    recognized_grid = True
                    if ri == len(grid)-1:
                        next_carry = {"page": page.number, "headers": headers, "rate_headers": rate_headers, "columns": len(row),
                                      "bounds": [tuple(round(v, 1) for v in (r[0], r[2])) if r else None
                                                 for r in table.rows[ri].cells]}
                    continue
                if not row or not metric_label(row[0]) or not headers:
                    continue
                reported, disclosure_status = None, "not_extracted"
                if len(rate_headers) == 1:
                    rate_col, rate_header = next(iter(rate_headers.items()))
                    if rate_col < len(row):
                        reported, disclosure_status = disclosed_change(row[rate_col], rate_header,
                            box(table.rows[ri].cells[rate_col]) if table.rows[ri].cells[rate_col] else None, page.number+1)
                elif rate_headers:
                    disclosure_status = "unresolved"
                for ci, header in headers.items():
                    if ci < len(row) and row[ci] and table.rows[ri].cells[ci]:
                        emit(page, row[0], row[ci], box(table.rows[ri].cells[0]), box(table.rows[ri].cells[ci]),
                             header, box(table.bbox), "interim_grid_headers", content, reported, disclosure_status)
        if is_summary and not recognized_grid:
            # 无网格：指标行与数值行须在同一 words 基线；折行标签仅拼接无数值的上一行。
            rows = page_rows(page)
            for ri, row in enumerate(rows):
                if len(row["cells"]) < 3:
                    continue
                label = row["cells"][0]
                raw_label = label["text"]
                if not metric_label(raw_label) and ri and len(rows[ri-1]["cells"]) == 1:
                    raw_label = rows[ri-1]["cells"][0]["text"] + raw_label
                if not metric_label(raw_label):
                    continue
                rates = []
                for cell in row["cells"][1:]:
                    cx = (cell["x0"] + cell["x1"]) / 2
                    headings = [c for prior in rows[:ri] for c in prior["cells"]
                                if 0 < cell["y0"] - c["y1"] < 150 and c["x0"]-6 <= cx <= c["x1"]+6]
                    anchors = [i for i,c in enumerate(headings) if re.search(r"比|增减|同比", c["text"])]
                    if anchors:
                        header = {"text": "".join(c["text"] for c in headings[anchors[-1]:]), "page": page.number+1}
                        rates.append(disclosed_change(cell["text"], header,
                            box([cell[k] for k in ("x0", "y0", "x1", "y1")]), page.number+1))
                reported, disclosure_status = rates[0] if len(rates) == 1 else (None, "unresolved" if rates else "not_extracted")
                for cell in row["cells"][1:]:
                    cx = (cell["x0"] + cell["x1"]) / 2
                    chunks = [c for prev in rows[:ri] for c in prev["cells"]
                              if c["y1"] < cell["y0"] and cell["y0"]-c["y1"] < HEADER_MAX_ABOVE
                              and c["x0"]-6 <= cx <= c["x1"]+6]
                    # 同一表头可能折成多行；取与数值行最近、纵向间距连续的整组。
                    header_candidates = [chunk for chunk in chunks if not re.fullmatch(
                        r"[-+−]?\d[\d,，]*(?:\.\d+)?(?:[%％]|pp)?|[（(][\d,，.]+[）)]",
                        compact(chunk["text"]))]
                    groups = []
                    for chunk in header_candidates:
                        if groups and chunk["y0"] - groups[-1][-1]["y1"] <= 25:
                            groups[-1].append(chunk)
                        else:
                            groups.append([chunk])
                    header_cells = groups[-1] if groups else []
                    if not any(re.search(
                            r"本报告期|上年同期|年初至|本期|上年度末|由(?:19|20)\d{2}年|于(?:19|20)\d{2}年",
                            chunk["text"]) for chunk in header_cells):
                        continue
                    header_text = "".join(chunk["text"] for chunk in header_cells)
                    header = {"text": header_text, "bbox": box([header_cells[0][k] for k in (
                        "x0", "y0", "x1", "y1")])}
                    emit(page, raw_label, cell["text"], box([label[k] for k in ("x0", "y0", "x1", "y1")]),
                         box([cell[k] for k in ("x0", "y0", "x1", "y1")]), header, box(page.rect),
                         "interim_words_headers", content, reported, disclosure_status)
        carry = next_carry
        run.event("extraction_channel", document_id=material["document_id"], channel="interim_headers",
                  page=page.number+1, identity=identity, evidence_ids=[f["evidence_id"] for f in facts[page_count:]])
    grouped = {}
    for fact in facts:
        grouped.setdefault((fact["metric"], fact["period_start"], fact["period_end"]), []).append(fact)
    winners = []
    for key, candidates in grouped.items():
        winner = candidates[0]
        disclosures = [f for f in candidates if f.get("reported_yoy")]
        if len({(f["reported_yoy"]["value"], f["reported_yoy"]["unit"], f["reported_yoy"]["comparison_kind"])
                for f in disclosures}) > 1:
            winner["issues"].append("conflicting_disclosed_changes")
        elif disclosures and not winner.get("reported_yoy"):
            winner.update(reported_yoy=disclosures[0]["reported_yoy"], reported_change=disclosures[0]["reported_yoy"], disclosure_status="extracted",
                          comparison_kind=disclosures[0]["comparison_kind"])
        if len({(f["normalized_value"], f["normalized_unit"]) for f in candidates}) > 1:
            winner["issues"].append("conflicting_extraction_values")
        winners.append(winner)
        run.event("extraction_dedup", key=list(key), winner_evidence_id=winner["evidence_id"],
                  candidate_evidence_ids=[f["evidence_id"] for f in candidates], issues=winner["issues"])
    if not winners:
        raise ValueError("未找到可确认期间表头的半年报/季报摘要数据；不按列位置猜测")
    run.event("extraction_completed", document_id=material["document_id"], evidence_count=len(winners),
              report_kind=identity["report_kind"], needs_review=sum(bool(f["issues"]) for f in winners))
    return winners
