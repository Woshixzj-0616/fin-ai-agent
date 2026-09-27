# -*- coding: utf-8 -*-
'''年报材料获取 + 来源登记（本项目主脚本）。

来源：队友 2026-09-27 给的独立练习版 fetch_reports_improved.py，已融合进本项目流水线。

产出：
    data/raw/<code>_<name>_<year>年年度报告.pdf   原始材料（只读不改）
    data/manifest.csv                             来源登记表（出处/公告ID/指纹/页数/许可证）
    data/sources/<code>_<year>.json               每份材料一份来源档案
    logs/fetch_runs/<run_id>/                     每次运行留痕（run.json + 候选公告清单）

运行：
    python scripts\\data\\fetch_reports.py                  正常取数（本地已有且公告一致 ⇒ 复用，不重下）
    python scripts\\data\\fetch_reports.py --refresh        强制重下，核对线上内容有没有变
    python scripts\\data\\fetch_reports.py --policy latest  选检索到的最新全文（默认选首次披露版）
    python scripts\\data\\fetch_reports.py --self-test      离线自检，不联网
    python scripts\\data\\fetch_reports.py --proxy http://127.0.0.1:7897

口径（要改先在记忆系统 项目/金融投研智能体/03-决策记录.md 拍板）：
    · 默认只选「首次披露」的年报全文；修订/更正版本另列候选并标 needs_manual_review。
    · 本地已有材料且公告ID一致 ⇒ 复用，不重新下载。
    · 线上出现新版本（公告ID 或内容指纹变了）⇒ 不覆盖，显式报错交人工处理。
    · 只接受 https://static.cninfo.com.cn 的附件地址；不关闭 HTTPS 证书校验。
'''
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data' / 'raw'
SOURCES = ROOT / 'data' / 'sources'
MANIFEST = ROOT / 'data' / 'manifest.csv'
RUNS = ROOT / 'logs' / 'fetch_runs'

COMPANIES = [('600519', '贵州茅台', 'sse'),
             ('000858', '五粮液', 'szse'),
             ('600887', '伊利股份', 'sse')]
YEARS = [2023, 2024, 2025]
SCOPE = ROOT / 'data' / 'scope.csv'
BASE = 'https://www.cninfo.com.cn'
STATIC = 'https://static.cninfo.com.cn/'
TZ = dt.timezone(dt.timedelta(hours=8))
MAX_BYTES = 100 * 1024 * 1024
SOURCE_SITE = '巨潮资讯网 www.cninfo.com.cn（上市公司公开公告）'
LICENSE_STATUS = '公开披露材料；使用条款与再分发权限待人工核实，非默认开放许可'
MANIFEST_FIELDS = ['code', 'name', 'year', 'title', 'publish_date', 'announcement_id',
                   'source_url', 'source_site', 'local_file', 'size_bytes', 'sha256',
                   'pages', 'fetched_at', 'first_fetched_at', 'last_checked_at',
                   'policy', 'needs_manual_review', 'license_status', 'run_id']

def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def load_scope():
    '''读 data/scope.csv 的样本清单：(代码, 名称, 板块, 起始年, 结束年)；没有就退回内置三个。'''
    if SCOPE.exists():
        items = []
        with SCOPE.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if not (row.get('code') or '').strip():
                    continue
                items.append((row['code'].strip(), row['name'].strip(), row['market'].strip(),
                              int(row['start_year']), int(row['end_year'])))
        if items:
            return items
    return [(code, name, column, YEARS[0], YEARS[-1]) for code, name, column in COMPANIES]


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pdf_pages(path):
    '''读本地 PDF 的页数（复用旧材料时用它补齐登记表，不用联网）。'''
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


def local_name(code, name, year):
    '''本地文件名口径：与既有材料一致（改它会让抽取脚本找不到文件）。'''
    return f'{code}_{name}_{year}年年度报告.pdf'


