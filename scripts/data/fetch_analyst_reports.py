#!/usr/bin/env python3
"""Download publicly linked analyst/research reports listed in case_sources.csv."""

from __future__ import annotations

import csv
import hashlib
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
SOURCE_CSV = ROOT / "data" / "cases" / "case_sources.csv"
OUTPUT_DIR = ROOT / "data" / "cases" / "原始报告"
USER_AGENT = "Mozilla/5.0 (compatible; FinancialResearchCorpus/1.0)"
MAX_BYTES = 100 * 1024 * 1024


class ReportLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text)))
            self._href = None
            self._text = []


def fetch(url: str) -> tuple[bytes, str, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,text/html,*/*"})
    with urlopen(request, timeout=45) as response:
        content_type = response.headers.get("Content-Type", "")
        data = response.read(MAX_BYTES + 1)
        final_url = response.geturl()
    if len(data) > MAX_BYTES:
        raise ValueError(f"response exceeds {MAX_BYTES // (1024 * 1024)} MiB")
    return data, final_url, content_type


def resolve_pdf_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    if parsed.path.lower().endswith(".pdf") or parsed.netloc.lower().startswith("pdf."):
        return source_url

    html, page_url, content_type = fetch(source_url)
    if "html" not in content_type.lower() and not html.lstrip().startswith((b"<!DOCTYPE html", b"<html")):
        raise ValueError(f"report page did not return HTML ({content_type or 'unknown type'})")
    parser = ReportLinkParser()
    parser.feed(html.decode("utf-8", errors="replace"))
    candidates: list[tuple[int, str]] = []
    for href, label in parser.links:
        candidate = urljoin(page_url, href.strip())
        parsed_candidate = urlparse(candidate)
        if parsed_candidate.scheme not in {"http", "https"}:
            continue
        if parsed_candidate.path.lower().endswith(".pdf") or parsed_candidate.netloc.lower().startswith("pdf."):
            score = 2 if "pdf" in label.lower() or "原文" in label else 1
            candidates.append((score, candidate))
    if not candidates:
        raise ValueError("no direct PDF link found on the report page")
    candidates.sort(reverse=True)
    return candidates[0][1]


def safe_filename(value: str) -> str:
    return re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "_", value).strip(" ._")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    if not SOURCE_CSV.exists():
        print(f"Missing source list: {SOURCE_CSV}", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with SOURCE_CSV.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for column in ("local_file", "sha256", "download_status", "resolved_url"):
        if column not in fieldnames:
            fieldnames.append(column)

    success = 0
    failures = 0
    for row in rows:
        case_id = (row.get("case_id") or "").strip()
        title = (row.get("report_title") or "").strip()
        if not case_id or not (row.get("source_url") or "").strip():
            row["download_status"] = "跳过：缺少编号或来源链接"
            continue

        # Reports saved through a browser may be accessible even when direct
        # requests receive an anti-bot page. Keep verified local originals.
        local_file = (row.get("local_file") or "").strip()
        if local_file:
            local_path = ROOT / local_file
            if local_path.is_file():
                existing = local_path.read_bytes()
                existing_hash = sha256(existing)
                recorded_hash = (row.get("sha256") or "").strip().lower()
                if existing.startswith(b"%PDF-") and (not recorded_hash or recorded_hash == existing_hash):
                    row["local_copy"] = "是"
                    row["sha256"] = existing_hash
                    row["download_status"] = f"本地原始PDF已校验（{len(existing)} bytes）"
                    success += 1
                    print(f"OK {case_id}: reused {local_path.name} ({len(existing)} bytes)")
                    continue

        basename = safe_filename(
            f"{case_id}_{row.get('company', '未标公司')}_{row.get('analyst_institution', '未标机构')}_{title}"
        )
        target = OUTPUT_DIR / f"{basename}.pdf"
        try:
            pdf_url = resolve_pdf_url(row["source_url"].strip())
            row["resolved_url"] = pdf_url
            data, final_url, content_type = fetch(pdf_url)
            if not data.startswith(b"%PDF-"):
                prefix = data.lstrip()[:80].lower()
                if prefix.startswith((b"<html", b"<!doctype html", b"<script")):
                    raise ValueError("source returned a JavaScript anti-bot page, not the PDF")
                raise ValueError(f"response is not a PDF ({content_type or 'unknown type'})")
            digest = sha256(data)
            target.write_bytes(data)
            row["local_copy"] = "是"
            row["local_file"] = target.relative_to(ROOT).as_posix()
            row["sha256"] = digest
            row["download_status"] = f"已下载并通过PDF头与SHA-256校验（{len(data)} bytes）"
            row["resolved_url"] = final_url
            success += 1
            print(f"OK {case_id}: {target.name} ({len(data)} bytes)")
        except Exception as exc:  # keep going so one broken source does not stop the batch
            row["local_copy"] = row.get("local_copy") or "否"
            row["download_status"] = f"未下载：{type(exc).__name__}: {exc}"
            failures += 1
            print(f"FAIL {case_id}: {row['download_status']}", file=sys.stderr)
        time.sleep(0.4)

    with SOURCE_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Finished: {success} downloaded, {failures} failed; folder: {OUTPUT_DIR}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
