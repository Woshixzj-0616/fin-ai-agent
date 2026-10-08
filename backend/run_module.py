"""Run one registered module against a local annual-report PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.config import PROJECT_ROOT, load_runtime_config
from backend.core.recorder import RunRecorder
from backend.pdf_reader import extract_all_pages, select_initial_pages


def _read_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在本地独立运行一个财报分析模块。")
    parser.add_argument("--module", required=True, help="模块身份，例如 business 或 profit")
    parser.add_argument("--pdf", required=True, type=Path, help="待分析的年报 PDF")
    parser.add_argument("--output", type=Path, default=Path("data/开发运行"), help="运行记录输出目录")
    parser.add_argument("--config", type=Path, default=None, help="本机 .env 配置文件；不复制密钥到工作目录")
    return parser.parse_args(argv)


def run_one(module_id: str, pdf_path: Path, output_dir: Path, config_file: Path | None = None) -> dict[str, Any]:
    config = load_runtime_config(config_file)
    from backend.module_registry import registrations
    from backend.core.context import ReportContext

    available = registrations()
    if module_id not in available:
        raise ValueError(f"模块 {module_id!r} 尚无真实入口。当前可运行：{', '.join(available)}")
    registration = available[module_id]
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"未找到 PDF：{pdf_path}")
    content = pdf_path.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    extracted = extract_all_pages(content)
    run_id = uuid.uuid4().hex
    attempt_id = uuid.uuid4().hex[:12]
    run_dir = output_dir.resolve() / run_id / attempt_id
    recorder = RunRecorder(run_dir, run_id=run_id, module_id=module_id)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    recorder.record(
        "input_file_access",
        path=str(pdf_path),
        sha256=file_hash,
        bytes=len(content),
        page_count=extracted.page_count,
        extracted_page_count=len(extracted.selected_pages),
    )
    pages_ref = recorder.save_artifact("full_extracted_pages", extracted.selected_pages)
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    manifest = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "module_id": module_id,
        "module_version": registration.version,
        "source_revision": _source_revision(),
        "report": {
            "file_name": pdf_path.name,
            "input_path": str(pdf_path),
            "sha256": file_hash,
            "page_count": extracted.page_count,
            "extracted_pages_artifact": pages_ref,
        },
        "model": model,
        "config_path": str(config.resolve()),
        "started_at": started,
    }
    recorder.write_manifest(manifest)
    initial_pages = select_initial_pages(extracted.selected_pages, analysis_module=module_id)
    recorder.save_artifact("selected_initial_pages", initial_pages)
    recorder.record(
        "initial_pages_selected",
        pages=[int(page["page"]) for page in initial_pages],
        candidate_count=len(initial_pages),
    )
    context = ReportContext(
        report_id=file_hash,
        file_name=pdf_path.name,
        file_sha256=file_hash,
        page_count=extracted.page_count,
        pages=extracted.selected_pages,
        recorder=recorder,
        initial_pages=initial_pages,
    )
    recorder.record("module_started", module_version=registration.version)
    recorder.progress("开始分析", "进行中")
    try:
        workflow = registration.run(context)
        module_result = workflow.get("result", workflow)
        trace = workflow.get("trace", [])
        if isinstance(trace, list):
            recorder.save_artifact("legacy_workflow_trace", trace)
        read_pages = workflow.get("read_pages", [])
        recorder.record("module_result_ready", read_pages=read_pages)
        envelope = {
            "report_id": file_hash,
            "module_id": module_id,
            "module_version": registration.version,
            "status": "completed",
            "error": None,
            "read_pages": read_pages,
            "result": module_result,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        recorder.write_result(envelope)
        recorder.progress("分析完成", "已完成")
        return {"run_id": run_id, "attempt_id": attempt_id, "run_dir": str(run_dir), **envelope}
    except Exception as exc:
        error = str(exc)
        recorder.record("module_failed", error_type=type(exc).__name__, error=error)
        envelope = {
            "report_id": file_hash,
            "module_id": module_id,
            "module_version": registration.version,
            "status": "failed",
            "error": error,
            "read_pages": [],
            "result": None,
            "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        recorder.write_result(envelope)
        recorder.progress("分析失败", "需要处理", error)
        raise


def _source_revision() -> str | None:
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
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
        result = run_one(args.module, args.pdf, args.output, args.config)
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "run_id": result["run_id"],
            "status": result["status"],
            "output": result["run_dir"],
            "module": result["module_id"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
