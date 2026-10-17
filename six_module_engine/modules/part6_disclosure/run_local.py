"""Run module six locally before task 00 registers it in the shared registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.core.config import load_runtime_config
from backend.core.context import ReportContext
from backend.core.recorder import RunRecorder
from modules.part6_disclosure.agent import MODULE_ID, MODULE_VERSION, _initial_pages, analyze_disclosure_report
from backend.pdf_reader import extract_all_pages


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在本地独立运行模块六。")
    parser.add_argument("--pdf", required=True, type=Path, help="待分析年报 PDF 路径")
    parser.add_argument("--config", type=Path, default=None, help="本机配置文件路径；不复制密钥")
    parser.add_argument("--output", type=Path, default=Path("data/开发运行/模块六"), help="当前任务工作区内的结果目录")
    return parser.parse_args(argv)


def run_one(pdf_path: Path, output_dir: Path, config_path: Path | None = None) -> dict[str, str]:
    config = load_runtime_config(config_path)
    pdf_path = pdf_path.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"没有找到年报 PDF：{pdf_path}")
    content = pdf_path.read_bytes()
    report_hash = hashlib.sha256(content).hexdigest()
    extracted = extract_all_pages(content)
    pages = extracted.selected_pages
    run_id = uuid.uuid4().hex
    run_dir = output_dir.resolve() / run_id
    recorder = RunRecorder(run_dir, run_id=run_id, module_id=MODULE_ID)
    recorder.record(
        "input_file_access",
        path=str(pdf_path),
        sha256=report_hash,
        bytes=len(content),
        page_count=extracted.page_count,
        extracted_page_count=len(pages),
    )
    recorder.write_manifest({
        "run_id": run_id,
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "report": {"file_name": pdf_path.name, "sha256": report_hash, "page_count": extracted.page_count},
        "model": "本机 DeepSeek 配置",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    seed = _initial_pages(pages)
    initial_numbers = {int(item["page"]) for item in seed}
    context = ReportContext(
        report_id=report_hash,
        file_name=pdf_path.name,
        file_sha256=report_hash,
        page_count=extracted.page_count,
        pages=pages,
        recorder=recorder,
        initial_pages=seed,
    )
    context._get_page = lambda number: next(
        (str(item["text"]) for item in pages if int(item["page"]) == number), None
    )
    recorder.record("module_started", module_version=MODULE_VERSION)
    context.progress("模块六独立运行", "进行中", f"候选原文页 {len(initial_numbers)} 页。")
    try:
        workflow = analyze_disclosure_report(context)
    except Exception as exc:
        checkpoint_path = recorder.artifacts_dir / "disclosure_checkpoint.json"
        checkpoint = None
        if checkpoint_path.is_file():
            try:
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                checkpoint = None
        has_partial_work = isinstance(checkpoint, dict) and any(
            checkpoint.get(key) for key in ("submitted_overview", "findings", "impacts", "calculations", "provisional_result")
        )
        recorder.record(
            "module_partial" if has_partial_work else "module_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        if has_partial_work:
            checkpoint["checkpoint_status"] = "partial"
            checkpoint["error"] = str(exc)
        recorder.write_result({
            "report_id": report_hash,
            "module_id": MODULE_ID,
            "module_version": MODULE_VERSION,
            "status": "partial" if has_partial_work else "failed",
            "error": str(exc),
            "result": checkpoint if has_partial_work else None,
        })
        if has_partial_work:
            return {
                "run_id": run_id,
                "status": "partial",
                "output": str(run_dir),
                "file_sha256": report_hash,
                "error": str(exc),
            }
        raise
    result = workflow["result"]
    status = str(workflow.get("status") or "completed")
    recorder.write_result({
        "report_id": report_hash,
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "status": status,
        "error": None,
        "read_pages": workflow["read_pages"],
        "result": result,
    })
    return {"run_id": run_id, "status": status, "output": str(run_dir), "file_sha256": report_hash}


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        result = run_one(args.pdf, args.output, args.config)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get("status") == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
