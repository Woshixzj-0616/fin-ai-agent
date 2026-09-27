# -*- coding: utf-8 -*-
'''从年报 PDF 抽取「主要会计数据和财务指标」，保留来源定位（页码）。

关键做法（不针对某家公司硬编码版式）：
  1. 用 pymupdf 的 words 级取数（带坐标）。不能用 line 级 —— 年报里相邻两列的数字
     会被并成「一行文本」，整行就认不出数字了。
  2. 按纵坐标聚成「表格行」，行内按横坐标切「列」（间距大于阈值就切）。
  3. 表头行（含「20XX年 / 20XX年末」的格子）按纵向聚成「表头组」；一页里可以有多组
     （「主要会计数据」「期末数」「主要财务指标」三张子表的列坐标往往并不一致）。
     每个数据行往上看最近的那一组表头，就知道自己每个数字属于哪一年 —— 不这么做，
     子表之间的列错位会把「本年数」记成「上年数」，而且不报错。
  4. 表头里「本期比上年同期增减(%)」那一列没有年份格，这类数字自然落不到任何一年，丢弃。
  5. 数字列最左边那列的左边就是「标签列」；标签常被折成多行，每个标签碎片只归给
     纵向最近的那个数字行，再按纵坐标顺序拼回完整标签。
  6. 指标名按「别名长优先」匹配：「营业总收入」先于「营业收入」，
     「扣除非经常性损益后的基本每股收益」先于「基本每股收益」。
  7. 单位优先取行标签里的（如「营业收入（千元）」），其次取页面的「单位：元」声明。

运行：python scripts\\data\\extract_metrics.py
产出：
    data/extracted/financials.csv   长表，一行 = 一家公司 x 一个财年 x 一个指标
    data/extracted/metrics.csv      旧口径（4 个核心指标 x 本年列），保持向后兼容
    logs/extract_run.txt            每个文件命中/未命中明细
'''
import csv
import re
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data' / 'raw'
OUT = ROOT / 'data' / 'extracted'
LOG = ROOT / 'logs'

# 指标目录：一行 = 一个「要抽的指标」，别名按「长的在前」写，避免短名吃掉长名。
CATALOG = [
    ('营业总收入', ['营业总收入']),
    ('营业收入', ['营业收入']),
    ('归母净利润', ['归属于上市公司股东的净利润', '归属于母公司股东的净利润',
                    '归属于母公司所有者的净利润', '归属于本行股东的净利润',
                    '归属于本公司股东的净利润']),
    ('扣非归母净利润', ['归属于上市公司股东的扣除非经常性损益的净利润',
                        '归属于母公司股东的扣除非经常性损益的净利润',
                        '归属于母公司所有者的扣除非经常性损益的净利润',
                        '扣除非经常性损益后的归属于上市公司股东的净利润',
                        '扣除非经常性损益后归属于上市公司股东的净利润',
                        '扣除非经常性损益后归属于母公司股东的净利润',
                        '扣除非经常性损益后归属于本行股东的净利润',
                        '归属于本公司股东的扣除非经常性损益的净利润',
                        '归属于母公司股东的扣除非经常性损益后的净利润',
                        '扣除非经常性损益后归属于本公司股东的净利润']),
    ('经营现金流净额', ['经营活动产生的现金流量净额']),
    ('基本每股收益', ['基本每股收益']),
    ('稀释每股收益', ['稀释每股收益']),
    ('扣非基本每股收益', ['扣除非经常性损益后的基本每股收益']),
    ('加权平均净资产收益率', ['加权平均净资产收益率', '净资产收益率（加权平均）',
                              '净资产收益率(加权平均)']),
    ('扣非加权平均净资产收益率', ['扣除非经常性损益后的加权平均净资产收益率']),
    ('总资产', ['总资产', '资产总额', '资产总计']),
    ('归母净资产', ['归属于上市公司股东的净资产', '归属于母公司股东的净资产',
                    '归属于母公司所有者权益', '归属于上市公司股东的所有者权益',
                    '归属于本行股东权益', '归属于母公司股东权益', '归属于本公司股东权益',
                    '归属于本行股东的净资产', '归属于本公司股东的净资产']),
    ('每股净资产', ['归属于上市公司股东的每股净资产', '归属于母公司股东的每股净资产',
                    '归属于上市公司普通股股东的每股净资产', '归属于本行普通股股东的每股净资产',
                    '每股净资产']),
    ('营业收入扣除后金额', ['营业收入扣除后金额', '扣除后营业收入']),
]

