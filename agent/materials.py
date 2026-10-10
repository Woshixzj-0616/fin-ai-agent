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
import zlib
from datetime import date, datetime, timezone, timedelta
from contextlib import ExitStack
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pymupdf
from backend_runtime import load_config

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
        sources = sorted(AGENT_DIR.glob("*.py")) + [AGENT_DIR.parent / "requirements.txt",
                                                  AGENT_DIR / "backend_config.json"]
        self.sources = {p.name: p.read_bytes() for p in sources if p.exists()}
        for path in sorted((AGENT_DIR.parent / "scripts").rglob("*.py")):
            relative = path.relative_to(AGENT_DIR.parent).as_posix()
            self.sources[relative.replace("/", "__")] = path.read_bytes()
        for relative in ("scripts/site_build.py", "scripts/setup_ocr.py", "docs/app.js", "docs/trace.js", "docs/index.html", "docs/style.css"):
            path = AGENT_DIR.parent / relative
            if path.exists():
                self.sources[relative.replace("/", "__")] = path.read_bytes()
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

    def finish(self, *, snapshot_prefix=None, **summary) -> None:
        self.event("run_finished", **summary)
        if snapshot_prefix:
            self.output(f"{snapshot_prefix}_events.jsonl").write_text("".join(self.events), encoding="utf-8")
            self.output(f"{snapshot_prefix}_run.json").write_bytes((self.folder / "run.json").read_bytes())
        from trace import build_trace
        write_json(self.output("trace.json"), {"schema_version": 1, "run_id": self.id,
                                              "steps": build_trace(self.events)})
        outputs = {name: sha256((self.folder / name).read_bytes())
                   for name in sorted(self.files) if name != "summary.json"
                   and (self.folder / name).is_file()}
        write_json(self.output("summary.json"), {
            "run_id": self.id, "finished_at": now(), "output_sha256": outputs,
            "history_policy": {"keep_runs": HISTORY_RUNS, "max_uncompressed_bytes": HISTORY_BYTES,
                               "oversized_latest": "保留完整本次运行，清除更早历史"}, **summary})
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
        from periods import report_period
        identity = report_period(text, report_year)
        if report_year and identity["report_kind"] == "unknown":
            raise ValueError("PDF前10页未核实报告年度")
        return {"page_count": document.page_count, "identity_checked": bool(report_year),
                "pdf_repaired_by_parser": document.is_repaired, **identity}


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
    metadata = dict(metadata)
    fingerprint = sha256(blob)
    local = (str(metadata.get("source_url", "")).startswith(("webui://", "onsite://", "local://"))
             or metadata.get("disclosure_date_status") == "onsite_unverified")
    if local:
        metadata.update(announcement_id=None, local_import_id=f"LOCAL-{fingerprint[:16]}",
                        disclosed_at=None, disclosure_precision="unknown",
                        disclosure_date_status="onsite_unverified", source_kind="local_import",
                        retrieved_at=now(), retrieved_at_status="local_import_time")
    announcement_id = metadata.get("announcement_id")
    if not re.fullmatch(r"\d{6}", code) or (not local and not re.fullmatch(r"\d+", str(announcement_id))):
        raise ValueError("证券代码或公告ID格式不合法")
    checks = validate_pdf(blob, code, metadata["company_name"], year)
    document_id = f"{code}_{year}_{metadata['local_import_id'] if local else announcement_id}_{fingerprint}"
    if not local and "disclosure_precision" not in metadata:
        supplied = str(metadata.get("disclosed_at") or "")
        metadata["disclosure_precision"] = ("date" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", supplied)
                                            else "unknown")
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
        # 上传/现场材料没有“线上更新版本”可追，默认不需要版本复核
        "needs_version_review": metadata.get("needs_version_review", False),
    }
    ledger = root / DATA / "materials.jsonl"
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    run.event("material_registered", document_id=document_id, reused=reused,
              by_reference=by_reference, sha256=fingerprint, source_url=record["source_url"])
    write_csv(root / DATA / "manifest.csv", load_materials(root), [
        "document_id", "company_code", "company_name", "report_year", "announcement_id",
        "local_import_id", "title", "disclosed_at", "disclosure_precision", "disclosure_date_status",
        "retrieved_at", "source_kind", "source_url", "local_file",
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
    def __init__(self, before_redirect=None):
        self.before_redirect = before_redirect

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        if self.before_redirect:
            self.before_redirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS
            or parts.username or parts.password or parts.port not in {None, 443}):
        raise ValueError("只允许巨潮HTTPS地址")


class Client:
    def __init__(self, run: Run, proxy: str | None = None, timeout: int | None = None,
                 *, config: dict | None = None):
        from backend_runtime import validate_config
        settings = config or load_config()
        validate_config(settings)
        self.limits = settings["sources"]
        self.run, self.timeout, self.sequence = run, timeout or self.limits["timeout_seconds"], 0
        self._task_end = time.monotonic() + self.limits["task_deadline_seconds"]
        self._request_end, self._last_start = self._task_end, None
        self._physical, self._redirects = 0, 0
        handlers = [SafeRedirect(self._before_redirect)]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"https": proxy}))
        self.opener = urllib.request.build_opener(*handlers)

    def _remaining(self):
        remaining = min(self._task_end, self._request_end) - time.monotonic()
        if remaining <= 0:
            raise ValueError("来源获取时限耗尽")
        return min(self.timeout, remaining)

    def _before_attempt(self, url: str, *, redirect=False):
        self._remaining()
        if self._physical >= self.limits["max_http_attempts"]:
            raise ValueError("来源物理请求预算耗尽")
        interval = self.limits["min_interval_milliseconds"] / 1000
        wait = max(0, interval - (time.monotonic() - self._last_start)) if self._last_start is not None else 0
        if time.monotonic() + wait >= min(self._task_end, self._request_end):
            raise ValueError("来源剩余时限不足以继续请求")
        if wait:
            time.sleep(wait)
        self._physical += 1
        self._last_start = time.monotonic()
        self.run.event("http_physical_request", url=url, physical_attempt=self._physical,
                       redirect=redirect, started_at=now())

    def _before_redirect(self, url: str):
        self._redirects += 1
        if self._redirects > self.limits["max_redirects"]:
            raise ValueError("来源重定向次数超过上限")
        self._before_attempt(url, redirect=True)

    def _read(self, response):
        limit = min(MAX_BYTES, self.limits["max_bytes"])
        encoding = (response.headers.get("Content-Encoding") or "").lower().strip()
        if encoding not in {"", "identity", "gzip"}:
            raise ValueError("来源返回未支持的内容编码")
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        parts, size, compressed_size = [], 0, 0
        reader = getattr(response, "read1", response.read)
        while True:
            remaining = self._remaining()
            # urllib 的读取超时需要随绝对截止时间收紧；read1 避免等待填满缓冲区。
            fp = getattr(response, "fp", None)
            raw = getattr(fp, "raw", None)
            socket = getattr(raw, "_sock", None)
            if socket is not None:
                socket.settimeout(remaining)
            chunk = reader(65536)
            self._remaining()
            if not chunk:
                break
            compressed_size += len(chunk)
            if compressed_size > limit:
                raise ValueError("来源响应超过大小上限")
            if decoder:
                try:
                    chunk = decoder.decompress(chunk, limit - size + 1)
                except zlib.error:
                    raise ValueError("来源gzip响应损坏") from None
                if decoder.unconsumed_tail or decoder.unused_data:
                    raise ValueError("来源gzip解码超限或含未处理尾部")
            size += len(chunk)
            if size > limit:
                raise ValueError("来源响应超过大小上限")
            parts.append(chunk)
        if decoder and not decoder.eof:
            raise ValueError("来源gzip响应未完整结束")
        return b"".join(parts)

    def request(self, url: str, params: dict | None = None) -> bytes:
        from llm_http import retry_after_seconds
        validate_url(url)
        self._request_end = min(self._task_end, time.monotonic() + self.limits["request_deadline_seconds"])
        data = None if params is None else urllib.parse.urlencode(params).encode("utf-8")
        for attempt in range(self.limits["max_attempts"]):
            retry_after = None
            self._redirects = 0
            self._before_attempt(url)
            began = time.monotonic()
            self.run.event("http_request", url=url, params=params, attempt=attempt + 1)
            try:
                request = urllib.request.Request(url, data=data, headers={
                    "User-Agent": "Mozilla/5.0", "Referer": BASE,
                    "Accept-Encoding": "gzip",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
                with self.opener.open(request, timeout=self._remaining()) as response:
                    validate_url(response.geturl())
                    blob = self._read(response)
                    self.run.event("http_response", url=response.geturl(), status=response.status,
                                   sha256=sha256(blob), size_bytes=len(blob),
                                   elapsed_seconds=round(time.monotonic() - began, 4),
                                   physical_attempts=self._physical)
                    return blob
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
                exc.close()
                self.run.event("http_error", error_type=type(exc).__name__, code=status,
                               attempt=attempt + 1, elapsed_seconds=round(time.monotonic() - began, 4))
                if status not in {408, 429, 500, 502, 503, 504} or attempt == self.limits["max_attempts"] - 1:
                    raise ValueError(f"来源接口HTTP {status}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                self.run.event("http_error", error_type=type(exc).__name__, attempt=attempt + 1,
                               elapsed_seconds=round(time.monotonic() - began, 4))
                if attempt == self.limits["max_attempts"] - 1:
                    raise ValueError("来源连接失败或超时，有限重试已用尽") from None
            except ValueError as exc:
                self.run.event("http_validation_failed", reason=str(exc), attempt=attempt + 1,
                               elapsed_seconds=round(time.monotonic() - began, 4))
                raise
            wait = retry_after if retry_after is not None else 2 ** attempt
            if time.monotonic() + wait >= min(self._task_end, self._request_end):
                raise ValueError("来源剩余时限不足以重试")
            time.sleep(wait)
        raise ValueError("来源请求未完成")

    def post(self, path: str, params: dict):
        blob = self.request(BASE + path, params)
        self.sequence += 1
        snapshot = self.run.output(f"response_{self.sequence:03d}.json")
        snapshot.write_bytes(blob)
        return json.loads(blob.decode("utf-8-sig"))


def company(client: Client, code: str) -> dict:
    response = client.post("/new/information/topSearch/query", {"keyWord": code, "maxNum": 10})
    if not isinstance(response, list):
        raise ValueError("公司查询响应结构变化")
    for item in response:
        if not isinstance(item, dict):
            raise ValueError("公司查询响应结构变化")
        if item.get("code") == code:
            if not isinstance(item.get("orgId"), str) or not item["orgId"]:
                raise ValueError("公司查询缺少机构标识")
            return item
    raise ValueError("找不到该证券代码")


def query_reports(client, code: str, org: str, year: int, as_of: date) -> list[dict]:
    column = "sse" if code.startswith("6") else "szse"
    result, seen, identities = [], set(), {}
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
            if (not isinstance(item, dict) or not re.fullmatch(r"\d+", str(item.get("announcementId", "")))
                    or not isinstance(item.get("announcementTitle"), str)
                    or not isinstance(item.get("adjunctUrl"), str)
                    or type(item.get("announcementTime")) not in {int, float}):
                raise ValueError("公告关键字段缺失或类型变化")
            ident = str(item["announcementId"])
            signature = tuple(item.get(key) for key in ("announcementTitle", "announcementTime", "adjunctUrl", "secCode"))
            if ident in identities and identities[ident] != signature:
                raise ValueError("同一公告ID出现不同元数据，拒绝静默去重")
            identities[ident] = signature
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
    selection = {
        "policy": policy, "as_of": as_of.isoformat(), "report_year": year,
        "selected_announcement_id": str(selected["announcementId"]),
        "candidate_ids": [str(item["announcementId"]) for item in candidates],
        "candidates_sha256": sha256(json.dumps(candidates, ensure_ascii=False, sort_keys=True).encode()),
        "query_completeness": "complete", "needs_version_review": review,
        "selection_kind": "first_full_text_candidate" if policy == "first" else "latest_full_text_candidate",
        "effective_version_confirmed": False,
        "limitation": "按fin策略选择全文候选；未解析更正/撤回关系，不等于确认最新有效版本",
    }
    write_json(run.output("version_selection.json"), selection)
    url = urllib.parse.urljoin(STATIC, selected["adjunctUrl"])
    if url.startswith("http://static.cninfo.com.cn/"):
        url = "https://" + url[len("http://"):]
    validate_url(url)
    blob = client.request(url)
    disclosed = datetime.fromtimestamp(selected["announcementTime"] / 1000, TZ)
    date_only = disclosed.time().replace(tzinfo=None) == datetime.min.time()
    return register(root, blob, {
        "company_code": code, "company_name": identity.get("zwjc") or identity.get("name") or selected["secName"],
        "report_year": year, "announcement_id": str(selected["announcementId"]),
        "title": re.sub(r"<[^>]+>", "", selected["announcementTitle"]),
        "source_url": url, "source_site": "巨潮资讯网",
        "disclosed_at": disclosed.date().isoformat() if date_only else disclosed.isoformat(),
        "disclosure_precision": "date" if date_only else "datetime_seconds",
        "disclosure_precision_note": "当地零点时间戳按日期级记录，不补造发布时间",
        "disclosure_date_status": "api_timestamp_Asia_Shanghai",
        "version_policy": policy, "needs_version_review": review,
        "as_of": as_of.isoformat(), "retrieved_at": now(),
        "source_kind": "official_disclosure", "version_selection": selection,
        "license_status": "公开披露材料；使用及再分发条款待核实，非默认开放许可",
    }, run)