def write_json(path, payload):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
    Path(path).write_bytes(blob)


def validate_pdf(blob):
    '''检查内容与全部页面可解析；不等于已核验财务数据真伪。'''
    if not blob.startswith(b'%PDF-'):
        raise ValueError('返回内容不是PDF，可能是错误页面')
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(blob), strict=True)
    if reader.is_encrypted:
        raise ValueError('加密PDF需要人工处理')
    if not len(reader.pages):
        raise ValueError('PDF没有页面')
    for page in reader.pages:
        _ = page.mediabox
        contents = page.get_contents()
        if contents is not None:
            contents.get_data()
    return len(reader.pages)


class Client:
    def __init__(self, proxy=None):
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({'https': proxy, 'http': proxy}))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, url, params=None):
        if urllib.parse.urlsplit(url).scheme != 'https':
            raise ValueError('只允许HTTPS源地址')
        data = None if params is None else urllib.parse.urlencode(params).encode('utf-8')
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': BASE,
                   'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=data, headers=headers)
                with self.opener.open(req, timeout=60) as response:
                    if urllib.parse.urlsplit(response.geturl()).scheme != 'https':
                        raise ValueError('拒绝HTTPS降级重定向')
                    blob = response.read(MAX_BYTES + 1)
                    if len(blob) > MAX_BYTES:
                        raise ValueError('响应超过100MB限制')
                    return blob
            except urllib.error.HTTPError as exc:
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
            time.sleep(2 ** attempt)

    def post(self, path, params):
        return json.loads(self.request(BASE + path, params).decode('utf-8'))


def org_id(client, code):
    result = client.post('/new/information/topSearch/query', {'keyWord': code, 'maxNum': 10})
    if not isinstance(result, list):
        raise ValueError('公司查询接口结构变化')
    for item in result:
        if item.get('code') == code:
            return item['orgId']
    raise ValueError('找不到公司：' + code)


def query_reports(client, code, org, column, year, as_of):
    '''完整翻页；接口忽略页码或触及上限时失败，不静默采用不完整结果。'''
    result, seen = [], set()
    for number in range(1, 201):
        data = client.post('/new/hisAnnouncement/query', {
            'pageNum': number, 'pageSize': 50, 'column': column, 'tabName': 'fulltext',
            'stock': f'{code},{org}', 'category': 'category_ndbg_szsh',
            'seDate': f'{year}-01-01~{as_of.isoformat()}',
        })
        if not isinstance(data, dict) or 'announcements' not in data:
            raise ValueError('公告查询接口结构变化')
        items = data['announcements'] or []
        if not isinstance(items, list):
            raise ValueError('公告列表格式错误')
        added = 0
        for item in items:
            ident = str(item['announcementId'])
            if ident not in seen:
                seen.add(ident)
                result.append(item)
                added += 1
        total = data.get('totalAnnouncement')
        if data.get('hasMore') is False:
            return result
        if total is not None and len(seen) >= int(total):
            return result
        if not items:
            if data.get('hasMore') or (total is not None and len(seen) < int(total)):
                raise ValueError('分页提前返回空页，结果不完整')
            return result
        if not added:
            raise ValueError('接口重复返回同一页，停止以避免遗漏')
        if total is None and 'hasMore' not in data and len(items) < 50:
            return result
        time.sleep(0.3)
    raise ValueError('超过200页安全上限，需要缩小查询范围')