CORE = ['营业收入', '归母净利润', '扣非归母净利润', '经营现金流净额']
METRIC_ORDER = [m for m, _ in CATALOG]

ALIASES = []
for _idx, (_metric, _names) in enumerate(CATALOG):
    for _name in _names:
        ALIASES.append((_name, _metric, _idx))
ALIASES.sort(key=lambda t: (-len(t[0]), t[2]))


def match_metric(text):
    '''按「别名长优先」找指标。命中后若紧跟「回报率/收益率」等后缀词，说明命中的是
    另一个更长的比率名（如「期末总资产回报率」里的「总资产」），不算。'''
    if not text:
        return None
    for alias, metric, _ in ALIASES:
        pos = text.find(alias)
        if pos < 0:
            continue
        if text[pos + len(alias):].startswith(NEVER_AFTER):
            continue
        return metric
    return None


def match_metric_at(text):
    '''同 match_metric，另外给出命中位置。标签里常把上一行的小节标题、上一行的
    脚注也吸进来，显示时从命中处截断更干净（「总资产」而不是「第二章会计数据
    和财务指标摘要……总资产」）。'''
    if not text:
        return None, -1
    for alias, metric, _ in ALIASES:
        pos = text.find(alias)
        if pos < 0:
            continue
        if text[pos + len(alias):].startswith(NEVER_AFTER):
            continue
        return metric, pos
    return None, -1

OUT_FIELDS = ['code', 'name', 'fiscal_year', 'metric', 'value', 'unit', 'page',
              'col_source', 'col_mode', 'label', 'source_file']
CORE_FIELDS = ['code', 'name', 'year', 'metric', 'value', 'unit', 'page',
               'n_cols', 'label', 'source_file']

NUM_RE = re.compile(r'^\d+(\.\d+)?$')
YEAR_CELL_RE = re.compile(r'^((?:19|20)\d{2})(?:年1[-—–~至]12月|年12月31日|年末|年度|年)?$')
# 指标别名命中后，若后面紧跟这些词，说明命中的是「另一个更长的比率名」，不是本指标。
NEVER_AFTER = ('回报率', '周转率', '收益率', '增长率', '变动率', '费用率', '比率', '周转天数')
Y_TOL = 3.0
X_GAP = 6.0
HEADER_GROUP_GAP = 16.0
HEADER_MAX_ABOVE = 300.0
YEAR_COL_TOL = 48.0
# 折行标签的搜索窗：标签碎片离数字行不超过这个距离，就进候选
LABEL_WIN = 62.0
# 碎片与数字行「同处一行」的纵向容差（同处一行的标签不许借给邻行用）
LABEL_SIT = 12.0
# 只差一个「（元）（元/股）（亿元）」这类单位时，把这段也吃进标签
UNIT_FRAG_RE = re.compile(r'^[（(][^（）()]{0,10}[）)]$')
MIN_YEAR = 2021
MAX_YEAR = 2025


def norm(s):
    return re.sub(r'[ \u3000\t]', '', s)


def to_num(s):
    '''把年报里的数字串转成 float。括号表示负数；带 % 的比率也当数字（数值本身）。'''
    t = s.strip().replace(',', '').replace('，', '')
    neg = False
    for a, b in (('（', '）'), ('(', ')'), ('〔', '〕')):
        if t.startswith(a) and t.endswith(b):
            t = t[1:-1]
            neg = True
            break
    if t.endswith('%') or t.endswith('％'):
        t = t[:-1]
    for sign in ('-', '－', '−'):
        if t.startswith(sign):
            t = t[1:]
            neg = True
            break
    if not NUM_RE.match(t):
        return None
    return -float(t) if neg else float(t)


