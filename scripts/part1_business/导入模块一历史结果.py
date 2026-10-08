"""将本任务已有模块一结果接入独立工作台的本机历史记录。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.database import UPLOAD_DIR, create_run, get_run, init_db, save_run_pages, update_run  # noqa: E402
from backend.pdf_reader import extract_all_pages  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_artifact_path(attempt_dir: Path, relative_path: str) -> Path | None:
    candidate = (attempt_dir / relative_path).resolve()
    try:
        candidate.relative_to(attempt_dir.resolve())
    except ValueError:
        return None
    return candidate


def load_saved_pages(attempt_dir: Path, manifest: dict[str, Any], pdf_path: Path) -> list[dict[str, Any]]:
    report = manifest.get("report") or {}
    artifact = report.get("extracted_pages_artifact") or {}
    relative_path = artifact.get("path")
    if relative_path:
        saved_path = safe_artifact_path(attempt_dir, str(relative_path))
        if saved_path and saved_path.is_file() and sha256_file(saved_path) == artifact.get("sha256"):
            payload = json.loads(saved_path.read_text(encoding="utf-8"))
            if isinstance(payload, list) and all(isinstance(page, dict) for page in payload):
                return payload

    extracted = extract_all_pages(pdf_path.read_bytes())
    return extracted.selected_pages


def import_existing_results() -> None:
    runtime_dir = ROOT / "data" / "开发运行"
    if not runtime_dir.is_dir():
        print("没有找到既有模块一运行记录；工作台将从新任务开始积累历史。")
        return

    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    imported = 0
    skipped = 0
    page_cache: dict[str, list[dict[str, Any]]] = {}
    result_paths = sorted(runtime_dir.glob("*/*/result.json"))

    for result_path in result_paths:
        run_id = result_path.parts[-3]
        if not re.fullmatch(r"[a-fA-F0-9]{32}", run_id):
            skipped += 1
            continue
        try:
            attempt_dir = result_path.parent
            result_record = json.loads(result_path.read_text(encoding="utf-8"))
            manifest = json.loads((attempt_dir / "manifest.json").read_text(encoding="utf-8"))
            if result_record.get("module_id") != "business" or manifest.get("module_id") != "business":
                skipped += 1
                continue
            if get_run(run_id):
                skipped += 1
                continue

            report = manifest.get("report") or {}
            file_name = Path(str(report.get("file_name") or "年报.pdf")).name
            report_hash = str(report.get("sha256") or result_record.get("report_id") or "")
            if not re.fullmatch(r"[a-fA-F0-9]{64}", report_hash):
                skipped += 1
                continue

            source_path = Path(str(report.get("input_path") or "")).resolve()
            allowed_raw_dir = (Path(r"D:\ChatGPT项目\金融AI智能体") / "data" / "raw").resolve()
            try:
                source_path.relative_to(allowed_raw_dir)
            except ValueError:
                skipped += 1
                continue
            if not source_path.is_file() or sha256_file(source_path) != report_hash:
                skipped += 1
                continue

            local_pdf = UPLOAD_DIR / f"{run_id}.pdf"
            if local_pdf.exists():
                if sha256_file(local_pdf) != report_hash:
                    skipped += 1
                    continue
            else:
                shutil.copyfile(source_path, local_pdf)
                if sha256_file(local_pdf) != report_hash:
                    local_pdf.unlink(missing_ok=True)
                    skipped += 1
                    continue

            if report_hash not in page_cache:
                page_cache[report_hash] = load_saved_pages(attempt_dir, manifest, local_pdf)
            pages = page_cache[report_hash]
            expected_pages = int(report.get("page_count") or len(pages))
            if not pages or len(pages) != expected_pages:
                skipped += 1
                continue

            status = "completed" if result_record.get("status") == "completed" and result_record.get("result") else "failed"
            started_at = manifest.get("started_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
            create_run(
                {
                    "id": run_id,
                    "file_name": file_name[:240],
                    "file_path": str(local_pdf),
                    "analysis_module": "business",
                    "sha256": report_hash,
                    "status": status,
                    "stage": "既有模块一结果 · 已完成" if status == "completed" else "既有模块一任务 · 失败",
                    "created_at": started_at,
                    "updated_at": started_at,
                }
            )
            result = result_record.get("result") if status == "completed" else None
            if result is not None:
                result.setdefault("document_sha256", report_hash)
            read_pages = result_record.get("read_pages") or (result or {}).get("read_pages") or []
            update_run(
                run_id,
                status=status,
                stage="既有模块一结果 · 已完成" if status == "completed" else "既有模块一任务 · 失败",
                page_count=expected_pages,
                read_pages=read_pages,
                error=None if status == "completed" else str(result_record.get("error") or "原始记录显示该任务失败。"),
                result=result,
            )
            save_run_pages(run_id, pages)
            imported += 1
        except Exception as exc:
            print(f"跳过一条无法安全导入的历史记录：{type(exc).__name__}")
            skipped += 1

    print(f"模块一历史记录导入完成：新增 {imported} 条，保留/跳过 {skipped} 条。")


if __name__ == "__main__":
    import_existing_results()
