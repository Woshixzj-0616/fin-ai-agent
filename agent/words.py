"""words 级表格几何引擎：按纵坐标聚行、按横坐标切列、按表头组认年份。

从 scripts/data/extract_metrics.py 合流过来的稳健版式解析层，两处共用一份逻辑。
关键做法（不针对某家公司硬编码版式）：
  1. 用 pymupdf 的 words 级取数（带坐标）。不能用 line 级 —— 年报里相邻两列的数字
     会被并成「一行文本」，整行就认不出数字了。
  2. 按纵坐标聚成「表格行」，行内按横坐标切「列」（间距大于阈值就切）。
  3. 表头行（含「20XX年 / 20XX年末」的格子）按纵向聚成「表头组」。
  4. 标签列常被折成多行，每个标签碎片只归给纵向最近的那个数字行，再拼回来。
  5. 指标名按「别名长优先」匹配，避免短名吃掉长名。
"""
from __future__ import annotations

import re

NUM_RE = re.compile(r"^\d+(\.\d+)?$")
YEAR_CELL_RE = re.compile(r"^((?:19|20)\d{2})(?:年1[-—–~至]12月|年12月31日|年末|年度|年)?$")
# 指标别名命中后，若后面紧跟这些词，说明命中的是「另一个更长的比率名」，不是本指标。
NEVER_AFTER = ("回报率", "周转率", "收益率", "增长率", "变动率", "费用率", "比率", "周转天数")
Y_TOL = 3.0
X_GAP = 6.0
HEADER_GROUP_GAP = 16.0
HEADER_MAX_ABOVE = 300.0
YEAR_COL_TOL = 48.0
LABEL_WIN = 62.0
LABEL_SIT = 12.0
HEADING_RE = re.compile(r"^[（(]?[一二三四五六七八九十]{1,3}[）)、]")
HEADING_KEYS = ("主要会计数据", "主要财务指标", "分季度")


def norm(s: str) -> str:
    return re.sub(r"[ \u3000\t]", "", s)


def to_num(s: str):
    """年报里的数字串 → float。括号表示负数；带 % 的比率也当数字（数值本身）。"""
    t = s.strip().replace(",", "").replace("，", "")
    neg = False
    for a, b in (("（", "）"), ("(", ")"), ("〔", "〕")):
        if t.startswith(a) and t.endswith(b):
            t = t[1:-1]
            neg = True
            break
    if t.endswith("%") or t.endswith("％"):
        t = t[:-1]
    for sign in ("-", "－", "−"):
        if t.startswith(sign):
            t = t[1:]
            neg = True
            break
    if not NUM_RE.match(t):
        return None
    return -float(t) if neg else float(t)


def page_rows(page, textpage=None) -> list[dict]:
    """把一页切成「表格行」，行内按横坐标切成「单元格」。每个 cell 带完整 bbox。"""
    words = page.get_text("words", textpage=textpage)
    items = [{"t": w[4], "x0": w[0], "x1": w[2], "y0": w[1], "y1": w[3],
              "yc": (w[1] + w[3]) / 2}
             for w in words if w[4].strip()]
    items.sort(key=lambda i: (i["yc"], i["x0"]))
    rows: list[dict] = []
    for it in items:
        hit = None
        for r in rows:
            if abs(r["yc"] - it["yc"]) <= Y_TOL:
                hit = r
                break
        if hit:
            hit["items"].append(it)
            hit["yc"] = sum(x["yc"] for x in hit["items"]) / len(hit["items"])
        else:
            rows.append({"yc": it["yc"], "items": [it]})
    rows.sort(key=lambda r: r["yc"])
    out = []
    for r in rows:
        r["items"].sort(key=lambda i: i["x0"])
        cells, cur = [], None
        for it in r["items"]:
            if cur is None or it["x0"] - cur["x1"] > X_GAP:
                cur = {"text": it["t"], "raw": it["t"], "x0": it["x0"], "x1": it["x1"],
                       "y0": it["y0"], "y1": it["y1"], "yc": it["yc"]}
                cells.append(cur)
            else:
                cur["text"] += it["t"]
                cur["raw"] += it["t"]
                cur["x1"] = it["x1"]
                cur["y0"] = min(cur["y0"], it["y0"])
                cur["y1"] = max(cur["y1"], it["y1"])
        out.append({"yc": r["yc"], "cells": cells})
    return out


