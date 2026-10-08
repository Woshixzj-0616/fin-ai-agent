# -*- coding: utf-8 -*-
"""调试：把指定 PDF 指定页还原出来的"表格行"原样打印，用于定位抽取失败的真因。
用法: python scripts/data/debug_rows.py <pdf关键字> <页码>
"""
import re, sys
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data' / 'raw'
LABEL_TOL = 3.0

def page_rows(page):
    lines = []
    for block in page.get_text('dict')['blocks']:
        if block.get('type') != 0:
            continue
        for line in block['lines']:
            text = ''.join(sp['text'] for sp in line['spans']).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line['bbox']
            lines.append({'text': text, 'x0': x0, 'x1': x1, 'yc': (y0 + y1) / 2})
    lines.sort(key=lambda r: (r['yc'], r['x0']))
    rows = []
    for ln in lines:
        hit = None
        for r in rows:
            if abs(r['yc'] - ln['yc']) <= LABEL_TOL:
                hit = r
                break
        if hit:
            hit['cells'].append(ln)
            hit['yc'] = sum(c['yc'] for c in hit['cells']) / len(hit['cells'])
        else:
            rows.append({'yc': ln['yc'], 'cells': [ln]})
    rows.sort(key=lambda r: r['yc'])
    for r in rows:
        r['cells'].sort(key=lambda c: c['x0'])
    return rows

key, pageno = sys.argv[1], int(sys.argv[2])
target = [p for p in RAW.glob('*.pdf') if key in p.name][0]
doc = pymupdf.open(target)
out = ['FILE: %s   PAGE %d' % (target.name, pageno)]
for r in page_rows(doc[pageno - 1]):
    cells = ' || '.join('%s@%.1f' % (c['text'], c['x0']) for c in r['cells'])
    out.append('yc=%7.1f  %s' % (r['yc'], cells))
(ROOT / 'logs' / 'debug_rows.txt').write_text('\n'.join(out), encoding='utf-8')
print('written')