def page_rows(page):
    '''把一页切成「表格行」，行内按横坐标切成「单元格」。'''
    words = page.get_text('words')
    items = [{'t': w[4], 'x0': w[0], 'x1': w[2], 'yc': (w[1] + w[3]) / 2}
             for w in words if w[4].strip()]
    items.sort(key=lambda i: (i['yc'], i['x0']))
    rows = []
    for it in items:
        hit = None
        for r in rows:
            if abs(r['yc'] - it['yc']) <= Y_TOL:
                hit = r
                break
        if hit:
            hit['items'].append(it)
            hit['yc'] = sum(x['yc'] for x in hit['items']) / len(hit['items'])
        else:
            rows.append({'yc': it['yc'], 'items': [it]})
    rows.sort(key=lambda r: r['yc'])
    out = []
    for r in rows:
        r['items'].sort(key=lambda i: i['x0'])
        cells, cur = [], None
        for it in r['items']:
            if cur is None or it['x0'] - cur['x1'] > X_GAP:
                cur = {'text': it['t'], 'x0': it['x0'], 'x1': it['x1'], 'yc': it['yc']}
                cells.append(cur)
            else:
                cur['text'] += it['t']
                cur['x1'] = it['x1']
        out.append({'yc': r['yc'], 'cells': cells})
    return out

UNIT_KEYS = (('百万', '百万元'), ('亿', '亿元'), ('万', '万元'), ('千', '千元'),
             ('元/股', '元/股'), ('元／股', '元/股'), ('元', '元'),
             ('%', '%'), ('％', '%'))
UNIT_RE = re.compile(r'单位[:：]?(.{0,10})')


def classify_unit(tail):
    for key, val in UNIT_KEYS:
        if key in tail:
            return val
    return None


def declared_unit(rows):
    '''页面上「单位：元 / 人民币百万元」这类声明。'''
    for r in rows:
        joined = norm(''.join(c['text'] for c in r['cells']))
        m = UNIT_RE.search(joined)
        if m:
            u = classify_unit(m.group(1))
            if u:
                return u
    return None


AMOUNT_UNITS = ('百万元', '亿元', '万元', '千元', '元')
PAREN_RE = re.compile(r'[（(]([^（）()]{1,14})[）)]')
# 这几个指标的单位是「指标本身决定的」，不该靠版式猜。
METRIC_UNIT = {'加权平均净资产收益率': '%', '扣非加权平均净资产收益率': '%',
               '基本每股收益': '元/股', '稀释每股收益': '元/股',
               '扣非基本每股收益': '元/股', '每股净资产': '元/股'}


def row_unit(text):
    '''扫一段文字里所有括号组，取第一个「金额单位」（排除 元/股、%）。'''
    for m in PAREN_RE.finditer(text):
        u = classify_unit(m.group(1))
        if u in AMOUNT_UNITS:
            return u
    return None


def common_label_unit(rows):
    '''页面没有「单位：」声明时，看标签里出现最多的「金额单位」（排除 元/股、%）。'''
    counts = Counter()
    for r in rows:
        u = row_unit(norm(''.join(c['text'] for c in r['cells'])))
        if u:
            counts[u] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def year_header_groups(rows, report_year):
    '''把「年份格」按纵向聚成表头组，返回 [(组下沿 y, [(列中心 x, 财年), ...]), ...]。

    只认整格就是「20XX年 / 20XX年末 / 20XX年12月31日」的单元格；跨页续表里的
    '20.2X年'、正文里的日期串都不符合，自然被丢掉。年份必须左→右递减且含报告年份。
    '''
    marks = []
    for r in rows:
        for c in r['cells']:
            m = YEAR_CELL_RE.match(norm(c['text']))
            if m:
                marks.append((r['yc'], (c['x0'] + c['x1']) / 2, int(m.group(1))))
    marks.sort()
    groups = []
    for yc, xc, y in marks:
        if groups and yc - groups[-1]['yc'] <= HEADER_GROUP_GAP:
            groups[-1]['yc'] = max(groups[-1]['yc'], yc)
            groups[-1]['marks'].append((xc, y))
        else:
            groups.append({'yc': yc, 'marks': [(xc, y)]})
    out = []
    for g in groups:
        cols = []
        for xc, y in sorted(g['marks']):
            if cols and xc - cols[-1][0] < 20.0:
                continue
            cols.append((xc, y))
        years = [y for _, y in cols]
        if len(cols) < 2 or len(set(years)) != len(years):
            continue
        if any(years[i] <= years[i + 1] for i in range(len(years) - 1)):
            continue
        if report_year not in years:
            continue
        out.append((g['yc'], cols))
    out.sort()
    return out


