"""Single-report workbench that schedules and preserves all selected modules."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pymupdf
from dotenv import set_key
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from backend.core.config import PROJECT_ROOT, config_path, load_runtime_config
from backend.core.model_io import model_name, new_client, recorded_completion, use_report_model
from backend.core.recorder import RunRecorder
from backend.deepseek_client import api_key_configured
from backend.integrated_store import (
    UPLOAD_DIR,
    create_report,
    get_report,
    init_store,
    list_reports,
    now_iso,
    update_module,
    update_report,
)
from backend.module_registry import registrations
from backend.pdf_reader import MAX_UPLOAD_BYTES, ExtractedPdf, extract_all_pages
from backend.run_module import run_one


ROOT = PROJECT_ROOT
DATA_DIR = ROOT / "data" / "integrated_workbench"
RUN_DIR = DATA_DIR / "runs"
REPORT_PIPELINE_DIR = DATA_DIR / "report_pipeline"
MODULE_LABELS = {
    "business": "业务与经营",
    "profit": "盈利分析",
    "assets": "资产质量",
    "cashflow": "现金流",
    "solvency": "偿债能力",
    "disclosure": "信息披露",
}
MODULE_ORDER = list(MODULE_LABELS)
load_runtime_config()
init_store()

app = FastAPI(title="林研 · 六模块财报分析工作台", version="4.0.0")
_scheduler = ThreadPoolExecutor(max_workers=1, thread_name_prefix="integrated-report")
_scheduler_lock = threading.Lock()
_active_reports: set[str] = set()


class KeyUpdate(BaseModel):
    api_key: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _selected_modules(raw: str) -> list[str]:
    value = (raw or "all").strip().lower()
    if value in {"all", "complete", "全部"}:
        return MODULE_ORDER.copy()
    aliases = {f"part{i}_{name}": module for i, (module, name) in enumerate([
        ("business", "business"), ("profit", "profit"), ("assets", "assets"),
        ("cashflow", "cashflow"), ("solvency", "solvency"), ("disclosure", "disclosure"),
    ], 1)}
    requested = [aliases.get(item.strip(), item.strip()) for item in value.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(MODULE_ORDER))
    if unknown:
        raise HTTPException(status_code=400, detail=f"无法识别的分析模块：{', '.join(unknown)}")
    selected = [module for module in MODULE_ORDER if module in requested]
    if not selected:
        raise HTTPException(status_code=400, detail="至少选择一个分析模块。")
    return selected


def _public_module(item: dict[str, Any], *, include_result: bool = False) -> dict[str, Any]:
    output = {
        key: item.get(key)
        for key in (
            "module_id", "module_version", "status", "stage", "detail", "analysis_status",
            "review_status", "error", "attempt", "updated_at",
        )
    }
    output["label"] = MODULE_LABELS.get(str(item.get("module_id")), item.get("module_id"))
    output["result_available"] = bool(item.get("result")) or bool(item.get("result_available"))
    if include_result:
        value = dict(item.get("result")) if isinstance(item.get("result"), dict) else item.get("result")
        if isinstance(value, dict):
            value.pop("run_dir", None)
        output["result"] = value
    return output


def _public_report(report: dict[str, Any], *, include_results: bool = False) -> dict[str, Any]:
    return {
        "id": report["id"],
        "file_name": report["file_name"],
        "company": report.get("company_name"),
        "code": report.get("security_code"),
        "year": report.get("report_year"),
        "sha256": report["sha256"],
        "selected_modules": report.get("selected_modules", []),
        "status": report["status"],
        "created_at": report["created_at"],
        "updated_at": report["updated_at"],
        "page_count": report.get("page_count", 0),
        "synthesis_status": report.get("synthesis_status"),
        "synthesis": report.get("synthesis"),
        "error": report.get("error"),
        "modules": [_public_module(item, include_result=include_results) for item in report.get("modules", [])],
    }


def _cached_extraction(report_id: str, content: bytes, module_ids: list[str]) -> ExtractedPdf:
    cache_path = DATA_DIR / report_id / "extracted_pages.json"
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return ExtractedPdf(
            page_count=int(payload["page_count"]),
            selected_pages=payload["selected_pages"],
            table_layout_pages=payload.get("table_layout_pages", []),
            table_count=int(payload.get("table_count", 0)),
        )
    registry = registrations()
    keywords = tuple(dict.fromkeys(
        term
        for module_id in module_ids
        for term in registry[module_id].table_layout_keywords
    ))
    extracted = extract_all_pages(content, table_layout_keywords=keywords)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "page_count": extracted.page_count,
        "selected_pages": extracted.selected_pages,
        "table_layout_pages": extracted.table_layout_pages,
        "table_count": extracted.table_count,
    }, ensure_ascii=False), encoding="utf-8")
    update_report(report_id, page_count=extracted.page_count)
    return extracted


def _execute_module_with_model(
    report_id: str,
    module_id: str,
    pdf_path: Path,
    extracted: ExtractedPdf,
    related_results: dict[str, Any],
) -> None:
    current = get_report(report_id)
    task = next((item for item in (current or {}).get("modules", []) if item["module_id"] == module_id), None)
    attempt = int((task or {}).get("attempt") or 0) + 1
    update_module(
        report_id, module_id, status="running", stage="开始分析", detail="正在准备模块所需年报页面。",
        error=None, attempt=attempt,
    )
    output_root = RUN_DIR / report_id / module_id

    def on_progress(stage: str, status: str, detail: str = "") -> None:
        update_module(report_id, module_id, stage=stage, detail=detail or status)

    try:
        result = run_one(
            module_id,
            pdf_path,
            output_root,
            config_path(),
            related_results=related_results,
            extracted_pdf=extracted,
            on_progress=on_progress,
        )
        update_module(
            report_id,
            module_id,
            module_version=result.get("module_version", ""),
            status="completed",
            stage="模块已返回结果",
            detail="分析结果已保存，可在模块页查看完整内容。",
            analysis_status=result.get("analysis_status"),
            review_status=result.get("analysis_review_status"),
            result=result,
            run_dir=result.get("run_dir"),
            error=None,
        )
    except Exception as exc:
        update_module(
            report_id, module_id, status="failed", stage="运行失败", detail="模块执行未完成。",
            error=str(exc),
        )


def _execute_module(
    report_id: str,
    module_id: str,
    pdf_path: Path,
    extracted: ExtractedPdf,
    related_results: dict[str, Any],
) -> None:
    with use_report_model(report_id):
        _execute_module_with_model(report_id, module_id, pdf_path, extracted, related_results)


def _summary_material(module_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
    result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
    summary_keys = (
        "summary", "executive_summary", "overall_view", "findings", "profit_checks",
        "quality_issues", "review_flags", "limitations", "uncertainties", "analysis_review_status",
        "analysis_completion_status", "analysis_status", "run_quality", "coverage",
        "revenue_totals", "revenue_segments", "profit_lines", "profit_metrics", "profit_bridge",
        "profit_checks", "asset_map", "sections", "debt_structure", "maturity_profile",
        "cash_availability", "data_coverage", "quality_issues", "review_flags",
    )
    material = {key: result[key] for key in summary_keys if key in result}
    material["module_id"] = module_id
    material["label"] = MODULE_LABELS[module_id]
    material["analysis_status"] = envelope.get("analysis_status")
    material["review_status"] = envelope.get("analysis_review_status")
    material["read_pages"] = envelope.get("read_pages", [])
    # Findings can be lengthy; keep enough detail for cross-checking while avoiding a second full-report upload.
    if isinstance(material.get("findings"), list):
        material["findings"] = material["findings"][:8]
    for key, value in list(material.items()):
        if key not in {"module_id", "label", "analysis_status", "review_status", "read_pages"}:
            material[key] = _compact_for_synthesis(value)
    return material


def _compact_for_synthesis(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[已省略更深层字段]"
    if isinstance(value, str):
        return value[:1200]
    if isinstance(value, list):
        return [_compact_for_synthesis(item, depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {key: _compact_for_synthesis(item, depth + 1) for key, item in list(value.items())[:28]}
    return value


def _synthesize_with_model(report_id: str, modules: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in modules if item.get("status") == "completed" and item.get("result")]
    if not completed:
        return {
            "status": "unavailable",
            "overall_summary": "没有模块完成分析，暂时无法形成综合判断。",
            "cross_module_findings": [],
            "critical_gaps": ["所有模块均未返回可用分析结果。"],
        }
    if len(completed) < 2:
        missing = [item["module_id"] for item in modules if item.get("status") != "completed"]
        return {
            "status": "incomplete",
            "overall_summary": "目前只有一个模块返回结果，无法判断模块之间是否一致或存在冲突。",
            "cross_module_findings": [],
            "agreements": [],
            "disagreements": [],
            "critical_gaps": [f"尚无结果的模块：{', '.join(missing)}"] if missing else [],
            "limitations": ["跨模块复核至少需要两个模块的分析结果。"],
        }
    material = [_summary_material(item["module_id"], item["result"]) for item in completed]
    recorder = RunRecorder(RUN_DIR / report_id / "synthesis", run_id=report_id, module_id="cross_module")
    recorder.save_artifact("module_summaries", material)
    system_prompt = """你是财报分析系统的跨模块复核员。输入是各专业模块已经产出的摘要、问题和来源页码。不得把模块结果当成经过审计的事实；不得补造金额、来源或因果关系。只识别有材料支持的模块间一致、矛盾或值得追问的联系。每条判断必须列出来源模块和页码；若给定材料不足，写入 critical_gaps。不同模块口径可能不同，应先说明口径差异，不要擅自合并。
