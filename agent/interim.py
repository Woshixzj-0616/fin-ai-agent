"""半年报/季报摘要表解析：按真实行列与期间表头取值，不按相邻行猜值。"""
from __future__ import annotations

import re

from finance import NON_AMOUNT_UNITS, convert, decimal, text
from materials import sha256
from periods import STOCK_METRICS, end_date, report_period
from words import METRIC_UNIT, page_rows


def compact(raw):
    return re.sub(r"\s+", "", raw or "").replace("／", "/")


def header_period(label: str, identity: dict, stock: bool) -> dict | None:
    label = compact(label)
    if re.search(r"比|增减|增长|变动|百分点", label):
        return None
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

    def emit(page, raw_label, raw_value, label_bbox, value_bbox, header, table_bbox, method, content):
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
                      "year_header": header, "reported_yoy": None, "adjustment_header": None,
                      "issues": issues, "extraction_method": method})

    for page, content in zip(document, texts):
        is_summary = any(s in compact(content) for s in ("主要会计数据", "主要财务指标"))
        continuation = carry if carry and carry["page"] == page.number - 1 else None
        if not is_summary and not continuation:
            carry = None
            continue
        grids = list(page.find_tables().tables)
        page_count, next_carry, recognized_grid = len(facts), None, False
        for table in grids:
            grid = table.extract()
            headers = {}
            # 只允许紧邻上一页末尾显式表头、且列边界一致的续表。
            if continuation and table.rows and len(grid[0]) == continuation["columns"]:
                bounds = [tuple(round(v, 1) for v in (r[0], r[2])) if r else None for r in table.rows[0].cells]
                if bounds == continuation["bounds"] and not compact(grid[0][0]):
                    headers = continuation["headers"]
                    recognized_grid = True
                    run.event("period_header_carry", page=page.number+1, source_page=page.number,
                              rule="adjacent_page_matching_column_boundaries", headers=headers)
            for ri, row in enumerate(grid):
                period_cells = {ci: {"text": label, "bbox": box(table.rows[ri].cells[ci])}
                                for ci, label in enumerate(row) if label and table.rows[ri].cells[ci]
                                and (header_period(label, identity, False) or header_period(label, identity, True))}
                if len(period_cells) >= 2:
                    headers = period_cells
                    recognized_grid = True
                    if ri == len(grid)-1:
                        next_carry = {"page": page.number, "headers": headers, "columns": len(row),
                                      "bounds": [tuple(round(v, 1) for v in (r[0], r[2])) if r else None
                                                 for r in table.rows[ri].cells]}
                    continue
                if not row or not metric_label(row[0]) or not headers:
                    continue
                for ci, header in headers.items():
                    if ci < len(row) and row[ci] and table.rows[ri].cells[ci]:
                        emit(page, row[0], row[ci], box(table.rows[ri].cells[0]), box(table.rows[ri].cells[ci]),
                             header, box(table.bbox), "interim_grid_headers", content)
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
                for cell in row["cells"][1:]:
                    cx = (cell["x0"] + cell["x1"]) / 2
                    chunks = [c for prev in rows[:ri] for c in prev["cells"]
                              if c["y1"] < cell["y0"] and cell["y0"]-c["y1"] < 150
                              and c["x0"]-6 <= cx <= c["x1"]+6]
                    # 最近表头以及紧邻其下的折行部分；不吃进其他数值行。
                    anchors = [i for i, c in enumerate(chunks) if re.search(r"本报告期|上年同期|年初至|本期|上年度末", c["text"])]
                    if not anchors:
                        continue
                    anchor = anchors[-1]
                    header_text = "".join(c["text"] for c in chunks[anchor:])
                    header = {"text": header_text, "bbox": box([chunks[anchor][k] for k in ("x0", "y0", "x1", "y1")])}
                    emit(page, raw_label, cell["text"], box([label[k] for k in ("x0", "y0", "x1", "y1")]),
                         box([cell[k] for k in ("x0", "y0", "x1", "y1")]), header, box(page.rect),
                         "interim_words_headers", content)
        carry = next_carry
        run.event("extraction_channel", document_id=material["document_id"], channel="interim_headers",
                  page=page.number+1, identity=identity, evidence_ids=[f["evidence_id"] for f in facts[page_count:]])
    grouped = {}
    for fact in facts:
        grouped.setdefault((fact["metric"], fact["period_start"], fact["period_end"]), []).append(fact)
    winners = []
    for key, candidates in grouped.items():
        winner = candidates[0]
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