HEADING_RE = re.compile(r'^[（(]?[一二三四五六七八九十]{1,3}[）)、]')
HEADING_KEYS = ('主要会计数据', '主要财务指标', '分季度')


def heading_ys(rows):
    '''小节标题所在的纵坐标。用来防止「张冠李戴」借到别的小节表头。'''
    out = []
    for r in rows:
        t = norm(''.join(c['text'] for c in r['cells']))
        if not t:
            continue
        if any(k in t for k in HEADING_KEYS) or (HEADING_RE.match(t) and len(t) <= 30):
            out.append(r['yc'])
    return out


def tail_carry(rows, report_year):
    '''上一页那张表的年份表头，给跨页续表用 —— 只有「表在页底被切断、
    之后没再起新小节」才算数，否则宁可返回 None。'''
    groups = year_header_groups(rows, report_year)
    if not groups:
        return None
    heads = heading_ys(rows)
    g = groups[-1]
    if any(hy > g[0] for hy in heads):
        return None
    below = [r for r in rows if r['yc'] > g[0] and any('num' in c for c in r['cells'])]
    if not below:
        return None
    return g[1]


def extract_from_page(page, report_year, carry_cols=None):
    '''抽一页，返回 (rows_found, col_mode, page_unit, n_header_groups, carry_cols)。

    carry_cols：上一页那张跨页表的年份表头；续表页自己往往不重复表头，借过来用。
    rows_found: [{'metric','label','unit','years':{财年: 数值},'n_vals'}, ...]
    '''
    rows = page_rows(page)
    for r in rows:
        for c in r['cells']:
            v = to_num(c['text'])
            if v is not None:
                c['num'] = v
                c['xc'] = (c['x0'] + c['x1']) / 2
    num_rows = [r for r in rows if sum(1 for c in r['cells'] if 'num' in c) >= 2]
    if not num_rows:
        return [], 'none', None, 0, None

    groups = year_header_groups(rows, report_year)
    if not groups and not carry_cols:
        # 没有可信的年份表头，就整页放弃：宁可缺数据，也绝不把「季度数」「比率」
        # 按位置硬套成年报数 —— 那种错法不会报错，只会悄悄污染整张表。
        return [], 'none', declared_unit(rows) or common_label_unit(rows), 0, None
    heads = heading_ys(rows)
    col_mode = 'header'
    page_declared = declared_unit(rows) or common_label_unit(rows)

    num_left = min(c['x0'] for r in num_rows for c in r['cells'] if 'num' in c)
    # 标签一定起在数字列的左边，所以按「左边界」判断；按右边界判断会把长标签
    # （如「加权平均净资产收益率（%）」「归属于上市公司股东的净利润（元）」）
    # 整行滤掉 —— 它们的右边界本来就压在数字列上。
    frags = [c for r in rows for c in r['cells']
             if 'num' not in c and c['x0'] < num_left + 1.5 and 0 < len(c['text']) <= 40]

    def near_frags(row):
        return sorted([f for f in frags if abs(f['yc'] - row['yc']) <= LABEL_WIN],
                      key=lambda c: c['yc'])

    # 老做法：每个标签碎片归给纵向最近的那个数字行，再按纵坐标拼回来。
    buckets = [[] for _ in num_rows]
    for f in frags:
        best = min(range(len(num_rows)), key=lambda i: abs(num_rows[i]['yc'] - f['yc']))
        buckets[best].append(f)
    for b in buckets:
        b.sort(key=lambda c: c['yc'])

    def label_options(i):
        '''候选标签，按可信度从高到低：
          ① 本行自己那一桶；
          ② 上一桶的尾巴 + 本桶 / 本桶 + 下一桶的开头 —— 折行标签的末行常被
             算到下一行头上（神华的「归属于本公司股东 / 的扣除非经常性损 /
             益的净利润」三段，末段更靠近下一行）。
        借来的碎片必须「没有跟别的数字行同处一行」（LABEL_SIT），否则宁可不要 ——
        招行、平安那种密集单行表里，邻行标签就贴在它自己的数字行上，不许借。'''
        own = buckets[i]
        out = [own]
        if i + 1 < len(buckets):
            base = num_rows[i + 1]['yc']
            for d in (1, 2):
                nxt = buckets[i + 1][:d]
                if len(nxt) == d and all(abs(f['yc'] - base) > LABEL_SIT for f in nxt):
                    out.append(own + nxt)
        if i > 0:
            base = num_rows[i - 1]['yc']
            for d in (1, 2):
                prev = buckets[i - 1][-d:]
                if len(prev) == d and all(abs(f['yc'] - base) > LABEL_SIT for f in prev):
                    out.append(prev + own)
        return out

    found = []
    found_metrics = set()
    for i, row in enumerate(num_rows):
        strict = metric = None
        for fs in label_options(i):
            text = norm(''.join(f['text'] for f in fs))
            m, pos = match_metric_at(text)
            if m and m not in found_metrics:
                strict, metric = text[pos:], m
                break
        if metric is None:
            continue
        found_metrics.add(metric)
        label = strict
        # 表头组和本行之间只要夹着小节标题，就说明这行不属于这张表（例如
        # 「六、分季度主要财务指标」下面的表没有年份，绝不能借用上面的年度表头）。
        above = [g for g in groups if 2.0 < row['yc'] - g[0] <= HEADER_MAX_ABOVE
                 and not any(g[0] < hy < row['yc'] for hy in heads)]
        if above:
            cols = above[-1][1]
        elif carry_cols and not any(hy < row['yc'] for hy in heads):
            # 续表页：本行上方没有表头、也没有新小节 ⇒ 借上一页那张表的年份列
            cols = carry_cols
        else:
            cols = None
        nums = [c for c in row['cells'] if 'num' in c]
        years = {}
        for xc, yr in cols or []:
            best = None
            for c in nums:
                d = abs(c['xc'] - xc)
                if d <= YEAR_COL_TOL and (best is None or d < best[0]):
                    best = (d, c['num'])
            if best is not None:
                years.setdefault(yr, best[1])
        if not years:
            continue
        found.append({'metric': metric, 'label': label,
                      'unit': METRIC_UNIT.get(metric) or row_unit(strict) or row_unit(label),
                      'years': years, 'n_vals': len(nums)})
    for f in found:
        if not f['unit']:
            f['unit'] = page_declared
    return found, col_mode, page_declared, len(groups), tail_carry(rows, report_year)

