# -*- coding: utf-8 -*-
"""从巨潮资讯网(cninfo)拉取指定公司的年度报告 PDF，并登记来源。

只用 Python 标准库。运行:
    python scripts/data/fetch_reports.py
产出:
    data/raw/<code>_<name>_<year>年年度报告.pdf
    data/manifest.csv   —— 来源登记表(赛题要求列明数据来源/许可证)
"""
import csv, datetime, hashlib, json, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data' / 'raw'
MANIFEST = ROOT / 'data' / 'manifest.csv'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
CNINFO = 'http://www.cninfo.com.cn/new/hisAnnouncement/query'
TOPSEARCH = 'http://www.cninfo.com.cn/new/information/topSearch/query'
STATIC = 'http://static.cninfo.com.cn/'

COMPANIES = [
    ('600519', '贵州茅台', 'sse'),
    ('000858', '五粮液', 'szse'),
    ('600887', '伊利股份', 'sse'),
]
YEARS = [2023, 2024, 2025]
EXCLUDE = re.compile(r'摘要|英文|English|取消|更正|补充|问询|反馈')


def _post(url, params):
    data = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={
        'User-Agent': UA,
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    })
    return json.loads(urllib.request.urlopen(req, timeout=45).read().decode('utf-8'))


def org_id(code):
    res = _post(TOPSEARCH, {'keyWord': code, 'maxNum': '10'})
    for item in res or []:
        if item.get('code') == code:
            return item['orgId']
    raise RuntimeError('找不到 %s 的 orgId' % code)


def find_report(code, org, column, year):
    res = _post(CNINFO, {
        'pageNum': 1, 'pageSize': 50, 'column': column, 'tabName': 'fulltext',
        'stock': '%s,%s' % (code, org), 'category': 'category_ndbg_szsh',
        'seDate': '%d-01-01~%d-12-31' % (year, year + 2),
    })
    pat = re.compile(r'%d\s*年?年度报告' % year)
    cands = [a for a in (res.get('announcements') or [])
             if pat.search(a['announcementTitle']) and not EXCLUDE.search(a['announcementTitle'])]
    return sorted(cands, key=lambda a: a['announcementTime'])[0] if cands else None


def download(url, dest):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        blob = r.read()
    dest.write_bytes(blob)
    return blob


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    rows = []
    for code, name, column in COMPANIES:
        org = org_id(code)
        print('[%s %s] orgId=%s' % (code, name, org))
        for year in YEARS:
            ann = find_report(code, org, column, year)
            if not ann:
                print('   %d 年报: 没找到' % year)
                continue
            fname = '%s_%s_%d年年度报告.pdf' % (code, name, year)
            dest = RAW / fname
            if dest.exists():
                blob = dest.read_bytes()
                print('   %d 年报: 已存在，跳过下载' % year)
            else:
                blob = download(STATIC + ann['adjunctUrl'], dest)
                time.sleep(0.6)
                print('   %d 年报: OK  %.1f MB -> %s' % (year, len(blob) / 1048576, fname))
            rows.append({
                'code': code, 'name': name, 'year': year,
                'title': ann['announcementTitle'],
                'publish_date': datetime.datetime.fromtimestamp(
                    ann['announcementTime'] / 1000, datetime.UTC).strftime('%Y-%m-%d'),
                'announcement_id': ann['announcementId'],
                'source_url': STATIC + ann['adjunctUrl'],
                'source_site': '巨潮资讯网 www.cninfo.com.cn（上市公司公开公告）',
                'local_file': str(dest.relative_to(ROOT)),
                'size_bytes': len(blob),
                'sha256': hashlib.sha256(blob).hexdigest(),
                'fetched_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
    if rows:
        with MANIFEST.open('w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print('\n来源登记表已写入 data/manifest.csv（%d 条）' % len(rows))


if __name__ == '__main__':
    main()