def select_report(items, year, policy, as_of):
    '''只认标题以「年度报告」结尾的全文；更正/修订另列候选并要求人工复核。'''
    full, notices = [], []
    pattern = re.compile(rf'{year}\s*年?\s*年度报告', re.I)
    for item in items:
        title = re.sub(r'<[^>]+>', '', item['announcementTitle']).strip()
        if not pattern.search(title):
            continue
        date = dt.datetime.fromtimestamp(item['announcementTime'] / 1000, TZ).date()
        if date > as_of or re.search(r'摘要|英文|English', title, re.I):
            continue
        is_full = re.search(r'年度报告\s*(?:[（(][^）)]*[）)])?\s*$', title)
        if re.search(r'取消|撤回|问询|反馈|公告|说明|提示', title) or not is_full:
            notices.append(item)
        else:
            full.append(item)
    full.sort(key=lambda a: (a['announcementTime'], str(a['announcementId'])))
    if not full:
        raise ValueError('未找到可确认的年报全文，请人工检查候选公告')
    selected = full[0] if policy == 'first' else full[-1]
    review = bool(notices) or len(full) > 1
    return selected, review


def source_url(ann):
    url = urllib.parse.urljoin(STATIC, ann['adjunctUrl'])
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != 'static.cninfo.com.cn':
        raise ValueError('附件地址不是允许的巨潮HTTPS域名')
    return url


def load_manifest():
    '''读现有登记表，键 = (公司代码, 年份)。'''
    rows = {}
    if MANIFEST.exists():
        with MANIFEST.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                rows[(row['code'], int(row['year']))] = row
    return rows


def write_manifest(rows):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, '') for key in MANIFEST_FIELDS})


def write_source(row):
    stem = '{}_{}'.format(row['code'], row['year'])
    write_json(SOURCES / (stem + '.json'), row)


def fetch_one(client, folder, code, name, column, year, args, old_row, run_id):
    '''取一家公司一年的材料，返回 (登记行, 动作)。动作：复用 / 下载 / 核对一致。'''
    org = org_id(client, code)
    candidates = query_reports(client, code, org, column, year, args.as_of)
    write_json(folder / f'{code}_{year}_candidates.json', candidates)
    ann, review = select_report(candidates, year, args.policy, args.as_of)
    url = source_url(ann)
    ident = str(ann['announcementId'])
    title = re.sub(r'<[^>]+>', '', ann['announcementTitle']).strip()
    target = RAW / local_name(code, name, year)
    base = {
        'code': code, 'name': name, 'year': year, 'title': title,
        'publish_date': dt.datetime.fromtimestamp(ann['announcementTime'] / 1000, TZ).isoformat(),
        'announcement_id': ident, 'source_url': url, 'source_site': SOURCE_SITE,
        'local_file': str(target.relative_to(ROOT)).replace('\\', '/'),
        'last_checked_at': now(), 'policy': args.policy,
        'needs_manual_review': review, 'license_status': LICENSE_STATUS, 'run_id': run_id,
    }
    if (not args.refresh and target.exists() and old_row
            and old_row.get('announcement_id') == ident and old_row.get('sha256')
            and sha256_file(target) == old_row['sha256']):
        first = old_row.get('first_fetched_at') or old_row.get('fetched_at') or now()
        return dict(base, size_bytes=target.stat().st_size, sha256=old_row['sha256'],
                    pages=old_row.get('pages') or pdf_pages(target), fetched_at=first,
                    first_fetched_at=first), '复用'
    blob = client.request(url)
    pages = validate_pdf(blob)
    sha = digest(blob)
    if target.exists():
        local_sha = sha256_file(target)
        if local_sha != sha:
            raise FileExistsError(
                '线上出现新版本（本地 ' + local_sha[:12] + ' / 线上 ' + sha[:12]
                + '），按口径不覆盖，请人工确认后决定是否换版')
        action = '核对一致'
    else:
        RAW.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + '.part')
        part.write_bytes(blob)
        os.replace(part, target)
        action = '下载'
    first = (old_row or {}).get('first_fetched_at') or (old_row or {}).get('fetched_at') or now()
    return dict(base, size_bytes=len(blob), sha256=sha, pages=pages,
                fetched_at=first, first_fetched_at=first), action


