"""年报材料获取练习版（独立文件，不修改原项目）。Python 3.11+

安装依赖：python -m pip install pypdf
离线自检：python fetch_reports_improved.py --self-test
查询并下载：python fetch_reports_improved.py
通过本机代理：python fetch_reports_improved.py --proxy http://127.0.0.1:7897

默认只选首次披露的年报全文；--policy latest 选择检索到的最新全文。
更正/补充说明单独列入候选记录，不冒充完整年报；发现时标记需人工复核。
这不是“自动确认法律意义上的最新有效版本”。不关闭 HTTPS 证书验证。
运行结果保存在脚本旁的 financial_materials，不接触原项目数据。
未验证线上接口当前可用性；接口结构变化会显式报错。
"""

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

COMPANIES = [('600519', '贵州茅台', 'sse'),
             ('000858', '五粮液', 'szse'), ('600887', '伊利股份', 'sse')]
YEARS = [2023, 2024, 2025]
BASE = 'https://www.cninfo.com.cn'
STATIC = 'https://static.cninfo.com.cn/'
TZ = dt.timezone(dt.timedelta(hours=8))
MAX_BYTES = 100 * 1024 * 1024


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def validate_pdf(blob):
    """检查实际内容及全部页面可解析；不等于已核验财务数据真伪。"""
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
        # 未指定代理时使用 urllib 的系统/环境代理配置。
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
    """完整翻页；接口忽略页码或触及上限时失败，不静默采用不完整结果。"""
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
    full, notices = [], []
    pattern = re.compile(rf'{year}\s*年?\s*年度报告', re.I)
    for item in items:
        title = re.sub(r'<[^>]+>', '', item['announcementTitle'])
        if not pattern.search(title):
            continue
        date = dt.datetime.fromtimestamp(item['announcementTime'] / 1000, TZ).date()
        if date > as_of or re.search(r'摘要|英文|English', title, re.I):
            continue
        # 仅接受标题以“年度报告”及可选版本括注结束的全文候选。
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


def save_material(root, ann, blob, code, name, year, url):
    """内容指纹命名 + 不可覆盖来源记录；旧版本和旧记录保持不动。"""
    pages = validate_pdf(blob)
    sha = digest(blob)
    ident = str(ann['announcementId'])
    if not re.fullmatch(r'\d+', ident):
        raise ValueError('公告ID格式异常')
    filename = f'{code}_{year}_{ident}_{sha}.pdf'
    raw = root / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    target = raw / filename
    if target.exists():
        if digest(target.read_bytes()) != sha:
            raise ValueError('已有文件与其内容指纹不符，不覆盖，请人工检查')
    else:
        # 独占创建，永不覆盖已有材料；中断造成的坏文件下次会显式报错。
        with target.open('xb') as stream:
            stream.write(blob)
    meta = target.with_suffix('.json')
    if meta.exists():
        record = json.loads(meta.read_text(encoding='utf-8'))
        if any(record.get(k) != v for k, v in {
            'sha256': sha, 'source_url': url, 'announcement_id': ident,
            'code': code, 'year': year, 'local_file': str(target.relative_to(root)),
        }.items()):
            raise ValueError('已有来源记录冲突，不覆盖，请人工检查')
        return record
    record = {
        'code': code, 'name': name, 'year': year, 'title': ann['announcementTitle'],
        'announcement_id': ident, 'source_url': url,
        'publish_date': dt.datetime.fromtimestamp(ann['announcementTime'] / 1000, TZ).isoformat(),
        'local_file': str(target.relative_to(root)), 'size_bytes': len(blob),
        'sha256': sha, 'pages': pages, 'first_fetched_at': now(),
        'license_status': '公开披露材料；使用条款和再分发权限待人工核实，非默认开放许可',
    }
    with meta.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    return record


