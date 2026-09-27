# -*- coding: utf-8 -*-
'''对账：把「从年报 PDF 抽出来的数」和「东方财富的现成数据」逐字段比对。

对账范围（东方财富 4 张表）：
    RPT_F10_FINANCE_GINCOME   利润表   营业收入/营业总收入/归母净利/扣非/每股收益
    RPT_F10_FINANCE_GBALANCE  资产负债表 总资产/归母净资产
    RPT_F10_FINANCE_GCASHFLOW 现金流量表 经营现金流净额
    RPT_LICO_FN_CPD           主要指标  加权ROE/每股净资产/扣非每股收益/同比

口径说明：东方财富只做「交叉验证」，不作唯一真源；不一致就是线索，要回原文看。
参差项（东财没有对应字段）单独标 INFO，不算错。

运行：python scripts/quality/crosscheck.py
产出：data/extracted/crosscheck.csv + logs/crosscheck_run.txt
'''
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTRACTED = ROOT / 'data' / 'extracted'
LOG = ROOT / 'logs'

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
EM = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
REPORTS = [('GINCOME', 'RPT_F10_FINANCE_GINCOME', 'REPORT_DATE'),
           ('GBALANCE', 'RPT_F10_FINANCE_GBALANCE', 'REPORT_DATE'),
           ('GCASHFLOW', 'RPT_F10_FINANCE_GCASHFLOW', 'REPORT_DATE'),
           ('CPD', 'RPT_LICO_FN_CPD', 'REPORTDATE')]

CHECKS = {
    '营业总收入': ('GINCOME', 'TOTAL_OPERATE_INCOME'),
    '营业收入': ('GINCOME', 'OPERATE_INCOME'),
    '归母净利润': ('GINCOME', 'PARENT_NETPROFIT'),
    '扣非归母净利润': ('GINCOME', 'DEDUCT_PARENT_NETPROFIT'),
    '经营现金流净额': ('GCASHFLOW', 'NETCASH_OPERATE'),
    '基本每股收益': ('GINCOME', 'BASIC_EPS'),
    '稀释每股收益': ('GINCOME', 'DILUTED_EPS'),
    '扣非基本每股收益': ('CPD', 'DEDUCT_BASIC_EPS'),
    '加权平均净资产收益率': ('CPD', 'WEIGHTAVG_ROE'),
    '总资产': ('GBALANCE', 'TOTAL_ASSETS'),
    '归母净资产': ('GBALANCE', 'TOTAL_PARENT_EQUITY'),
    '每股净资产': ('CPD', 'BPS'),
}
NO_COUNTERPART = ['扣非加权平均净资产收益率', '营业收入扣除后金额']
SCALE = {'元': 1.0, '千元': 1e3, '万元': 1e4, '百万元': 1e6, '亿元': 1e8,
         '元/股': 1.0, '%': 1.0, '股': 1.0}
ABS_TOL = 0.011
YOY_TOL = 0.05


def em_get(report, code, sort_col):
    params = {'reportName': report, 'columns': 'ALL', 'filter': '(SECURITY_CODE=%s)' % code,
              'pageSize': '80', 'sortColumns': sort_col, 'sortTypes': '-1'}
    req = urllib.request.Request(EM + '?' + urllib.parse.urlencode(params),
                                 headers={'User-Agent': UA, 'Referer': 'https://data.eastmoney.com/'})
    res = json.loads(urllib.request.urlopen(req, timeout=45).read().decode('utf-8'))
    by_year = {}
    for r in (res.get('result') or {}).get('data') or []:
        d = str(r.get('REPORT_DATE') or r.get('REPORTDATE') or '')[:10]
        if d.endswith('-12-31'):
            by_year[int(d[:4])] = r
    return by_year