def run(args):
    from pypdf import PdfReader  # 启动前确认依赖，不触发联网安装。
    RAW.mkdir(parents=True, exist_ok=True)
    run_id = dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
    folder = RUNS / run_id
    folder.mkdir(parents=True, exist_ok=False)
    old = load_manifest()
    scope = load_scope()
    rows, failures = [], 0
    counts = {'复用': 0, '下载': 0, '核对一致': 0}
    started = now()
    for code, name, column, first_year, last_year in scope:
        for year in range(first_year, last_year + 1):
            try:
                row, action = fetch_one(args.client, folder, code, name, column,
                                        year, args, old.get((code, year)), run_id)
                rows.append(row)
                counts[action] = counts.get(action, 0) + 1
                flag = '，需人工复核版本' if row['needs_manual_review'] else ''
                print(f'{code} {year}: {action}{flag}')
            except Exception as exc:
                failures += 1
                if (code, year) in old:
                    rows.append(old[(code, year)])  # 失败不丢旧登记
                error = {'code': code, 'year': year, 'time': now(),
                         'error_type': type(exc).__name__, 'message': str(exc)}
                with (folder / 'failures.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(error, ensure_ascii=False) + '\n')
                print(f'{code} {year}: 失败（{type(exc).__name__}），见本次 failures.jsonl')
            time.sleep(0.5)
    order = {item[0]: index for index, item in enumerate(scope)}
    rows.sort(key=lambda row: (order[row['code']], int(row['year'])))
    write_manifest(rows)
    for row in rows:
        write_source(row)
    script_sha = args.script_sha256 or digest(Path(__file__).read_bytes())
    write_json(folder / 'run.json', {
        'started_at': started, 'finished_at': now(), 'as_of': args.as_of.isoformat(),
        'policy': args.policy, 'refresh': bool(args.refresh), 'script_sha256': script_sha,
        'python': sys.version, 'companies': [list(item[:3]) for item in scope],
        'years': sorted({year for item in scope for year in range(item[3], item[4] + 1)}),
        'reused': counts.get('复用', 0), 'downloaded': counts.get('下载', 0),
        'verified': counts.get('核对一致', 0), 'failures': failures,
    })
    print('本次留痕：' + str(folder))
    print('登记表已更新：data/manifest.csv（{} 条）'.format(len(rows)))
    print('复用 {} 份 / 下载 {} 份 / 核对一致 {} 份 / 失败 {} 份'.format(
        counts.get('复用', 0), counts.get('下载', 0), counts.get('核对一致', 0), failures))
    return 1 if failures else 0


def self_test():
    '''只使用内存 PDF 和临时目录，不发网络请求。'''
    import tempfile
    from pypdf import PdfWriter
    global ROOT, RAW, SOURCES, MANIFEST, RUNS
    cutoff = dt.date(2026, 9, 19)

    def ann(ident, title):
        return {'announcementId': str(ident), 'announcementTitle': title,
                'announcementTime': 1700000000000 + ident * 1000,
                'adjunctUrl': f'finalpage/test/{ident}.PDF'}

    def pdf_bytes(size):
        buffer = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=size, height=size)
        writer.write(buffer)
        return buffer.getvalue()

    first = ann(1, '2023年年度报告')
    revised = ann(2, '2023年年度报告（修订版）')
    correction = ann(3, '关于2023年年度报告的更正公告')
    assert select_report([first, revised, correction], 2023, 'first', cutoff) == (first, True)
    assert select_report([first, revised, correction], 2023, 'latest', cutoff) == (revised, True)

    class FakeFeed:
        '''假客户端：公司查询固定回答，公告查询交给 feed()。'''
        def post(self, path, params):
            if path.endswith('topSearch/query'):
                return [{'code': '600519', 'orgId': 'gssh0600519'}]
            return self.feed(params)

        def feed(self, params):
            return {'announcements': [first], 'totalAnnouncement': 1, 'hasMore': False}

    class PagedClient(FakeFeed):
        def feed(self, params):
            return {'announcements': [first] if params['pageNum'] == 1 else [revised],
                    'totalAnnouncement': 2, 'hasMore': params['pageNum'] == 1}

    class NoDownload(FakeFeed):
        def request(self, url, params=None):
            raise AssertionError('本地已有同一公告，不该重新下载')

    class SameContent(FakeFeed):
        def request(self, url, params=None):
            return blob

    class ChangedContent(FakeFeed):
        def request(self, url, params=None):
            return pdf_bytes(200)

    assert len(query_reports(PagedClient(), '600519', 'x', 'sse', 2023, cutoff)) == 2

    blob = pdf_bytes(100)
    assert validate_pdf(blob) == 1
    try:
        validate_pdf(b'<html>error</html>')
    except ValueError:
        pass
    else:
        raise AssertionError('未拒绝HTML错误页')

    with tempfile.TemporaryDirectory(prefix='fetch_reports_test_') as temp:
        root = Path(temp)
        ROOT = root
        RAW = root / 'data' / 'raw'
        SOURCES = root / 'data' / 'sources'
        MANIFEST = root / 'data' / 'manifest.csv'
        RUNS = root / 'logs' / 'fetch_runs'
        RAW.mkdir(parents=True)
        folder = RUNS / 'run-test'
        folder.mkdir(parents=True)
        plain = argparse.Namespace(policy='first', as_of=cutoff, refresh=False,
                                   client=None, script_sha256='test')
        forced = argparse.Namespace(policy='first', as_of=cutoff, refresh=True,
                                    client=None, script_sha256='test')
        target = RAW / local_name('600519', '测试', 2023)
        target.write_bytes(blob)
        old_row = {'announcement_id': '1', 'sha256': digest(blob), 'pages': '1',
                   'fetched_at': '2026-09-27T18:00:00+08:00'}

        row, action = fetch_one(NoDownload(), folder, '600519', '测试', 'sse',
                                2023, plain, old_row, 'run-test')
        assert action == '复用' and row['sha256'] == digest(blob)

        _, action = fetch_one(SameContent(), folder, '600519', '测试', 'sse',
                              2023, forced, old_row, 'run-test')
        assert action == '核对一致'

        before = target.read_bytes()
        try:
            fetch_one(ChangedContent(), folder, '600519', '测试', 'sse',
                      2023, forced, old_row, 'run-test')
        except FileExistsError:
            pass
        else:
            raise AssertionError('线上新版本未被拦截')
        assert target.read_bytes() == before

        write_manifest([row])
        write_source(row)
        assert MANIFEST.exists() and (SOURCES / '600519_2023.json').exists()
        with MANIFEST.open(encoding='utf-8-sig', newline='') as stream:
            assert list(csv.DictReader(stream))[0]['sha256'] == digest(blob)

    print('10项离线检查通过：首次版 / 最新版 / 更正提醒 / 分页 / PDF / 错误页 / '
          '复用不下载 / 强制核对 / 新版本拦截 / 登记表与来源档案')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--policy', choices=['first', 'latest'], default='first',
                        help='first=首次披露版（默认）；latest=检索到的最新全文')
    parser.add_argument('--as-of', type=dt.date.fromisoformat,
                        default=dt.datetime.now(TZ).date(),
                        help='只认这一天之前披露的公告（默认今天）')
    parser.add_argument('--refresh', action='store_true', help='强制重新下载核对线上内容')
    parser.add_argument('--proxy', help='例如 http://127.0.0.1:7897；不关闭TLS验证')
    parser.add_argument('--self-test', action='store_true', help='离线自检，不发网络请求')
    args = parser.parse_args()
    args.script_sha256 = digest(Path(__file__).read_bytes())
    try:
        if args.self_test:
            return self_test()
        args.client = Client(args.proxy)
        return run(args)
    except ModuleNotFoundError as exc:
        if exc.name != 'pypdf':
            raise
        print('缺少依赖，请运行：python -m pip install -r requirements.txt', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
