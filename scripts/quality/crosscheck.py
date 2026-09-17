# -*- coding: utf-8 -*-
"""三源对账: 把"从年报 PDF 抽出来的数"和"东方财富的现成数据"逐项比对。

对账项:
  1. 营业收入      PDF 抽取值   vs  东财 OPERATE_INCOME
  2. 归母净利润    PDF 抽取值   vs  东财 PARENT_NETPROFIT
  3. 营业收入同比  PDF(本年/上年自己算) vs 东财 OPERATE_INCOME_YOY
  4. 归母同比      PDF(本年/上年自己算) vs 东财 SJLTZ
  5. (参考) 东财"营业总收入" TOTAL_OPERATE_INCOME —— 和"营业收入"口径不同，
     差异 = 利息收入/手续费收入等。茅台、伊利有财务公司，所以两者不等；
     五粮液没有，所以两者相等。这条差异本身就是要留神的"口径陷阱"。

运行: python scripts/quality/crosscheck.py
产出: data/extracted/crosscheck.csv + logs/crosscheck_run.txt
"""
import csv, json, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTRACTED = ROOT / 'data' / 'extracted'
LOG = ROOT / 'logs'

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
EM = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
COMPANIES = {'600519': '贵州茅台', '000858': '五粮液', '600887': '伊利股份'}
TOL = 1.0
YOY_TOL = 0.05


def em_get(report, code, sort_col='REPORT_DATE'):
    params = {'reportName': report, 'columns': 'ALL', 'filter': '(SECURITY_CODE="%s")' % code,
              'pageSize': '60', 'sortColumns': sort_col, 'sortTypes': '-1'}
    req = urllib.request.Request(EM + '?' + urllib.parse.urlencode(params),
                                 headers={'User-Agent': UA, 'Referer': 'https://data.eastmoney.com/'})
    res = json.loads(urllib.request.urlopen(req, timeout=45).read().decode('utf-8'))
    by_year = {}
    for r in (res.get('result') or {}).get('data') or []:
        d = (r.get('REPORT_DATE') or r.get('REPORTDATE') or '')[:10]
        if d.endswith('-12-31'):
            by_year[int(d[:4])] = r
    return by_year


def load_ours():
    with (EXTRACTED / 'metrics.csv').open(encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    ours = {}
    for r in rows:
        if r['value']:
            ours[(r['code'], int(r['year']), r['metric'])] = float(r['value'])
    return ours


def close(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


def main():
    ours = load_ours()
    lines, out_rows = [], []
    for code, name in COMPANIES.items():
        income = em_get('RPT_F10_FINANCE_GINCOME', code, 'REPORT_DATE')
        cpd = em_get('RPT_LICO_FN_CPD', code, 'REPORTDATE')
        years = sorted({y for (c, y, m) in ours if c == code})
        lines.append('===== %s %s =====' % (code, name))
        for year in years:
            gi, cp = income.get(year) or {}, cpd.get(year) or {}
            rev = ours.get((code, year, '营业收入'))
            npf = ours.get((code, year, '归母净利润'))
            prev_rev = ours.get((code, year - 1, '营业收入'))
            prev_npf = ours.get((code, year - 1, '归母净利润'))
            our_rev_yoy = (rev - prev_rev) / abs(prev_rev) * 100 if rev and prev_rev else None
            our_npf_yoy = (npf - prev_npf) / abs(prev_npf) * 100 if npf and prev_npf else None

            checks = [
                ('营业收入', rev, gi.get('OPERATE_INCOME'), close(rev, gi.get('OPERATE_INCOME'), TOL)),
                ('归母净利润', npf, cp.get('PARENT_NETPROFIT'), close(npf, cp.get('PARENT_NETPROFIT'), TOL)),
                ('营业收入同比%', our_rev_yoy, gi.get('OPERATE_INCOME_YOY'), close(our_rev_yoy, gi.get('OPERATE_INCOME_YOY'), YOY_TOL)),
                ('归母同比%', our_npf_yoy, cp.get('SJLTZ'), close(our_npf_yoy, cp.get('SJLTZ'), YOY_TOL)),
            ]
            lines.append('  --- %d 年 ---' % year)
            for label, a, b, ok in checks:
                if a is None:
                    state, fa = '跳过(缺上一年)', '—'
                else:
                    state, fa = ('一致' if ok else '★不一致'), '%.4f' % a
                fb = '—' if b is None else '%.4f' % b
                lines.append('    %-12s 年报=%18s  东财=%18s  %s' % (label, fa, fb, state))
                out_rows.append({'code': code, 'name': name, 'year': year, 'item': label,
                                 'value_from_pdf': '' if a is None else round(a, 4),
                                 'value_eastmoney': '' if b is None else round(b, 4),
                                 'match': 'SKIP' if a is None else ('PASS' if ok else 'FAIL')})
            tot, oi = gi.get('TOTAL_OPERATE_INCOME'), gi.get('OPERATE_INCOME')
            if tot and oi:
                lines.append('    [口径参考] 东财营业总收入=%.2f 营业收入=%.2f 差=%.2f' % (tot, oi, tot - oi))
                out_rows.append({'code': code, 'name': name, 'year': year,
                                 'item': '参考:营业总收入-营业收入(口径差)',
                                 'value_from_pdf': round(tot - oi, 2), 'value_eastmoney': '', 'match': 'INFO'})
    with (EXTRACTED / 'crosscheck.csv').open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['code', 'name', 'year', 'item', 'value_from_pdf', 'value_eastmoney', 'match'])
        w.writeheader()
        w.writerows(out_rows)
    checked = [r for r in out_rows if r['match'] in ('PASS', 'FAIL')]
    passed = sum(1 for r in checked if r['match'] == 'PASS')
    lines += ['', '对账: %d / %d 一致  (跳过 %d 项、口径参考 %d 项)'
              % (passed, len(checked), sum(1 for r in out_rows if r['match'] == 'SKIP'),
                 sum(1 for r in out_rows if r['match'] == 'INFO'))]
    (LOG / 'crosscheck_run.txt').write_text('\n'.join(lines), encoding='utf-8')
    print('对账: %d / %d 一致' % (passed, len(checked)))


if __name__ == '__main__':
    main()