def run(args):
    from pypdf import PdfReader  # 启动前确认依赖，不触发联网安装。
    root = args.output.resolve()
    run_id = dt.datetime.now(TZ).strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
    folder = root / 'runs' / run_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'run.json').write_text(json.dumps({
        'started_at': now(), 'as_of': args.as_of.isoformat(), 'policy': args.policy,
        'script_sha256': digest(Path(__file__).read_bytes()),
        'python': sys.version, 'companies': COMPANIES, 'years': YEARS,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    client = Client(args.proxy)
    failures = 0
    for code, name, column in COMPANIES:
        for year in YEARS:
            try:
                org = org_id(client, code)
                candidates = query_reports(client, code, org, column, year, args.as_of)
                (folder / f'{code}_{year}_candidates.json').write_text(
                    json.dumps(candidates, ensure_ascii=False, indent=2), encoding='utf-8')
                ann, review = select_report(candidates, year, args.policy, args.as_of)
                url = source_url(ann)
                # 每次重新获取选定公告，避免同公告地址内容变化而误用旧文件。
                blob = client.request(url)
                record = save_material(root, ann, blob, code, name, year, url)
                row = dict(record, checked_at=now(), policy=args.policy,
                           needs_manual_review=review, run_id=run_id)
                manifest = folder / 'manifest.csv'
                is_new = not manifest.exists()
                with manifest.open('a', encoding='utf-8-sig' if is_new else 'utf-8', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    if is_new:
                        writer.writeheader()
                    writer.writerow(row)
                print(f'{code} {year}: 已保存' + ('，版本需人工复核' if review else ''))
            except Exception as exc:
                failures += 1
                error = {'code': code, 'year': year, 'time': now(),
                         'error_type': type(exc).__name__, 'message': str(exc)}
                with (folder / 'failures.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(error, ensure_ascii=False) + '\n')
                print(f'{code} {year}: 失败，见本次 failures.jsonl')
            time.sleep(0.5)
    print(f'本次记录：{folder}\n失败任务：{failures}')
    return 1 if failures else 0


def self_test():
    """只使用内存PDF和临时目录，无网络请求。"""
    import tempfile
    from pypdf import PdfWriter
    cutoff = dt.date(2026, 9, 19)
    def ann(ident, title):
        return {'announcementId': str(ident), 'announcementTitle': title,
                'announcementTime': 1700000000000 + ident * 1000,
                'adjunctUrl': f'finalpage/test/{ident}.PDF'}
    a, b = ann(1, '2023年年度报告'), ann(2, '2023年年度报告（修订版）')
    c = ann(3, '关于2023年年度报告的更正公告')
    assert select_report([a, b, c], 2023, 'first', cutoff) == (a, True)
    assert select_report([a, b, c], 2023, 'latest', cutoff) == (b, True)
    class FakeClient:
        def post(self, path, params):
            return {'announcements': [a] if params['pageNum'] == 1 else [b],
                    'totalAnnouncement': 2, 'hasMore': params['pageNum'] == 1}
    assert len(query_reports(FakeClient(), '600519', 'x', 'sse', 2023, cutoff)) == 2
    buffer = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)
    blob = buffer.getvalue()
    assert validate_pdf(blob) == 1
    try:
        validate_pdf(b'<html>error</html>')
    except ValueError:
        pass
    else:
        raise AssertionError('未拒绝HTML错误页')
    with tempfile.TemporaryDirectory(prefix='financial_materials_test_') as temp:
        root = Path(temp)
        record = save_material(root, a, blob, '600519', '测试', 2023, source_url(a))
        again = save_material(root, a, blob, '600519', '测试', 2023, source_url(a))
        assert record == again
        record2 = save_material(root, b, blob, '600519', '测试', 2023, source_url(b))
        assert record['local_file'] != record2['local_file']
    print('7项离线检查通过：首次版、最新版、更正提醒、分页、PDF、错误页、来源复用/版本隔离。')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'financial_materials')
    parser.add_argument('--policy', choices=['first', 'latest'], default='first')
    parser.add_argument('--as-of', type=dt.date.fromisoformat, default=dt.datetime.now(TZ).date())
    parser.add_argument('--proxy', help='例如 http://127.0.0.1:7897；不关闭TLS验证')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    try:
        return self_test() if args.self_test else run(args)
    except ModuleNotFoundError as exc:
        if exc.name != 'pypdf':
            raise
        print('缺少依赖，请运行：python -m pip install pypdf', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
