"""材料下载、来源登记与运行记录。PDF和台账平铺在data，输出平铺在results。"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import json
import platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone, timedelta
from contextlib import ExitStack
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pymupdf

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parent  # 仓库根目录：data/raw 年报与 agent 台账的公共根
DATA = "data/agent"  # agent 自己的台账与副本命名空间，不覆盖主仓 data/manifest.csv
TZ = timezone(timedelta(hours=8))
HISTORY_RUNS = 20
HISTORY_BYTES = 50 * 1024 * 1024  # 按解压内容计量，压缩ZIP通常更小。


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def within(root: Path, path: Path) -> Path:
    """拒绝清单中的路径越界。"""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"路径不在指定目录内：{path.name}")
    return resolved


class Run:
    """最新结果平铺；历史保留最近20次完整运行，并限制归档内容大小。"""

    def __init__(self, root: Path, command: str, parameters: dict):
        self.id = datetime.now(TZ).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        self.root = root.resolve()
        self.folder = self.root / "results"
        self.folder.mkdir(parents=True, exist_ok=True)
        # 已完成的旧日志在history.zip中；当前日志不再无限追加。
        (self.folder / "events.jsonl").write_text("", encoding="utf-8")
        self.command, self.files, self.events = command, set(), []
        sources = [AGENT_DIR / name for name in (
            "main.py", "materials.py", "extract.py", "finance.py", "llm_check.py", "test.py",
            "requirements.txt", "run.ps1")]
        self.sources = {p.name: p.read_bytes() for p in sources if p.exists()}
        write_json(self.output("run.json"), {
            "schema_version": 2, "run_id": self.id, "started_at": now(),
            "command": command, "parameters": parameters,
            "versions": {"python": platform.python_version(),
                         "PyMuPDF": importlib.metadata.version("PyMuPDF")},
            "python_executable": sys.executable,
            "code_sha256": {name: sha256(blob) for name, blob in self.sources.items()},
        })
        self.event("run_started", command=command)

    def output(self, name: str) -> Path:
        if not name or name in {".", ".."} or any(c in name for c in "/\\:"):
            raise ValueError("结果文件必须直接放在results目录，不能嵌套目录")
        self.files.add(name)
        return self.folder / name

    def event(self, kind: str, **data) -> None:
        entry = json.dumps({"time": now(), "run_id": self.id, "event": kind, **data},
                           ensure_ascii=False) + "\n"
        self.events.append(entry)
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(entry)

    def read(self, path: Path) -> bytes:
        blob = path.read_bytes()
        self.event("file_read", path=str(path.resolve()), sha256=sha256(blob),
                   size_bytes=len(blob))
        return blob

    def finish(self, **summary) -> None:
        outputs = {name: sha256((self.folder / name).read_bytes())
                   for name in sorted(self.files) if name != "summary.json"
                   and (self.folder / name).is_file()}
        write_json(self.output("summary.json"), {
            "run_id": self.id, "finished_at": now(), "output_sha256": outputs,
            "history_policy": {"keep_runs": HISTORY_RUNS, "max_uncompressed_bytes": HISTORY_BYTES,
                               "oversized_latest": "保留完整本次运行，清除更早历史"}, **summary})
        self.event("run_finished", **summary)
        self.save_history()

    def save_history(self) -> None:
        """完整运行一起保留/淘汰；验证临时ZIP后原子替换，失败不损坏原归档。"""
        history, temporary = self.folder / "history.zip", self.folder / "history.tmp"
        current = {f"{self.id}__{name}": (self.folder / name).read_bytes()
                   for name in sorted(self.files) if (self.folder / name).is_file()}
        current[f"{self.id}__events.jsonl"] = "".join(self.events).encode("utf-8")
        current.update({f"{self.id}__source__{name}": blob for name, blob in self.sources.items()})
        size = sum(len(blob) for blob in current.values())
        try:
            with ExitStack() as stack:
                previous = stack.enter_context(ZipFile(history)) if history.exists() else None
                groups = {}
                if previous:
                    for entry in previous.infolist():
                        run_id = entry.filename.split("__", 1)[0]
                        if run_id != self.id:
                            groups.setdefault(run_id, []).append(entry)
                retained = []
                # ZIP写入顺序代表实际运行顺序；同一秒内UUID字典序不代表先后。
                for run_id in reversed(groups):
                    if len(retained) >= HISTORY_RUNS - 1:
                        break
                    entries = groups[run_id]
                    added_size = sum(entry.file_size for entry in entries)
                    if size + added_size > HISTORY_BYTES:
                        break
                    size += added_size
                    retained.insert(0, entries)
                with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
                    for entries in retained:
                        for entry in entries:
                            archive.writestr(entry.filename, previous.read(entry))
                    for name, blob in current.items():
                        archive.writestr(name, blob)
                with ZipFile(temporary) as check:
                    if check.testzip() is not None:
                        raise ValueError("历史归档完整性检查失败，原归档未替换")
            temporary.replace(history)
        finally:
            temporary.unlink(missing_ok=True)


def validate_pdf(blob: bytes, company_code: str | None = None,
                 company_name: str | None = None, report_year: int | None = None) -> dict:
    if not blob.startswith(b"%PDF-"):
        raise ValueError("不是PDF文件，可能下载到了HTML错误页")
    with pymupdf.open(stream=blob, filetype="pdf") as document:
        if document.needs_pass or not document.page_count:
            raise ValueError("PDF加密或无页面")
        sample = []
        for i, page in enumerate(document):
            # 每页实际加载文字内容，避免只检查PDF头。
            content = page.get_text()
            if i < 10:
                sample.append(content)
        text = re.sub(r"\s+", "", "".join(sample))
        if company_code and company_name:
            if company_code not in text and company_name not in text:
                raise ValueError("PDF前10页未核实公司身份")
        if report_year and not re.search(rf"{report_year}年?(?:年度报告|年度報告)", text):
            raise ValueError("PDF前10页未核实报告年度")
        return {"page_count": document.page_count, "identity_checked": bool(report_year),
                "pdf_repaired_by_parser": document.is_repaired}


def load_materials(root: Path) -> list[dict]:
    path = root / DATA / "materials.jsonl"
    if not path.exists():
        return []
    latest = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                old = latest.get(row["document_id"])
                verified = row.get("disclosure_date_status") == "api_timestamp_Asia_Shanghai"
                old_verified = old and old.get("disclosure_date_status") == "api_timestamp_Asia_Shanghai"
                # 再次导入旧清单不应降低已核实来源的质量；全部观察仍保留在台账。
                if not old_verified or verified:
                    latest[row["document_id"]] = row
    return list(latest.values())


def register(root: Path, blob: bytes, metadata: dict, run: Run, by_reference: bool = False) -> dict:
    """登记一份材料。by_reference=True 时不复制 PDF，直接引用 root 下已有文件（要求 metadata 带 local_file）。"""
    code = metadata["company_code"]
    year = int(metadata["report_year"])
    announcement_id = str(metadata["announcement_id"])
    if not re.fullmatch(r"\d{6}", code) or not re.fullmatch(r"\d+", announcement_id):
        raise ValueError("证券代码或公告ID格式不合法")
    checks = validate_pdf(blob, code, metadata["company_name"], year)
    fingerprint = sha256(blob)
    document_id = f"{code}_{year}_{announcement_id}_{fingerprint}"
    if by_reference:
        relative = Path(metadata["local_file"])
        path = within(root, root / relative)
        if not path.is_file():
            raise ValueError(f"by_reference 指向的文件不存在：{relative.as_posix()}")
        if sha256(run.read(path)) != fingerprint:
            raise ValueError("引用文件指纹与登记内容不一致")
        reused = True
    else:
        relative = Path(DATA) / f"{document_id}.pdf"
        path = within(root, root / relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if sha256(run.read(path)) != fingerprint:
                raise ValueError("已有文件与指纹不一致，拒绝覆盖")
            reused = True
        else:
            with path.open("xb") as stream:
                stream.write(blob)
            reused = False
    old = next((r for r in load_materials(root) if r["document_id"] == document_id), None)
    record = {
        **metadata, **checks, "schema_version": 1, "document_id": document_id,
        "local_file": relative.as_posix(), "sha256": fingerprint, "size_bytes": len(blob),
        "first_ingested_at": old["first_ingested_at"] if old else now(),
        "checked_at": now(), "observation_run_id": run.id,
    }
    ledger = root / DATA / "materials.jsonl"
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    run.event("material_registered", document_id=document_id, reused=reused,
              by_reference=by_reference, sha256=fingerprint, source_url=record["source_url"])
    write_csv(root / DATA / "manifest.csv", load_materials(root), [
        "document_id", "company_code", "company_name", "report_year", "announcement_id",
        "title", "disclosed_at", "disclosure_date_status", "source_url", "local_file",
        "sha256", "size_bytes", "page_count", "version_policy", "needs_version_review",
        "first_ingested_at", "checked_at", "license_status",
    ])
    return record


def import_legacy(root: Path, source: Path, run: Run) -> tuple[list[dict], list[dict]]:
    source = source.resolve()
    manifest = source / "data" / "manifest.csv"
    manifest_blob = run.read(manifest)
    content = manifest_blob.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    records, failures = [], []
    root_resolved = root.resolve()
    for row in rows:
        try:
            path = within(source, source / row["local_file"])
            blob = run.read(path)
            if sha256(blob) != row["sha256"] or len(blob) != int(row["size_bytes"]):
                raise ValueError("文件大小或SHA-256与旧清单不一致")
            url = row["source_url"]
            url_date = re.search(r"/finalpage/(\d{4}-\d{2}-\d{2})/", url)
            supplied_date = row["publish_date"]
            date_status = "legacy_unverified"
            if url_date and url_date[1] != supplied_date[:10]:
                date_status = "legacy_date_differs_from_url_path"
            # 源文件已在本仓库内（如主仓 data/raw）⇒ 引用登记，不复制 PDF。
            by_reference = path.is_relative_to(root_resolved)
            metadata = {
                "company_code": row["code"], "company_name": row["name"],
                "report_year": int(row["year"]), "announcement_id": row["announcement_id"],
                "title": row["title"], "source_url": url,
                "source_site": "巨潮资讯网", "disclosed_at": supplied_date,
                "disclosure_date_status": date_status,
                "source_url_path_date": url_date[1] if url_date else None,
                "version_policy": "legacy_selected_full_text",
                "needs_version_review": True, "retrieved_at": None,
                "legacy_fetched_at": row["fetched_at"],
                "imported_from": str(path), "source_manifest_sha256": sha256(manifest_blob),
                "license_status": "公开披露材料；使用及再分发条款待核实，非默认开放许可",
            }
            if by_reference:
                metadata["local_file"] = path.relative_to(root_resolved).as_posix()
            records.append(register(root, blob, metadata, run, by_reference=by_reference))
        except Exception as exc:
            failure = {"company_code": row.get("code"), "report_year": row.get("year"),
                       "error_type": type(exc).__name__, "message": str(exc)}
            failures.append(failure)
            run.event("material_failed", **failure)
    return records, failures


def verify_registry(root: Path, run: Run) -> list[dict]:
    results = []
    for row in load_materials(root):
        try:
            path = within(root, root / row["local_file"])
            blob = run.read(path)
            if sha256(blob) != row["sha256"] or len(blob) != row["size_bytes"]:
                raise ValueError("大小或指纹与登记不一致")
            validate_pdf(blob, row["company_code"], row["company_name"], row["report_year"])
            results.append({"document_id": row["document_id"], "status": "PASS"})
        except Exception as exc:
            results.append({"document_id": row["document_id"], "status": "FAIL", "reason": str(exc)})
    return results


BASE = "https://www.cninfo.com.cn"
STATIC = "https://static.cninfo.com.cn/"
MAX_BYTES = 100 * 1024 * 1024
ALLOWED_HOSTS = {"www.cninfo.com.cn", "static.cninfo.com.cn"}


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.username:
        raise ValueError("只允许巨潮HTTPS地址")


class Client:
    def __init__(self, run: Run, proxy: str | None = None, timeout: int = 20):
        self.run, self.timeout, self.sequence = run, timeout, 0
        handlers = [SafeRedirect()]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"https": proxy}))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, url: str, params: dict | None = None) -> bytes:
        validate_url(url)
        data = None if params is None else urllib.parse.urlencode(params).encode("utf-8")
        for attempt in range(3):
            try:
                self.run.event("http_request", url=url, params=params, attempt=attempt + 1)
                request = urllib.request.Request(url, data=data, headers={
                    "User-Agent": "Mozilla/5.0", "Referer": BASE,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
                with self.opener.open(request, timeout=self.timeout) as response:
                    validate_url(response.geturl())
                    blob = response.read(MAX_BYTES + 1)
                    if len(blob) > MAX_BYTES:
                        raise ValueError("响应超过100MB")
                    self.run.event("http_response", url=response.geturl(), status=response.status,
                                   sha256=sha256(blob), size_bytes=len(blob))
                    return blob
            except urllib.error.HTTPError as exc:
                self.run.event("http_error", error_type=type(exc).__name__, code=exc.code,
                               attempt=attempt + 1)
                if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                self.run.event("http_error", error_type=type(exc).__name__, attempt=attempt + 1)
                if attempt == 2:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError("请求未完成")

    def post(self, path: str, params: dict):
        blob = self.request(BASE + path, params)
        self.sequence += 1
        snapshot = self.run.output(f"response_{self.sequence:03d}.json")
        # 保存API原始响应；解析失败仍有可检查的证据。
        snapshot.write_bytes(blob)
        return json.loads(blob.decode("utf-8"))


def company(client: Client, code: str) -> dict:
    response = client.post("/new/information/topSearch/query", {"keyWord": code, "maxNum": 10})
    if not isinstance(response, list):
        raise ValueError("公司查询响应结构变化")
    for item in response:
        if item.get("code") == code:
            return item
    raise ValueError("找不到该证券代码")


def query_reports(client, code: str, org: str, year: int, as_of: date) -> list[dict]:
    column = "sse" if code.startswith("6") else "szse"
    result, seen = [], set()
    for number in range(1, 201):
        response = client.post("/new/hisAnnouncement/query", {
            "pageNum": number, "pageSize": 50, "column": column, "tabName": "fulltext",
            "stock": f"{code},{org}", "category": "category_ndbg_szsh",
            "seDate": f"{year}-01-01~{as_of.isoformat()}",
        })
        if not isinstance(response, dict) or "announcements" not in response:
            raise ValueError("公告查询响应结构变化")
        items = response["announcements"] or []
        if not isinstance(items, list):
            raise ValueError("公告列表格式变化")
        added = 0
        for item in items:
            ident = str(item["announcementId"])
            if ident not in seen:
                if item.get("secCode", code) != code:
                    raise ValueError("接口返回了其他公司的公告")
                seen.add(ident)
                result.append(item)
                added += 1
        total = response.get("totalAnnouncement")
        if total is not None and len(seen) >= int(total):
            return result
        if response.get("hasMore") is False:
            if total is not None and len(seen) < int(total):
                raise ValueError("分页结束标记与总条数冲突")
            return result
        if not items:
            if response.get("hasMore") or (total is not None and len(seen) < int(total)):
                raise ValueError("分页提前返回空页")
            return result
        if not added:
            raise ValueError("分页返回重复内容，拒绝使用不完整清单")
        if total is None and "hasMore" not in response and len(items) < 50:
            return result
        time.sleep(0.2)
    raise ValueError("超过200页上限")


def select_report(items: list[dict], year: int, policy: str, as_of: date) -> tuple[dict, bool]:
    if policy not in {"first", "latest"}:
        raise ValueError("版本策略必须为 first 或 latest")
    full, notices = [], []
    for item in items:
        title = re.sub(r"<[^>]+>", "", item["announcementTitle"])
        if not re.search(rf"{year}\s*年?\s*年度报告", title):
            continue
        disclosed = datetime.fromtimestamp(item["announcementTime"] / 1000, TZ).date()
        if disclosed > as_of or re.search(r"摘要|英文|English", title, re.I):
            continue
        is_full = re.search(r"年度报告\s*(?:[（(][^）)]*[）)])?\s*$", title)
        if not is_full or re.search(r"取消|撤回|问询|反馈|公告|说明|提示", title):
            notices.append(item)
        else:
            full.append(item)
    full.sort(key=lambda item: (item["announcementTime"], str(item["announcementId"])))
    if not full:
        raise ValueError("未发现可以确认的年度报告全文")
    return (full[0] if policy == "first" else full[-1]), bool(notices) or len(full) > 1


def fetch(root: Path, run: Run, code: str, year: int, policy: str,
          as_of: date, proxy: str | None = None) -> dict:
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("证券代码须为6位数字")
    if year > as_of.year:
        raise ValueError("报告年度晚于检索截止日期")
    client = Client(run, proxy)
    identity = company(client, code)
    candidates = query_reports(client, code, identity["orgId"], year, as_of)
    write_json(run.output("candidates.json"), candidates)
    selected, review = select_report(candidates, year, policy, as_of)
    url = urllib.parse.urljoin(STATIC, selected["adjunctUrl"])
    if url.startswith("http://static.cninfo.com.cn/"):
        url = "https://" + url[len("http://"):]
    validate_url(url)
    blob = client.request(url)
    return register(root, blob, {
        "company_code": code, "company_name": identity.get("zwjc") or identity.get("name") or selected["secName"],
        "report_year": year, "announcement_id": str(selected["announcementId"]),
        "title": re.sub(r"<[^>]+>", "", selected["announcementTitle"]),
        "source_url": url, "source_site": "巨潮资讯网",
        "disclosed_at": datetime.fromtimestamp(selected["announcementTime"] / 1000, TZ).isoformat(),
        "disclosure_date_status": "api_timestamp_Asia_Shanghai",
        "version_policy": policy, "needs_version_review": review,
        "as_of": as_of.isoformat(), "retrieved_at": now(),
        "license_status": "公开披露材料；使用及再分发条款待核实，非默认开放许可",
    }, run)
