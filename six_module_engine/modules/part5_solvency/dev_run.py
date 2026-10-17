"""Run solvency analysis from this task worktree without editing the shared registry."""

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
from backend.pdf_reader import extract_all_pages
from modules.part5_solvency.retrieval import SOLVENCY_TABLE_LAYOUT_TERMS


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在任务05工作区单独运行模块五。")
    parser.add_argument("--pdf", required=True, type=Path, help="年报 PDF 完整路径")
    parser.add_argument("--config", type=Path, default=None, help="本机 DeepSeek 配置文件路径")
    parser.add_argument("--output", type=Path, default=Path("data/开发运行"), help="私有运行记录目录")
    return parser.parse_args(argv)


def run_one(pdf_path: Path, output_dir: Path, config_file: Path | None = None) -> dict[str, Any]:
    loaded_config = load_runtime_config(config_file)
    from backend.core.context import ReportContext
    from modules.part5_solvency.entry import MODULE_ID, MODULE_VERSION, run

    source = pdf_path.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"未找到 PDF：{source}")
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    extracted = extract_all_pages(content, table_layout_keywords=SOLVENCY_TABLE_LAYOUT_TERMS)
    run_id = uuid.uuid4().hex
    run_dir = output_dir.expanduser().resolve() / run_id
    recorder = RunRecorder(run_dir, run_id=run_id, module_id=MODULE_ID)
    pages = extracted.selected_pages
    pages_ref = recorder.save_artifact("full_extracted_pages", pages)
    recorder.write_manifest(
        {
            "run_id": run_id,
            "module_id": MODULE_ID,
            "module_version": MODULE_VERSION,
            "source_revision": _source_revision(),
            "report": {
                "file_name": source.name,
                "sha256": digest,
                "page_count": extracted.page_count,
                "pages_artifact": pages_ref,
            },
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    recorder.record("input_file_access", file_name=source.name, sha256=digest, page_count=extracted.page_count)
    context = ReportContext(
        report_id=digest,
        file_name=source.name,
        file_sha256=digest,
        page_count=extracted.page_count,
        pages=pages,
        recorder=recorder,
    )
    recorder.progress("模块五：启动", "进行中")
    try:
        workflow = run(context)
        result = workflow.get("result", workflow)
        envelope = {
            "report_id": digest,
            "module_id": MODULE_ID,
            "module_version": MODULE_VERSION,
            "status": "completed",
            "error": None,
            "read_pages": workflow.get("read_pages", []),
            "result": result,
        }
        recorder.write_result(envelope)
        recorder.progress("模块五：结果已保存", "已完成")
        return {"run_id": run_id, "run_dir": str(run_dir), **envelope}
    except Exception as exc:
        recorder.record("module_failed", error_type=type(exc).__name__, error=str(exc))
        recorder.progress("模块五：运行失败", "需要处理", str(exc))
        raise


def _source_revision() -> str | None:
    import subprocess

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return f"{revision}-dirty" if dirty else revision
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        result = run_one(args.pdf, args.output, args.config)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "status": result["status"],
                "module_id": result["module_id"],
                "analysis_status": result["result"].get("analysis_status"),
                "output": result["run_dir"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