def year_header_groups(rows: list[dict], report_year: int):
    """把「年份格」按纵向聚成表头组，返回 [(组下沿 y, [(列中心 x, 财年, cell), ...]), ...]。"""
    marks = []
    for r in rows:
        for c in r["cells"]:
            m = YEAR_CELL_RE.match(norm(c["text"]))
            if m:
                marks.append((r["yc"], (c["x0"] + c["x1"]) / 2, int(m.group(1)), c))
    marks.sort(key=lambda t: t[0])
    groups = []
    for yc, xc, y, cell in marks:
        if groups and yc - groups[-1]["yc"] <= HEADER_GROUP_GAP:
            groups[-1]["yc"] = max(groups[-1]["yc"], yc)
            groups[-1]["marks"].append((xc, y, cell))
        else:
            groups.append({"yc": yc, "marks": [(xc, y, cell)]})
    out = []
    for g in groups:
        cols = []
        for xc, y, cell in sorted(g["marks"], key=lambda t: t[0]):
            if cols and xc - cols[-1][0] < 20.0:
                continue
            cols.append((xc, y, cell))
        years = [y for _, y, _ in cols]
        if len(cols) < 2 or len(set(years)) != len(years):
            continue
        if any(years[i] <= years[i + 1] for i in range(len(years) - 1)):
            continue
        if report_year not in years:
            continue
        out.append((g["yc"], cols))
    out.sort(key=lambda t: t[0])
    return out


def heading_ys(rows: list[dict]) -> list[float]:
    """小节标题所在的纵坐标。防止「张冠李戴」借到别的小节表头。"""
    out = []
    for r in rows:
        t = norm("".join(c["text"] for c in r["cells"]))
        if not t:
            continue
        if any(k in t for k in HEADING_KEYS) or (HEADING_RE.match(t) and len(t) <= 30):
            out.append(r["yc"])
    return out


def tail_carry(rows: list[dict], report_year: int):
    """上一页那张表的年份表头，给跨页续表用。"""
    groups = year_header_groups(rows, report_year)
    if not groups:
        return None
    heads = heading_ys(rows)
    g = groups[-1]
    if any(hy > g[0] for hy in heads):
        return None
    below = [r for r in rows if r["yc"] > g[0] and any("num" in c for c in r["cells"])]
    if not below:
        return None
    return g[1]


def match_metric_at(text: str, aliases: list[tuple[str, str, int]]):
    """按「别名长优先」找指标，返回 (metric_key, 命中位置)。aliases: [(别名, key, 序)]"""
    if not text:
        return None, -1
    for alias, metric, _ in aliases:
        pos = text.find(alias)
        if pos < 0:
            continue
        if text[pos + len(alias):].startswith(NEVER_AFTER):
            continue
        return metric, pos
    return None, -1


UNIT_KEYS = (("百万", "百万元"), ("亿", "亿元"), ("万", "万元"), ("千", "千元"),
             ("元/股", "元/股"), ("元／股", "元/股"), ("元", "元"),
             ("%", "%"), ("％", "%"))
UNIT_RE = re.compile(r"单位[:：]?(.{0,10})")
AMOUNT_UNITS = ("百万元", "亿元", "万元", "千元", "元")
PAREN_RE = re.compile(r"[（(]([^（）()]{1,14})[）)]")
# 这几个指标的单位是「指标本身决定的」，不该靠版式猜。
METRIC_UNIT = {"weighted_roe": "%", "deducted_weighted_roe": "%",
               "basic_eps": "元/股", "diluted_eps": "元/股",
               "deducted_basic_eps": "元/股", "book_value_per_share": "元/股"}


def classify_unit(tail: str):
    for key, val in UNIT_KEYS:
        if key in tail:
            return val
    return None


def declared_unit(rows: list[dict]):
    for r in rows:
        joined = norm("".join(c["text"] for c in r["cells"]))
        m = UNIT_RE.search(joined)
        if m:
            u = classify_unit(m.group(1))
            if u:
                return u
    return None


def row_unit(text: str):
    for m in PAREN_RE.finditer(text):
        u = classify_unit(m.group(1))
        if u in AMOUNT_UNITS:
            return u
    return None


def common_label_unit(rows: list[dict]):
    from collections import Counter
    counts: Counter = Counter()
    for r in rows:
        u = row_unit(norm("".join(c["text"] for c in r["cells"])))
        if u:
            counts[u] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]