只返回 JSON 对象，字段为：overall_summary（简洁总览），cross_module_findings（数组，每项含 title、observation、module_ids、evidence_refs 数组〔每项含 module_id、page〕、interpretation、follow_up），agreements（数组），disagreements（数组），critical_gaps（数组），limitations（数组）。只能引用输入中对应模块 read_pages 出现的页码。"""
    try:
        response = recorded_completion(
            recorder=recorder,
            client=new_client(),
            model=model_name(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(material, ensure_ascii=False, default=str)},
            ],
            response_format={"type": "json_object"},
            stream=False,
        )
        raw = response.choices[0].message.content if response.choices else ""
        value = json.loads(raw or "{}")
        if not isinstance(value, dict):
            raise ValueError("综合复核返回格式错误")
        allowed_pages = {
            item["module_id"]: {int(page) for page in item.get("read_pages", []) if str(page).isdigit()}
            for item in material
        }
        valid_findings = []
        for finding in value.get("cross_module_findings", [])[:10]:
            if not isinstance(finding, dict):
                continue
            refs = []
            for ref in finding.get("evidence_refs", []):
                if not isinstance(ref, dict):
                    continue
                module_id = str(ref.get("module_id", ""))
                try:
                    page = int(ref.get("page"))
                except (TypeError, ValueError):
                    continue
                if page in allowed_pages.get(module_id, set()):
                    refs.append({"module_id": module_id, "label": MODULE_LABELS.get(module_id, module_id), "page": page})
            source_ids = [item for item in finding.get("module_ids", []) if item in allowed_pages]
            if refs:
                finding["module_ids"] = list(dict.fromkeys(source_ids or [item["module_id"] for item in refs]))
                finding["evidence_refs"] = refs
                valid_findings.append(finding)
        value["cross_module_findings"] = valid_findings
        value["status"] = "completed"
        value.setdefault("agreements", [])
        value.setdefault("disagreements", [])
        value.setdefault("critical_gaps", [])
        value.setdefault("limitations", [])
        value.setdefault("overall_summary", "已完成跨模块复核，请结合各模块完整结果阅读。")
        recorder.save_artifact("validated_synthesis", value)
        return value
    except Exception as exc:
        recorder.record("synthesis_failed", error_type=type(exc).__name__, error=str(exc))
        return {
            "status": "unavailable",
            "overall_summary": "六模块结果已分别保存，但本次跨模块综合复核未能完成。",
            "cross_module_findings": [],
            "agreements": [],
            "disagreements": [],
            "critical_gaps": ["综合复核调用失败；请查看各模块结果后重试。"],
            "limitations": [str(exc)[:500]],
        }


def _synthesize(report_id: str, modules: list[dict[str, Any]]) -> dict[str, Any]:
    with use_report_model(report_id):
        return _synthesize_with_model(report_id, modules)


def _finish_report(report_id: str) -> None:
    report = get_report(report_id)
    if not report:
        return
    states = [item["status"] for item in report["modules"]]
    if any(value in {"queued", "running"} for value in states):
        status = "running"
    elif states and all(value == "completed" for value in states):
        status = "completed"
    elif any(value == "completed" for value in states):
        status = "partial"
    else:
        status = "failed"
    update_report(report_id, status=status)


def _submit_report(report_id: str, retry_module: str | None = None) -> None:
    with _scheduler_lock:
        if report_id in _active_reports:
            raise HTTPException(status_code=409, detail="这份报告已有任务排队或正在运行。")
        _active_reports.add(report_id)
    try:
        _scheduler.submit(_run_report, report_id, retry_module)
    except Exception:
        with _scheduler_lock:
            _active_reports.discard(report_id)
        raise


def _run_report(report_id: str, retry_module: str | None = None) -> None:
    report = get_report(report_id)
    if not report:
        with _scheduler_lock:
            _active_reports.discard(report_id)
        return
    selected = report.get("selected_modules") or []
    chosen = [retry_module] if retry_module else selected
    update_report(report_id, status="running", error=None, synthesis_status="running" if not retry_module else "pending")
    try:
        pdf_path = Path(report["file_path"])
        content = pdf_path.read_bytes()
        extracted = _cached_extraction(report_id, content, chosen)
        if retry_module:
            related: dict[str, Any] = {}
            if retry_module == "profit":
                latest = get_report(report_id) or {}
                business = next((item for item in latest["modules"] if item["module_id"] == "business"), None)
                if business and business.get("result"):
                    related["business"] = business["result"].get("result", {})
            _execute_module(report_id, retry_module, pdf_path, extracted, related)
            latest = get_report(report_id) or {}
            synthesis = _synthesize(report_id, latest["modules"])
            update_report(report_id, synthesis_status=synthesis.get("status", "completed"), synthesis=synthesis)
        else:
            # Profit analysis can use the business module's result, so it starts after business.
            results: dict[str, Any] = {}
            if "business" in chosen:
                _execute_module(report_id, "business", pdf_path, extracted, {})
                latest = get_report(report_id) or {}
                task = next(item for item in latest["modules"] if item["module_id"] == "business")
                if task.get("result"):
                    results["business"] = task["result"].get("result", {})
            remaining = [item for item in chosen if item != "business"]
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="module-analysis") as pool:
                futures = {
                    pool.submit(
                        _execute_module,
                        report_id,
                        module_id,
                        pdf_path,
                        extracted,
                        results if module_id == "profit" else {},
                    ): module_id
                    for module_id in remaining
                }
                for future in as_completed(futures):
                    future.result()
            latest = get_report(report_id) or {}
            synthesis = _synthesize(report_id, latest["modules"])
            update_report(report_id, synthesis_status=synthesis.get("status", "completed"), synthesis=synthesis)

        # Build the readable, source-linked report after the module outputs and
        # cross-module review are available. A writing-stage failure must leave
        # the six specialist workpapers usable.
        try:
            from backend.report_generator import generate_report_package

            with use_report_model(report_id):
                report_generation = generate_report_package(report_id)
            latest = get_report(report_id) or {}
            synthesis = latest.get("synthesis") if isinstance(latest.get("synthesis"), dict) else {}
            synthesis["report_generation"] = report_generation
            update_report(report_id, synthesis=synthesis)
        except Exception as report_exc:
            latest = get_report(report_id) or {}
            synthesis = latest.get("synthesis") if isinstance(latest.get("synthesis"), dict) else {}
            synthesis["report_generation"] = {
                "status": "failed",
                "error": str(report_exc)[:500],
                "note": "六模块底稿和跨模块综合已保留；可在各模块结果中查看分析。",
            }
            update_report(report_id, synthesis=synthesis)
        _finish_report(report_id)
    except Exception as exc:
        update_report(report_id, status="failed", error=str(exc))
        if not retry_module:
            latest = get_report(report_id) or {}
            for item in latest.get("modules", []):
                if item.get("status") == "queued":
                    update_module(
                        report_id, item["module_id"], status="failed", stage="准备分析失败",
                        detail="年报解析或任务准备未完成。", error=str(exc),
                    )
        else:
            update_module(
                report_id, retry_module, status="failed", stage="重试准备失败",
                detail="模块未能进入分析阶段。", error=str(exc),
            )
    finally:
        with _scheduler_lock:
            _active_reports.discard(report_id)


@app.on_event("startup")
def startup() -> None:
    init_store()


@app.get("/api/health")
def health() -> dict[str, Any]:
    registry = registrations()
    return {
        "status": "ok",
        "api_configured": api_key_configured(),
        "model": model_name(),
        "modules": [
            {
                "module_id": module_id,
                "label": MODULE_LABELS[module_id],
                "version": registration.version,
            }
            for module_id, registration in registry.items()
        ],
    }


def _local_research_package(report_id: str) -> tuple[dict[str, Any], str]:
    """Load the newest complete, local research package for an existing report."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    if not re.fullmatch(r"[0-9a-f]{32}", report_id):
        raise HTTPException(status_code=404, detail="没有找到这份分析记录的本地综合研报。")

    base = REPORT_PIPELINE_DIR.resolve()
    run_dir = (base / report_id).resolve()
    if run_dir.parent != base or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="这份分析还没有本地综合研报。")

    required_files = ("findings.json", "themes.json", "review.json", "outline.json", "audit.json", "report.md")
    candidates = []
    for candidate in run_dir.iterdir():
        if candidate.is_symlink() or not candidate.is_dir() or candidate.resolve().parent != run_dir:
            continue
        if all((candidate / name).is_file() for name in required_files):
            candidates.append(candidate)
    if not candidates:
        raise HTTPException(status_code=404, detail="这份分析还没有完整的本地综合研报。")

    package_dir = max(candidates, key=lambda path: (path / "audit.json").stat().st_mtime_ns)

    def read_json(name: str) -> Any:
        try:
            return json.loads((package_dir / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"本地综合研报文件无法读取：{name}") from exc

    try:
        report_markdown = (package_dir / "report.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=422, detail="本地综合研报正文无法读取。") from exc

    findings = read_json("findings.json")
    themes = read_json("themes.json")
    review = read_json("review.json")
    outline = read_json("outline.json")
    audit = read_json("audit.json")
    if not all(isinstance(value, dict) for value in (findings, themes, review, outline, audit)):
        raise HTTPException(status_code=422, detail="本地综合研报结构不完整。")
    if audit.get("pipeline_version") == "finscope-v5.1.0-python":
        from backend.financial_brief import build_financial_brief, render_financial_brief

        preview = render_financial_brief(build_financial_brief(report))
        report_markdown = (
            "> **历史报告：**这份正文由 V5.1.0 生成。请在 V5.2.0 工作台重新生成综合报告，"
            "以应用来源修复与数值冲突检查。下方计算底表由 V5.2.0 本地程序即时生成，"
            "历史正文尚未经新版复核。\n\n"
            + (preview + "\n\n---\n\n" if preview else "")
            + report_markdown
        )
    editorial_report = read_json("editorial_report.json") if (package_dir / "editorial_report.json").is_file() else None
    if editorial_report is not None and not isinstance(editorial_report, dict):
        raise HTTPException(status_code=422, detail="本地报告正文结构不完整。")
    editorial_audit = read_json("editorial_audit.json") if (package_dir / "editorial_audit.json").is_file() else None

    public_audit = {
        key: audit.get(key)
        for key in (
            "pipeline_version", "artifact_kind", "status", "generated_on", "processing_record",
            "external_model_attempt", "checks", "counts", "warnings",
        )
    }
    payload = {
        "source_run_id": report_id,
        "file_name": report.get("file_name"),
        "package_name": package_dir.name,
        "company": findings.get("company"),
        "period": findings.get("period"),
        "reporting_scope": findings.get("reporting_scope"),
        "generation_mode": findings.get("generation_mode"),
        "modules": findings.get("modules", []),
        "findings": findings.get("findings", []),
        "themes": themes.get("themes", []),
        "overall_review": review.get("overall_review"),
        "reviews": review.get("reviews", []),
        "outline": outline.get("outline", []),
        "report_markdown": report_markdown,
        "editorial_report": editorial_report,
        "editorial_audit": editorial_audit,
        "audit": public_audit,
    }
    return payload, report_markdown


@app.get("/api/reports/{report_id}/research")
def research_report_endpoint(report_id: str) -> dict[str, Any]:
    payload, _ = _local_research_package(report_id)
    return payload


@app.get("/api/reports/{report_id}/research/export")
def research_report_export_endpoint(report_id: str) -> Response:
    payload, report_markdown = _local_research_package(report_id)
    filename = re.sub(r"[\\/:*?\"<>|\r\n]+", "_", f"{payload['company'] or '财报'}_{payload['period'] or '综合研报'}")[:100]
    export_name = quote(f"{filename}_六模块综合分析.md")
    return Response(
        content=report_markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{export_name}"},
    )


@app.post("/api/settings/deepseek-key")
def save_deepseek_key(payload: KeyUpdate) -> dict[str, bool]:
    api_key = payload.api_key.strip()
    if not api_key or len(api_key) > 512 or any(character.isspace() for character in api_key):
        raise HTTPException(status_code=400, detail="API Key 不能为空，且不能包含空格或换行。")
    target = config_path()
    try:
        saved, _, _ = set_key(target, "DEEPSEEK_API_KEY", api_key, quote_mode="always")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="无法写入本机 .env 文件。") from exc
    if not saved:
        raise HTTPException(status_code=500, detail="无法写入本机 .env 文件。")
    os.environ["DEEPSEEK_API_KEY"] = api_key
    return {"api_configured": True}


@app.post("/api/reports")
async def create_report_endpoint(
    file: UploadFile = File(...),
    modules: str = Form(default="all"),
) -> dict[str, Any]:
    if not api_key_configured():
        raise HTTPException(status_code=503, detail="尚未配置 DeepSeek API Key。请先配置 Key。")
    file_name = Path(file.filename or "").name.strip()
    if not file_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传 PDF 文件。")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 25 MB，请先压缩 PDF 后重试。")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="上传内容不是有效的 PDF 文件。")
    selected = _selected_modules(modules)
    registry = registrations()
    report_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{report_id}.pdf"
    path.write_bytes(content)
    create_report(
        {
            "id": report_id,
            "file_name": file_name[:240],
            "file_path": str(path),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        [
            {"module_id": module_id, "module_version": registry[module_id].version}
            for module_id in selected
        ],
    )
    _submit_report(report_id)
    return _public_report(get_report(report_id) or {})


@app.get("/api/reports")
def reports_endpoint() -> dict[str, Any]:
    return {"reports": [_public_report(item) for item in list_reports()]}


@app.get("/api/reports/{report_id}")
def report_endpoint(report_id: str) -> dict[str, Any]:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    return _public_report(report)


@app.get("/api/reports/{report_id}/modules/{module_id}")
def module_result_endpoint(report_id: str, module_id: str) -> dict[str, Any]:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    item = next((row for row in report["modules"] if row["module_id"] == module_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="这份分析中没有该模块。")
    return _public_module(item, include_result=True)


@app.post("/api/reports/{report_id}/modules/{module_id}/retry")
def retry_module_endpoint(report_id: str, module_id: str) -> dict[str, Any]:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    if module_id not in report.get("selected_modules", []):
        raise HTTPException(status_code=404, detail="这份分析中没有该模块。")
    with _scheduler_lock:
        if report_id in _active_reports:
            raise HTTPException(status_code=409, detail="这份报告仍在排队或运行，请等当前任务完成后重试。")
        _active_reports.add(report_id)
    try:
        update_module(
            report_id, module_id, status="queued", stage="等待重试", detail="已提交单模块重试。",
            error=None, result=None, analysis_status=None, review_status=None,
        )
        update_report(report_id, status="running", error=None, synthesis_status="pending")
        _scheduler.submit(_run_report, report_id, module_id)
    except Exception:
        with _scheduler_lock:
            _active_reports.discard(report_id)
        raise
    return _public_report(get_report(report_id) or {})


def _regenerate_report(report_id: str) -> None:
    try:
        from backend.report_generator import generate_report_package

        with use_report_model(report_id):
            generation = generate_report_package(report_id)
        report = get_report(report_id) or {}
        synthesis = report.get("synthesis") if isinstance(report.get("synthesis"), dict) else {}
        synthesis["report_generation"] = generation
        update_report(report_id, synthesis=synthesis)
    except Exception as exc:
        report = get_report(report_id) or {}
        synthesis = report.get("synthesis") if isinstance(report.get("synthesis"), dict) else {}
        synthesis["report_generation"] = {
            "status": "failed", "error": str(exc)[:500],
            "note": "专业模块的既有底稿已保留。",
        }
        update_report(report_id, synthesis=synthesis)
    finally:
        _finish_report(report_id)
        with _scheduler_lock:
            _active_reports.discard(report_id)


def regenerate_report_endpoint(report_id: str) -> dict[str, Any]:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    if not any(item.get("result") for item in report.get("modules", [])):
        raise HTTPException(status_code=409, detail="尚无可用于成稿的专业模块底稿。")
    with _scheduler_lock:
        if report_id in _active_reports:
            raise HTTPException(status_code=409, detail="这份分析已有任务正在运行。")
        _active_reports.add(report_id)
    try:
        synthesis = report.get("synthesis") if isinstance(report.get("synthesis"), dict) else {}
        synthesis["report_generation"] = {"status": "running", "note": "正在根据已保存的模块底稿重新成稿。"}
        update_report(report_id, status="running", synthesis=synthesis)
        _scheduler.submit(_regenerate_report, report_id)
    except Exception:
        with _scheduler_lock:
            _active_reports.discard(report_id)
        raise
    return _public_report(get_report(report_id) or {})


@app.get("/api/reports/{report_id}/pages/{page_number}")
def report_page_endpoint(report_id: str, page_number: int) -> dict[str, Any]:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    path = Path(report["file_path"]).resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原始 PDF 已不存在。")
    try:
        with pymupdf.open(path) as document:
            if page_number < 1 or page_number > len(document):
                raise HTTPException(status_code=404, detail="页码超出 PDF 范围。")
            text = document[page_number - 1].get_text("text")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="无法读取 PDF 原文页。") from exc
    return {"report_id": report_id, "file_name": report["file_name"], "page": page_number, "text": text}


@app.get("/api/reports/{report_id}/file")
def report_file_endpoint(report_id: str) -> FileResponse:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    path = Path(report["file_path"]).resolve()
    if not path.is_file() or UPLOAD_DIR.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="原始 PDF 已不存在。")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=report["file_name"],
        content_disposition_type="inline",
    )


