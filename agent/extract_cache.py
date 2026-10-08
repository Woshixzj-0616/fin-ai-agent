"""抽取结果缓存与并行：按 PDF sha256 + 抽取器代码指纹缓存，二次运行近零成本。

设计
----
- 缓存键 = material.sha256 + 抽取器代码指纹（extract/words/finance 等变更即失效）
- 证据 evidence_id 本身是身份串哈希，同 PDF 重抽应字节级稳定 ⇒ 适合缓存
- 并行只包「解析」段；run 事件经锁串行写入，保持可复现
"""
from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

from materials import Run, sha256, write_json

CACHE_VERSION = 2
_CACHE_DIRNAME = "data/cache/extract"


def _code_fingerprint() -> str:
    """抽取相关源码指纹；任一文件改动即失效缓存。"""
    here = Path(__file__).resolve().parent
    parts: list[bytes] = []
    for name in ("extract.py", "words.py", "finance.py", "interim.py", "periods.py",
                 "extract_cache.py"):
        p = here / name
        if p.exists():
            parts.append(p.read_bytes())
    return sha256(b"".join(parts))[:16]


def cache_key(material: dict) -> str:
    return f"{material.get('sha256', '')}_{_code_fingerprint()}_v{CACHE_VERSION}"


def cache_path(root: Path, material: dict) -> Path:
    return root / _CACHE_DIRNAME / f"{cache_key(material)}.json"


def load_cache(root: Path, material: dict) -> list[dict] | None:
    path = cache_path(root, material)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("cache_key") != cache_key(material):
        return None
    facts = payload.get("facts")
    return facts if isinstance(facts, list) else None


def save_cache(root: Path, material: dict, facts: list[dict]) -> Path:
    path = cache_path(root, material)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {
        "cache_key": cache_key(material),
        "document_id": material.get("document_id"),
        "sha256": material.get("sha256"),
        "fact_count": len(facts),
        "facts": facts,
    })
    return path


class _RunProxy:
    """线程安全的 Run 转发：事件先入本地缓冲，由主线程合并。"""

    def __init__(self, run: Run, lock: threading.Lock) -> None:
        self._run = run
        self._lock = lock
        self.events: list[dict] = []

    def read(self, path: Path) -> bytes:
        # file_read 由调用方统一打，避免并行重复且省一次 IO 事件噪音
        return Path(path).read_bytes()

    def event(self, kind: str, **data) -> None:
        self.events.append({"event": kind, **data})

    def output(self, name: str):
        return self._run.output(name)

    def flush(self) -> None:
        with self._lock:
            for e in self.events:
                self._run.event(e.pop("event"), **e)
            self.events.clear()


def extract_material_cached(root: Path, material: dict, run: Run | _RunProxy | None = None,
                            use_cache: bool = True) -> tuple[list[dict], bool]:
    """返回 (facts, from_cache)。"""
    if use_cache:
        cached = load_cache(root, material)
        if cached is not None:
            if run is not None:
                run.event("extract_cache_hit", document_id=material.get("document_id"),
                          sha256=material.get("sha256"), fact_count=len(cached))
            return cached, True

    from extract import extract_material

    class _Inner:
        """内层调用：复用已登记的文件读事件，不再重复打 file_read。"""
        def __init__(self, outer):
            self._outer = outer

        def read(self, p):
            return Path(p).read_bytes()

        def event(self, kind, **data):
            if self._outer is not None:
                self._outer.event(kind, **data)

        def output(self, name):
            return self._outer.output(name)

    path = root / material["local_file"]
    if run is not None:
        run.event("file_read", path=str(path.resolve()),
                  sha256=material.get("sha256"), size_bytes=material.get("size_bytes"))
    facts = extract_material(root, material, _Inner(run))
    if use_cache and facts:
        save_cache(root, material, facts)
        if run is not None:
            run.event("extract_cache_store", document_id=material.get("document_id"),
                      sha256=material.get("sha256"), fact_count=len(facts))
    return facts, False


def _worker_extract(root_str: str, material: dict, use_cache: bool):
    """进程池 worker：不共享 Run，只回 facts/错误。"""
    root = Path(root_str)
    try:
        facts, from_cache = extract_material_cached(root, material, None, use_cache=use_cache)
        return material, facts, from_cache, None
    except Exception as exc:  # noqa: BLE001
        return material, [], False, f"{type(exc).__name__}: {exc}"


def extract_many(root: Path, materials: list[dict], run: Run, *,
                 max_workers: int | None = None, use_cache: bool = True,
                 progress=None, prefer_processes: bool = True) -> tuple[list[dict], list[dict]]:
    """并行抽取。PyMuPDF 吃 GIL ⇒ 优先进程池；失败退回线程池。

    返回 (facts, failures)。
    """
    if max_workers is None:
        max_workers = min(8, max(2, (os.cpu_count() or 4) - 1))
    facts: list[dict] = []
    failures: list[dict] = []
    hit = miss = 0
    results: list[tuple] = []

    def _collect(material, got, from_cache, err, events=None):
        nonlocal hit, miss
        if err:
            failures.append({"document_id": material.get("document_id"),
                             "company_code": material.get("company_code"),
                             "report_year": material.get("report_year"),
                             "error": err})
            run.event("extraction_failed", document_id=material.get("document_id"), error=err)
        else:
            facts.extend(got)
            hit += int(from_cache)
            miss += int(not from_cache)
        if events:
            for e in events:
                run.event(e.pop("event"), **e)

    used_pool = "process"
    try:
        if not prefer_processes or len(materials) <= 1:
            raise RuntimeError("use-thread")
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_worker_extract, str(root), m, use_cache) for m in materials]
            done = 0
            for fut in as_completed(futs):
                material, got, from_cache, err = fut.result()
                done += 1
                _collect(material, got, from_cache, err)
                if progress:
                    progress(done, len(materials), material, from_cache, err)
    except Exception:
        used_pool = "thread"
        lock = threading.Lock()

        def _one(material: dict):
            local = _RunProxy(run, lock)
            try:
                got, from_cache = extract_material_cached(root, material, local, use_cache=use_cache)
                return material, got, from_cache, None, list(local.events)
            except Exception as exc:  # noqa: BLE001
                return material, [], False, f"{type(exc).__name__}: {exc}", list(local.events)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_one, m) for m in materials]
            done = 0
            for fut in as_completed(futs):
                material, got, from_cache, err, events = fut.result()
                done += 1
                _collect(material, got, from_cache, err, events)
                if progress:
                    progress(done, len(materials), material, from_cache, err)

    run.event("extract_batch_done", documents=len(materials), evidence=len(facts),
              cache_hits=hit, cache_misses=miss, failures=len(failures),
              max_workers=max_workers, pool=used_pool)
    return facts, failures
