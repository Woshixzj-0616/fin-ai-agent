"""Standalone developer runner for the cashflow module (not yet registered in V3.0.1)."""

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


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="独立运行模块四：现金流与利润兑现分析。")
    parser.add_argument("--pdf", required=True, type=Path, help="年报 PDF 路径")
    parser.add_argument("--config", type=Path, default=None, help="本机 DeepSeek 配置文件；不复制密钥")
    parser.add_argument("--output", type=Path, default=Path("data/开发运行"), help="模块运行记录目录")
    return parser.parse_args(argv)


def run_one(pdf_path: Path, output_dir: Path, config_file: Path | None = None) -> dict[str, Any]:
    if config_file:
        config_file = config_file.expanduser().resolve()
        os.environ["FINLAB_CONFIG_PATH"] = str(config_file)

    from backend.core.config import load_runtime_config

    config_path = load_runtime_config(config_file)
    from backend.core.context import ReportContext
    from backend.core.recorder import RunRecorder
    from modules.part4_cashflow.agent import MODULE_ID, MODULE_VERSION
    from modules.part4_cashflow.entry import run as run_cashflow
    from backend.pdf_reader import extract_all_pages

    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到年报 PDF：{pdf_path}")
    content = pdf_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    extracted = extract_all_pages(content)
    run_id = uuid.uuid4().hex
    run_dir = output_dir.expanduser().resolve() / run_id / "attempt-0001"
    recorder = RunRecorder(run_dir, run_id=run_id, module_id=MODULE_ID)
    recorder.write_manifest({
        "run_id": run_id,
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "report": {
            "file_name": pdf_path.name,
            "sha256": digest,
            "page_count": extracted.page_count,
        },
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "config_path": str(config_path.resolve()),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    recorder.save_artifact("full_extracted_pages", extracted.selected_pages)
    context = ReportContext(
        report_id=digest,
        file_name=pdf_path.name,
        file_sha256=digest,
        page_count=extracted.page_count,
        pages=extracted.selected_pages,
        recorder=recorder,
    )
    try:
        workflow = run_cashflow(context)
        result = workflow.get("result", workflow)
        envelope = {
            "report_id": digest,
            "module_id": MODULE_ID,
            "module_version": MODULE_VERSION,
            "status": "completed" if result.get("analysis_completeness") != "partial" else "partial",
            "error": None,
            "read_pages": workflow.get("read_pages", []),
            "result": result,
            "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        recorder.write_result(envelope)
        return {"run_id": run_id, "run_dir": str(run_dir), **envelope}
    except Exception as exc:
        safe_error = str(exc)
        if any(token in safe_error.lower() for token in ("api_key", "authorization", "bearer", "sk-")):
            safe_error = "模型配置或调用失败；详细凭据已省略。"
        recorder.record("module_failed", error_type=type(exc).__name__, error=safe_error)
        recorder.save_artifact("cashflow_failure_state", {
            "error_type": type(exc).__name__,
            "error": safe_error,
        })
        raise RuntimeError(f"模块四运行失败（{type(exc).__name__}）：{safe_error}; 运行记录：{run_dir}") from exc


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        result = run_one(args.pdf, args.output, args.config)
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "run_id": result["run_id"],
        "status": result["status"],
        "module": result["module_id"],
        "output": result["run_dir"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
