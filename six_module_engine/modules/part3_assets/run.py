"""Standalone module-three runner using the V3.0.1 context and recorder."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立运行资产质量与经营效率分析模块。")
    parser.add_argument("--pdf", required=True, type=Path, help="待分析年报 PDF")
    parser.add_argument("--config", type=Path, default=Path("D:/ChatGPT项目/金融AI智能体_V3.0.1/.env"), help="本机 DeepSeek 配置文件，不会复制密钥")
    parser.add_argument("--output", type=Path, default=Path("data/开发运行"), help="运行记录输出目录")
    return parser.parse_args(argv)


def run_asset_module(pdf_path: Path, config_file: Path, output_dir: Path) -> dict[str, Any]:
    from backend.core.config import load_runtime_config

    config = load_runtime_config(config_file.resolve())
    from backend.core.context import ReportContext
    from backend.core.recorder import RunRecorder
    from backend.pdf_reader import extract_all_pages, rank_search_pages
    from backend.deepseek_client import ModelCallError
    from modules.part3_assets.entry import MODULE_ID, MODULE_VERSION, run

    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"未找到 PDF：{pdf_path}")
    content = pdf_path.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    extracted = extract_all_pages(content)
    pages = extracted.selected_pages
    pages_by_number = {int(page["page"]): str(page.get("text") or "") for page in pages}
    run_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_dir = output_dir.resolve() / run_id
    recorder = RunRecorder(run_dir, run_id=run_id, module_id=MODULE_ID)
    report = {
        "file_name": pdf_path.name,
        "sha256": file_hash,
        "page_count": extracted.page_count,
        "extracted_page_count": len(pages),
        "config_path": str(config.resolve()),
    }
    recorder.write_manifest({
        "run_id": run_id,
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "report": report,
        "started_at": started,
        "source_revision": _source_revision(Path(__file__).resolve().parents[3]),
    })
    recorder.record("input_file_access", path=str(pdf_path), sha256=file_hash, bytes=len(content), page_count=extracted.page_count)
    recorder.save_artifact("full_extracted_pages", pages)
    context = ReportContext(
        report_id=file_hash,
        file_name=pdf_path.name,
        file_sha256=file_hash,
        page_count=extracted.page_count,
        pages=pages,
        recorder=recorder,
        _get_page=lambda page: pages_by_number.get(page),
        _search_pages=lambda query: rank_search_pages(pages, query, limit=8, excerpt_chars=3_600),
    )
    recorder.record("module_started", module_version=MODULE_VERSION)
    try:
        workflow = run(context)
        module_result = workflow.get("result", workflow)
        analysis_status = module_result.get("analysis_status") if isinstance(module_result, dict) else "unknown"
        run_status = "completed" if analysis_status == "complete" else "partial"
        envelope = {
            "report_id": file_hash,
            "module_id": MODULE_ID,
            "module_version": MODULE_VERSION,
            "status": run_status,
            "analysis_status": analysis_status,
            "error": None,
            "read_pages": workflow.get("read_pages", []),
            "result": module_result,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    except Exception as exc:
        safe_error = str(exc) if isinstance(exc, (ValueError, ModelCallError)) else "模块三运行失败，已保留当前运行记录；请查看安全错误信息并重试。"
        recorder.record("module_failed", error_type=type(exc).__name__, error=safe_error)
        envelope = {
            "report_id": file_hash,
            "module_id": MODULE_ID,
            "module_version": MODULE_VERSION,
            "status": "failed",
            "analysis_status": "failed",
            "error": safe_error,
            "read_pages": [],
            "result": None,
            "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        recorder.write_result(envelope)
        raise
    recorder.write_result(envelope)
    return {"run_id": run_id, "run_dir": str(run_dir), **envelope}


def _source_revision(project_root: Path) -> str | None:
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    args = _read_arguments(argv)
    try:
        result = run_asset_module(args.pdf, args.config, args.output)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({key: result[key] for key in ("run_id", "status", "analysis_status", "module_id", "run_dir")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
