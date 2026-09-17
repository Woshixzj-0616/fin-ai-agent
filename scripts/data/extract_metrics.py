# -*- coding: utf-8 -*-
"""从年报 PDF 里抽取核心财务指标，并保留来源定位(页码)。

关键做法(不针对某家公司硬编码版式):
  1. 用 pymupdf 的 words 级取数(带坐标)。不能用 line 级 —— 年报里相邻两列的数字
     会被并成"一行文本"，导致整行认不出数字。
  2. 按纵坐标聚成"表格行"，行内按横坐标切"列"(间距大于阈值就切)。
  3. 数字列最左边那列的左边界，左边就是"标签列"。
  4. 标签常被折成多行(如"归属于上市公司股东的净"+"利润")。
     每个标签碎片只归给它纵向最近的那个数字行，再按纵坐标顺序拼回完整标签。

运行: python scripts/data/extract_metrics.py
产出: data/extracted/metrics.csv
"""
import csv, re
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data' / 'raw'
OUT = ROOT / 'data' / 'extracted'
LOG = ROOT / 'logs'

METRICS = [
    ('营业收入', ['营业收入']),
    ('归母净利润', ['归属于上市公司股东的净利润']),
    ('扣非归母净利润', ['归属于上市公司股东的扣除非经常性损益的净利润']),
    ('经营现金流净额', ['经营活动产生的现金流量净额']),
]

NUM_RE = re.compile(r'^-?\d+(\.\d+)?$')
Y_TOL = 3.0
X_GAP = 6.0


def norm(s):
    return re.sub(r'[ \u3000\t]', '', s)


def to_num(s):
    t = s.strip().replace(',', '')
    if t.startswith('（') and t.endswith('）'):
        t = '-' + t[1:-1]
    return float(t) if NUM_RE.match(t) else None


def page_rows(page):
    words = page.get_text('words')
    items = [{'t': w[4], 'x0': w[0], 'x1': w[2], 'yc': (w[1] + w[3]) / 2} for w in words if w[4].strip()]
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


def extract_from_page(page):
    rows = page_rows(page)
    unit = None
    for r in rows:
        joined = norm(''.join(c['text'] for c in r['cells']))
        m = re.search(r'单位[:：]?([^币金股万]{1,4})', joined)
        if m and m.group(1):
            unit = m.group(1)
            break

    for r in rows:
        for c in r['cells']:
            v = to_num(c['text'])
            if v is not None:
                c['num'] = v
    num_rows = [r for r in rows if sum(1 for c in r['cells'] if 'num' in c) >= 2]
    if not num_rows:
        return {}, unit

    num_left = min(c['x0'] for r in num_rows for c in r['cells'] if 'num' in c)
    frags = [c for r in rows for c in r['cells']
             if 'num' not in c and c['x1'] <= num_left + 1.5 and 0 < len(c['text']) <= 40]

    # 每个标签碎片只归给纵向最近的那个数字行
    buckets = [[] for _ in num_rows]
    for f in frags:
        best = min(range(len(num_rows)), key=lambda i: abs(num_rows[i]['yc'] - f['yc']))
        buckets[best].append(f)

    result = {}
    for i, row in enumerate(num_rows):
        nums = [c for c in row['cells'] if 'num' in c]
        label = ''.join(f['text'] for f in sorted(buckets[i], key=lambda c: c['yc']))
        label_norm = norm(label)
        for name, aliases in METRICS:
            if name in result:
                continue
            for alias in sorted(aliases, key=len, reverse=True):
                if norm(alias) in label_norm:
                    result[name] = {'value': nums[0]['num'], 'n_cols': len(nums),
                                    'label': label_norm[:44], 'all_values': [c['num'] for c in nums]}
                    break
    return result, unit


def parse_pdf(path):
    doc = pymupdf.open(path)
    best, unit_best, page_best = {}, None, None
    for i in range(len(doc)):
        if '主要会计数据' not in doc[i].get_text():
            continue
        found, unit = extract_from_page(doc[i])
        if len(found) > len(best):
            best, unit_best, page_best = found, unit, i + 1
    doc.close()
    return best, unit_best, page_best


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    LOG.mkdir(parents=True, exist_ok=True)
    lines, csv_rows = [], []
    for pdf in sorted(RAW.glob('*.pdf')):
        m = re.match(r'(\d{6})_([^_]+)_(\d{4})年年度报告', pdf.stem)
        if not m:
            continue
        code, name, year = m.group(1), m.group(2), int(m.group(3))
        found, unit, page = parse_pdf(pdf)
        lines.append('=== %s %s %d 年报  命中页 p%s  单位=%s' % (code, name, year, page, unit))
        for key, _ in METRICS:
            if key in found:
                f = found[key]
                lines.append('    %-14s %20.2f  同行%d个数字  标签=%s' % (key, f['value'], f['n_cols'], f['label']))
                csv_rows.append({'code': code, 'name': name, 'year': year, 'metric': key,
                                 'value': f['value'], 'unit': unit or '', 'page': page,
                                 'n_cols': f['n_cols'], 'label': f['label'], 'source_file': pdf.name})
            else:
                lines.append('    %-14s MISS' % key)
                csv_rows.append({'code': code, 'name': name, 'year': year, 'metric': key,
                                 'value': '', 'unit': unit or '', 'page': page,
                                 'n_cols': '', 'label': '', 'source_file': pdf.name})
    with (OUT / 'metrics.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['code', 'name', 'year', 'metric', 'value', 'unit', 'page', 'n_cols', 'label', 'source_file'])
        w.writeheader()
        w.writerows(csv_rows)
    hit = sum(1 for r in csv_rows if r['value'] != '')
    lines.append('')
    lines.append('提取命中 %d / %d' % (hit, len(csv_rows)))
    (LOG / 'extract_run.txt').write_text('\n'.join(lines), encoding='utf-8')
    print('提取命中 %d / %d' % (hit, len(csv_rows)))


if __name__ == '__main__':
    main()