def parse_pdf(path, report_year):
    '''扫全篇：凡含「主要会计数据 / 主要财务指标」的页都抽一遍，按指标合并。

    年报里这几张表常跨页（年度数在上一页、期末数在下一页），所以必须合并；
    同一指标在多页出现且数值不同 ⇒ 记冲突，写进日志交人工看。
    '''
    doc = pymupdf.open(path)
    merged, conflicts, pages_used = {}, [], []
    carry, prev_used = None, False
    try:
        for i in range(len(doc)):
            text = doc[i].get_text()
            hit = ('主要会计数据' in text) or ('主要财务指标' in text)
            if not hit and not prev_used:
                carry = None
                continue
            found, mode, unit, _n_groups, carry = extract_from_page(
                doc[i], report_year, carry if prev_used else None)
            if not found:
                prev_used = False
                continue
            prev_used = True
            pages_used.append(i + 1)
            for rec in found:
                rec['page'] = i + 1
                rec['col_mode'] = mode
                if not rec['unit']:
                    rec['unit'] = unit
                old = merged.get(rec['metric'])
                if old is None:
                    merged[rec['metric']] = rec
                elif old['years'] != rec['years']:
                    conflicts.append((rec['metric'], old['page'], old['years'],
                                      rec['page'], rec['years']))
    finally:
        doc.close()
    if not merged:
        return None
    return {'rows': [merged[m] for m in METRIC_ORDER if m in merged],
            'pages': pages_used, 'conflicts': conflicts}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)
    records, lines = {}, []
    company_stat, mode_stat = {}, Counter()
    for pdf in sorted(RAW.glob('*.pdf')):
        m = re.match(r'(\d{6})_([^_]+)_(\d{4})年年度报告', pdf.stem)
        if not m:
            continue
        code, name, report_year = m.group(1), m.group(2), int(m.group(3))
        company_stat.setdefault(code, [name, 0, 0])
        company_stat[code][2] += 1
        best = parse_pdf(pdf, report_year)
        if best is None:
            lines.append('=== %s %s %d 年报  ★没找到「主要会计数据」页' % (code, name, report_year))
            continue
        mode_stat['+'.join(sorted({r['col_mode'] for r in best['rows']}))] += 1
        for cname, pg1, ys1, pg2, ys2 in best['conflicts']:
            lines.append('    ★同指标冲突 %s：p%s=%s  vs  p%s=%s' % (cname, pg1, ys1, pg2, ys2))
        lines.append('=== %s %s %d 年报  命中页 %s  单位=%s  命中指标=%d'
                     % (code, name, report_year,
                        ','.join('p%d' % p for p in best['pages']),
                        '|'.join(sorted({r['unit'] or '-' for r in best['rows']})),
                        len(best['rows'])))
        for metric in METRIC_ORDER:
            rec = next((r for r in best['rows'] if r['metric'] == metric), None)
            if rec is None:
                lines.append('    %-16s MISS' % metric)
                continue
            shown = []
            for fy, val in sorted(rec['years'].items()):
                shown.append('%s=%s' % (fy, val))
                if not (MIN_YEAR <= fy <= MAX_YEAR):
                    continue
                # pri = 这个财年离报告年份差多少年（0 = 本年列）。越小越优先：
                # 「本年列」是首次披露的原值；越往后的年报里的老财年列越可能是重述值。
                pri = abs(fy - report_year)
                key = (code, fy, metric)
                old = records.get(key)
                if old is None or (pri, report_year) < (old['_pri'], old['_ry']):
                    records[key] = {'code': code, 'name': name, 'fiscal_year': fy,
                                    'metric': metric, 'value': val, 'unit': rec['unit'] or '',
                                    'page': rec['page'], 'col_mode': rec['col_mode'],
                                    'col_source': 'current' if pri == 0 else 'prior%d' % pri,
                                    'label': rec['label'][:46], 'source_file': pdf.name,
                                    'n_years': len(rec['years']), '_pri': pri, '_ry': report_year}
            lines.append('    %-16s p%-3s %-42s 单位=%-6s %s'
                         % (metric, rec['page'], rec['label'][:36], rec['unit'] or '-',
                            ' '.join(shown)))
    rows = sorted(records.values(), key=lambda r: (r['code'], r['fiscal_year'],
                                                   METRIC_ORDER.index(r['metric'])))
    for r in rows:
        company_stat[r['code']][1] += 1
    with (OUT / 'financials.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in OUT_FIELDS})
    core = [r for r in rows if r['metric'] in CORE and r['col_source'] == 'current']
    with (OUT / 'metrics.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=CORE_FIELDS)
        w.writeheader()
        for r in core:
            w.writerow({'code': r['code'], 'name': r['name'], 'year': r['fiscal_year'],
                        'metric': r['metric'], 'value': r['value'], 'unit': r['unit'],
                        'page': r['page'], 'n_cols': r['n_years'], 'label': r['label'],
                        'source_file': r['source_file']})
    lines += ['', '长表 %d 行' % len(rows), '列模式：' + str(dict(mode_stat)), '']
    for metric in METRIC_ORDER:
        n = sum(1 for r in rows if r['metric'] == metric)
        lines.append('  指标 %-16s 覆盖 %d 行' % (metric, n))
    lines.append('')
    for code, (name, n, files) in sorted(company_stat.items()):
        lines.append('  公司 %s %-8s %d 行 / %d 个年报文件' % (code, name, n, files))
    (LOG / 'extract_run.txt').write_text('\n'.join(lines), encoding='utf-8')
    print('长表 %d 行；%d 家公司；列模式 %s' % (len(rows), len(company_stat), dict(mode_stat)))


if __name__ == '__main__':
    main()
