# -*- coding: utf-8 -*-
"""下载巨潮资讯网披露的上市公司年度报告全文，并保留公告版本。

运行：python scripts/data/fetch_reports.py
公司范围：data/companies.csv；默认财年：2023—2025。

原始公告版本保存在 data/raw/versions/，每家公司每个财年最新的可识别全文
另复制到 data/raw/ 供现有抽取脚本读取。清单按公告 ID 合并，不清除旧记录。
“最新可识别全文”仅是默认选择，遇到更正/修订时仍须人工检查公告说明。
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data' / 'raw'
VERSIONS = RAW / 'versions'
COMPANIES_FILE = ROOT / 'data' / 'companies.csv'
MANIFEST = ROOT / 'data' / 'manifest.csv'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
CNINFO = 'https://www.cninfo.com.cn/new/hisAnnouncement/query'
TOPSEARCH = 'https://www.cninfo.com.cn/new/information/topSearch/query'
STATIC = 'https://static.cninfo.com.cn/'
SOURCE_SITE = '巨潮资讯网（上市公司公开公告）'
FIELDS = [
    'code', 'name', 'year', 'sector', 'title', 'publish_date', 'announcement_id',
    'version_note', 'source_url', 'source_site', 'local_file', 'canonical_file',
    'selected_for_default', 'size_bytes', 'sha256', 'pdf_header_valid',
    'fetched_at', 'usage_rights_notes',
]
EXCLUDE = re.compile(r'摘要|英文|english|问询|反馈|取消|提示性公告|审计报告|更正公告', re.I)
REVISION = re.compile(r'更正|修订|更新|补充|重述')


def request_bytes(url, data=None, timeout=60):
    headers = {'User-Agent': UA}
    if data is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
    req = urllib.request.Request(url, data=data, headers=headers)
    last_error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError('请求失败：%s (%s)' % (url, last_error))


def post_json(url, params):
    data = urllib.parse.urlencode(params).encode('utf-8')
    return json.loads(request_bytes(url, data=data).decode('utf-8'))


def load_companies(limit=None):
    with COMPANIES_FILE.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get('include', '1').strip() not in ('0', 'false', '否')]
    return rows[:limit] if limit else rows


def load_manifest():
    if not MANIFEST.exists():
        return []
    with MANIFEST.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def save_manifest(rows):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        w.writeheader()
        for row in rows:
            w.writerow({key: row.get(key, '') for key in FIELDS})


def org_id(code):
    res = post_json(TOPSEARCH, {'keyWord': code, 'maxNum': '10'})
    for item in res or []:
        if item.get('code') == code:
            return item['orgId']
    raise RuntimeError('找不到 %s 的 orgId' % code)


def candidate_reports(code, org, column, year):
    found = []
    page_num, page_size = 1, 30
    while page_num <= 30:
        res = post_json(CNINFO, {
            'pageNum': page_num, 'pageSize': page_size, 'column': column,
            'tabName': 'fulltext', 'stock': '%s,%s' % (code, org),
            'category': 'category_ndbg_szsh',
            # Keep the full history through today so late corrections are not missed.
            'seDate': '%d-01-01~%s' % (year, dt.date.today().isoformat()),
        })
        announcements = res.get('announcements') or []
        found.extend(announcements)
        total = int(res.get('totalRecord') or res.get('totalAnnouncement') or 0)
        if not announcements or len(found) >= total:
            break
        page_num += 1

    year_pat = re.compile(r'%d\s*年?\s*年度报告' % year)
    unique = {}
    for ann in found:
        title = ann.get('announcementTitle', '')
        if not year_pat.search(title) or EXCLUDE.search(title):
            continue
        if not ann.get('adjunctUrl') or not ann.get('announcementId'):
            continue
        unique[str(ann['announcementId'])] = ann
    return sorted(unique.values(), key=lambda a: int(a.get('announcementTime') or 0))


def pdf_url(ann):
    return urllib.parse.urljoin(STATIC, ann['adjunctUrl'].lstrip('/'))


def safe_name(text):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', text).strip('_')


def version_path(code, name, year, announcement_id):
    return VERSIONS / ('%s_%s_%d_%s.pdf' % (code, safe_name(name), year, announcement_id))


def is_pdf(path):
    try:
        with path.open('rb') as f:
            head = f.read(8)
        return head.startswith(b'%PDF-') and path.stat().st_size > 100_000
    except OSError:
        return False


def fetch_candidate(ann, company, year, previous_by_id):
    code, name = company['code'], company['name']
    announcement_id = str(ann['announcementId'])
    dest = version_path(code, name, year, announcement_id)
    previous = previous_by_id.get(announcement_id, {})
    old_file = previous.get('local_file', '')
    old_path = ROOT / old_file if old_file else None

    if not dest.exists() and old_path and old_path.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_path, dest)
    if not dest.exists():
        blob = request_bytes(pdf_url(ann), timeout=120)
        if not blob.startswith(b'%PDF-') or len(blob) <= 100_000:
            raise RuntimeError('%s %d 公告 %s 下载结果不像有效年报 PDF' % (code, year, announcement_id))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        time.sleep(0.7)
    if not is_pdf(dest):
        raise RuntimeError('PDF 校验失败：%s' % dest)

    blob_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
    timestamp = int(ann.get('announcementTime') or 0)
    published = dt.datetime.fromtimestamp(timestamp / 1000, dt.timezone.utc).date().isoformat()
    title = ann.get('announcementTitle', '')
    return {
        **previous,
        'code': code, 'name': name, 'year': year, 'sector': company.get('sector', ''),
        'title': title, 'publish_date': published, 'announcement_id': announcement_id,
        'version_note': '标题疑含更正/修订' if REVISION.search(title) else '标题未标明修订；仍须核对公告',
        'source_url': pdf_url(ann), 'source_site': SOURCE_SITE,
        'local_file': dest.relative_to(ROOT).as_posix(),
        'size_bytes': dest.stat().st_size, 'sha256': blob_hash,
        'pdf_header_valid': 'yes',
        'fetched_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
        'usage_rights_notes': '上市公司公开披露文件；保留来源与用途说明，公开可读不等于可再分发',
    }


def main():
    parser = argparse.ArgumentParser(description='下载公司清单中的上市公司年度报告')
    parser.add_argument('--start-year', type=int, default=2023)
    parser.add_argument('--end-year', type=int, default=2025)
    parser.add_argument('--limit-companies', type=int, default=0,
                        help='仅处理前 N 家，默认处理完整清单')
    args = parser.parse_args()
    if args.end_year < args.start_year:
        parser.error('--end-year 不能早于 --start-year')

    RAW.mkdir(parents=True, exist_ok=True)
    VERSIONS.mkdir(parents=True, exist_ok=True)
    companies = load_companies(args.limit_companies or None)
    rows = load_manifest()
    by_id = {str(r.get('announcement_id', '')): r for r in rows if r.get('announcement_id')}
    errors = []

    for company in companies:
        code, name, column = company['code'], company['name'], company['column']
        print('[%s %s | %s]' % (code, name, company.get('sector', '')))
        try:
            org = org_id(code)
        except Exception as exc:
            errors.append('%s orgId: %s' % (code, exc))
            print('  查询公司失败：%s' % exc)
            continue
        for year in range(args.start_year, args.end_year + 1):
            try:
                candidates = candidate_reports(code, org, column, year)
                if not candidates:
                    print('  %d：没有找到可识别的年度报告全文' % year)
                    continue
                year_rows = []
                for ann in candidates:
                    ann_id = str(ann['announcementId'])
                    try:
                        row = fetch_candidate(ann, company, year, by_id)
                        by_id[ann_id] = row
                        year_rows.append(row)
                    except Exception as exc:
                        errors.append('%s %d %s: %s' % (code, year, ann_id, exc))
                        print('  版本 %s 下载失败：%s' % (ann_id, exc))
                if not year_rows:
                    continue

                chosen = max(year_rows, key=lambda r: (r.get('publish_date', ''), r.get('announcement_id', '')))
                canonical = RAW / ('%s_%s_%d年年度报告.pdf' % (code, safe_name(name), year))
                source = ROOT / chosen['local_file']
                if source.resolve() != canonical.resolve():
                    shutil.copy2(source, canonical)
                for row in year_rows:
                    row['canonical_file'] = canonical.relative_to(ROOT).as_posix()
                    row['selected_for_default'] = 'yes' if row['announcement_id'] == chosen['announcement_id'] else 'no'
                print('  %d：保存 %d 个版本；默认版本 %s（%s）' % (
                    year, len(year_rows), chosen['announcement_id'], chosen['publish_date']))
            except Exception as exc:
                errors.append('%s %d: %s' % (code, year, exc))
                print('  %d 查询失败：%s' % (year, exc))

    merged = {str(r.get('announcement_id', '')): r for r in rows if r.get('announcement_id')}
    for ann_id, row in by_id.items():
        if ann_id:
            merged[ann_id] = row
    # Keep legacy rows without announcement IDs rather than discarding existing provenance.
    legacy = [r for r in rows if not r.get('announcement_id')]
    save_manifest(list(merged.values()) + legacy)
    print('\n总清单已合并写入 data/manifest.csv：%d 个公告版本' % len(merged))
    if errors:
        print('有 %d 项未完成；详见上方错误。已完成项目和历史清单均已保留。' % len(errors))


if __name__ == '__main__':
    main()
