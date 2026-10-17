"""words 级年度摘要表与利润表补抽。"""
from __future__ import annotations

import re

from finance import NON_AMOUNT_UNITS, convert, text
from materials import sha256
from words import (
    HEADER_MAX_ABOVE,
    LABEL_SIT,
    METRIC_UNIT,
    YEAR_COL_TOL,
    common_label_unit,
    declared_unit,
    heading_ys,
    match_metric_at,
    norm,
    page_rows,
    row_unit,
    tail_carry,
    to_num,
    year_header_groups,
)

from .catalog import ALIASES, METRICS, default_scope
from .grid import _decimal_cell, cell_bbox

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
        return t.endswith(("%", "％")) or ("." in t and abs(v) < 200)

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
                if t.endswith(("%", "％")):
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