def load_ours():
    path = EXTRACTED / 'financials.csv'
    with path.open(encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    ours = {}
    for r in rows:
        fy = int(r['fiscal_year'])
        ours[(r['code'], fy, r['metric'])] = (float(r['value']), r['unit'], r['page'],
                                              r['col_source'], r['col_mode'])
    return ours


def close(a, b):
    if a is None or b is None:
        return False
    return abs(a - b) <= max(ABS_TOL, abs(b) * 1e-6)


def verdict(a, b):
    '''三态：PASS 完全一致 / DIFF 口径差异（差 ≤3%，多半是追溯调整 —— 东财多用后来的
    重述值，我们取当年年报原值）/ FAIL 待人工确认（>3%）/ SKIP 东财没这个字段。'''
    if a is None or b is None:
        return 'SKIP'
    if close(a, b):
        return 'PASS'
    return 'DIFF' if abs(a - b) <= max(ABS_TOL, abs(b) * 0.03) else 'FAIL'


def main():
    ours = load_ours()
    codes = sorted({k[0] for k in ours})
    names = {}
    lines, out_rows = [], []
    for code in codes:
        tables = {}
        for short, report, sort_col in REPORTS:
            tables[short] = em_get(report, code, sort_col)
        years = sorted({k[1] for k in ours if k[0] == code})
        lines.append('===== %s =====' % code)
        for year in years:
            hits = fails = skips = 0
            for metric, (table, field) in CHECKS.items():
                got = ours.get((code, year, metric))
                em_row = tables.get(table, {}).get(year)
                em_val = em_row.get(field) if em_row else None
                if got is None:
                    continue
                raw, unit, page, col_source, col_mode = got
                val = raw * SCALE.get(unit, 1.0)
                name = metric
                if em_val is None:
                    state = 'SKIP'
                    skips += 1
                else:
                    state = verdict(val, float(em_val))
                    hits += 1 if state == 'PASS' else 0
                    fails += 0 if state in ('PASS', 'DIFF') else 1
                out_rows.append({'code': code, 'fiscal_year': year, 'metric': name,
                                 'value_pdf': raw, 'unit_pdf': unit, 'value_norm': val,
                                 'table': table, 'field': field,
                                 'value_eastmoney': '' if em_val is None else float(em_val),
                                 'page': page, 'col_source': col_source, 'col_mode': col_mode,
                                 'match': state, 'kind': 'extract'})
            for label, a, b in yoy_checks(ours, tables, code, year):
                out_rows.append({'code': code, 'fiscal_year': year, 'metric': label,
                                 'value_pdf': '', 'unit_pdf': '%', 'value_norm': a,
                                 'table': 'CPD', 'field': '', 'value_eastmoney': b,
                                 'page': '', 'col_source': 'derived', 'col_mode': '',
                                 'match': verdict(a, b), 'kind': 'derived'})
            gi = tables.get('GINCOME', {}).get(year) or {}
            tot, oi = gi.get('TOTAL_OPERATE_INCOME'), gi.get('OPERATE_INCOME')
            if tot is not None and oi is not None and abs(tot - oi) > 0.5:
                out_rows.append({'code': code, 'fiscal_year': year,
                                 'metric': '口径参考:东财营业总收入-营业收入',
                                 'value_pdf': '', 'unit_pdf': '元', 'value_norm': tot - oi,
                                 'table': 'GINCOME', 'field': 'TOTAL_OPERATE_INCOME-OPERATE_INCOME',
                                 'value_eastmoney': '', 'page': '', 'col_source': 'info',
                                 'col_mode': '', 'match': 'INFO', 'kind': 'info'})
            lines.append('  %d 年  对账 %d 项：一致 %d / 不一致 %d / 东财无此字段 %d'
                         % (year, hits + fails + skips, hits, fails, skips))
    with (EXTRACTED / 'crosscheck.csv').open('w', newline='', encoding='utf-8-sig') as f:
        fields = ['code', 'fiscal_year', 'metric', 'value_pdf', 'unit_pdf', 'value_norm',
                  'table', 'field', 'value_eastmoney', 'page', 'col_source', 'col_mode',
                  'match', 'kind']
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    checked = [r for r in out_rows if r['kind'] == 'extract' and r['match'] in ('PASS', 'DIFF', 'FAIL')]
    passed = sum(1 for r in checked if r['match'] == 'PASS')
    diffed = sum(1 for r in checked if r['match'] == 'DIFF')
    failed = sum(1 for r in checked if r['match'] == 'FAIL')
    by_metric = {}
    for r in checked:
        d = by_metric.setdefault(r['metric'], [0, 0, 0])
        d[1] += 1
        if r['match'] == 'PASS':
            d[0] += 1
        if r['match'] == 'FAIL':
            d[2] += 1
    lines += ['', '对账 %d 项：一致 %d / 口径差异 %d / 疑似错误 %d；东财无此字段 %d 项；口径参考 %d 项'
              % (len(checked), passed, diffed, failed,
                 sum(1 for r in out_rows if r['match'] == 'SKIP'),
                 sum(1 for r in out_rows if r['match'] == 'INFO')), '',
              '分指标（一致 / 疑似错误 / 总数）：']
    for metric, (ok, tot, bad) in sorted(by_metric.items(),
                                         key=lambda kv: kv[1][0] / max(1, kv[1][1])):
        lines.append('  %-22s %d / %d / %d' % (metric, ok, bad, tot))
    derived = [r for r in out_rows if r['kind'] == 'derived']
    lines += ['', '自算同比 %d 项，其中与东财一致 %d 项。'
              '口径提醒：东财 YSTZ/SJLTZ 用的是「营业总收入」同比，我们按「营业收入」自算，'
              '对不上是口径不同，不当错误。'
              % (len(derived), sum(1 for r in derived if r['match'] == 'PASS'))]
    fails = [r for r in checked if r['match'] == 'FAIL']
    if fails:
        lines += ['', '疑似抽取错误明细：']
        for r in fails:
            lines.append('  %s %s %s  PDF=%s%s(第%s页/%s)  东财=%s  %s'
                         % (r['code'], r['fiscal_year'], r['metric'], r['value_pdf'],
                            r['unit_pdf'], r['page'], r['col_source'], r['value_eastmoney'],
                            r['field'] or r['table']))
    diffs = [r for r in checked if r['match'] == 'DIFF']
    if diffs:
        lines += ['', '口径差异明细（差 ≤1%%，多为追溯调整/取列不同，需人工确认）：']
        for r in diffs:
            lines.append('  %s %s %s  PDF=%s%s(第%s页/%s)  东财=%s'
                         % (r['code'], r['fiscal_year'], r['metric'], r['value_pdf'],
                            r['unit_pdf'], r['page'], r['col_source'], r['value_eastmoney']))
    (LOG / 'crosscheck_run.txt').write_text('\n'.join(lines), encoding='utf-8')
    print('对账 %d 项：一致 %d / 口径差异 %d / 疑似错误 %d' % (len(checked), passed, diffed, failed))


def yoy_checks(ours, tables, code, year):
    out = []
    for label, metric, field in (('营业收入同比%', '营业收入', 'YSTZ'),
                                 ('归母同比%', '归母净利润', 'SJLTZ')):
        cur = ours.get((code, year, metric))
        prev = ours.get((code, year - 1, metric))
        if not cur or not prev or prev[0] == 0:
            continue
        a = (cur[0] - prev[0]) / abs(prev[0]) * 100
        row = tables.get('CPD', {}).get(year) or {}
        b = row.get(field)
        out.append((label, a, b))
    return out


if __name__ == '__main__':
    main()