def _markdown(value: Any, depth: int = 0) -> str:
    if isinstance(value, dict):
        chunks = []
        for key, item in value.items():
            title = str(key).replace("_", " ")
            if isinstance(item, (dict, list)):
                chunks.append(f"{'#' * min(depth + 3, 6)} {title}\n\n{_markdown(item, depth + 1)}")
            else:
                chunks.append(f"- **{title}：** {item}")
        return "\n\n".join(chunks)
    if isinstance(value, list):
        return "\n\n".join(
            (f"### 条目 {index}\n\n" if isinstance(item, (dict, list)) else "- ") + _markdown(item, depth + 1)
            for index, item in enumerate(value, 1)
        )
    return str(value)


@app.get("/api/reports/{report_id}/export")
def export_report_endpoint(report_id: str) -> Response:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="没有找到这份分析记录。")
    parts = [
        f"# 财报分析结果：{report['file_name']}",
        f"\n- 报告编号：{report_id}\n- 文件 SHA-256：{report['sha256']}\n- 总体状态：{report['status']}",
    ]
    if report.get("synthesis"):
        parts.append("\n## 跨模块综合复核\n\n" + _markdown(report["synthesis"]))
    for item in report["modules"]:
        module_result = dict(item.get("result")) if isinstance(item.get("result"), dict) else item.get("result")
        if isinstance(module_result, dict):
            module_result.pop("run_dir", None)
        parts.append(
            f"\n## {MODULE_LABELS.get(item['module_id'], item['module_id'])}"
            f"\n\n运行状态：{item['status']}；分析状态：{item.get('analysis_status') or '模块未提供统一标记'}\n\n"
            + (_markdown(module_result) if module_result else (item.get("error") or "暂无结果。"))
        )
    body = "\n\n---\n\n".join(parts)
    filename = re.sub(r"[^\w.-]+", "_", Path(report["file_name"]).stem)[:80] or report_id
    export_name = quote(f"{filename}_六模块分析.md")
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{export_name}"},
    )
