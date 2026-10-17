"""Same-origin adapter between the V5 local workbench and its six-module engine."""
from __future__ import annotations

import hashlib
import re
import sys
import uuid
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = (PROJECT_ROOT / "vendor" / "python").resolve()
ENGINE_ROOT = (PROJECT_ROOT / "six_module_engine").resolve()
for dependency_root in (ENGINE_ROOT, VENDOR_ROOT):
    if dependency_root.is_dir() and str(dependency_root) not in sys.path:
        sys.path.insert(0, str(dependency_root))

from backend.core.model_io import register_report_model
from backend.integrated_store import (
    UPLOAD_DIR,
    connect,
    create_report,
    get_report,
    list_reports,
)
from backend.module_registry import registrations
from backend.pdf_reader import MAX_UPLOAD_BYTES
from backend.integrated_workbench import (
    MODULE_LABELS,
    _public_report,
    _selected_modules,
    _submit_report,
    get_report as _get_report,
    module_result_endpoint,
    report_page_endpoint,
    research_report_endpoint,
    regenerate_report_endpoint,
    retry_module_endpoint,
)

SAMPLE_REPORT_ID = "603a98fb89234045a881b5f4b498cf68"
SAMPLE_PDF = UPLOAD_DIR / f"{SAMPLE_REPORT_ID}.pdf"
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class SixModuleError(RuntimeError):
    def __init__(self, status: int, message: str, code: str = "SIX_MODULE_ERROR") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def prepare_local_sample() -> None:
    """Repoint the copied Wuliangye record to its PDF inside this V5 folder."""
    if not SAMPLE_PDF.is_file():
        return
    with connect() as db:
        db.execute(
            "UPDATE reports SET file_path=?,company_name=CASE WHEN COALESCE(company_name,'')='' THEN ? ELSE company_name END,"
            "report_year=CASE WHEN COALESCE(report_year,'')='' THEN ? ELSE report_year END WHERE id=?",
            (str(SAMPLE_PDF), "宜宾五粮液股份有限公司", "2023", SAMPLE_REPORT_ID),
        )


def _model_for_session(session_id: str) -> dict[str, str]:
    try:
        from model_settings import activate_model_session, current_model_settings, reset_model_session
    except ModuleNotFoundError:
        from agent.model_settings import activate_model_session, current_model_settings, reset_model_session
    token = activate_model_session(session_id)
    try:
        settings = current_model_settings()
    finally:
        reset_model_session(token)
    if not settings.get("api_key"):
        raise SixModuleError(503, "请先在工作台配置 DeepSeek，再开始六模块分析。", "MODEL_NOT_CONFIGURED")
    return {
        "api_key": settings["api_key"],
        "base_url": settings["base_url"],
        "model": settings["model"],
    }


def create_project_report(
    *,
    project_id: str,
    file_name: str,
    content: bytes,
    modules: str,
    session_id: str,
    company: str = "",
    code: str = "",
    year: str = "",
) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", project_id):
        raise SixModuleError(400, "项目编号无效。", "INVALID_PROJECT")
    file_name = Path(file_name or "").name.strip()
    if not file_name.lower().endswith(".pdf"):
        raise SixModuleError(400, "请上传 PDF 财报。", "INVALID_UPLOAD")
    if not content or len(content) > MAX_UPLOAD_BYTES or not content.startswith(b"%PDF-"):
        raise SixModuleError(400, "PDF 为空、格式错误或超过 25 MB。", "INVALID_UPLOAD")
    fingerprint = hashlib.sha256(content).hexdigest()
    active = next((item for item in list_reports() if item.get("workbench_project_id") == project_id and item.get("status") in {"queued", "running"}), None)
    if active:
        if active.get("sha256") != fingerprint:
            raise SixModuleError(409, "当前项目已有分析正在排队或运行，请等待结束后再分析新文件。", "PROJECT_RUN_ACTIVE")
        result = _public_report(get_report(active["id"]) or active)
        result["project_id"] = project_id
        return result
    model_settings = _model_for_session(session_id)
    try:
        selected = _selected_modules(modules)
    except Exception as exc:
        raise SixModuleError(getattr(exc, "status_code", 400), str(getattr(exc, "detail", exc)), "INVALID_MODULES") from exc
    registry = registrations()
    report_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{report_id}.pdf"
    path.write_bytes(content)
    try:
        create_report(
            {
                "id": report_id,
                "file_name": file_name[:240],
                "file_path": str(path),
                "sha256": fingerprint,
            },
            [
                {"module_id": module_id, "module_version": registry[module_id].version}
                for module_id in selected
            ],
            project_id=project_id,
            company=company[:120],
            code=code[:20],
            year=year[:8],
        )
        register_report_model(report_id, model_settings)
        _submit_report(report_id)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    result = _public_report(_get_report(report_id) or {})
    result["project_id"] = project_id
    return result


def project_reports(project_id: str) -> dict[str, Any]:
    rows = [item for item in list_reports() if item.get("workbench_project_id") == project_id]
    return {
        "reports": [
            {**_public_report(item), "project_id": item.get("workbench_project_id")}
            for item in rows
        ]
    }


def report(report_id: str) -> dict[str, Any]:
    value = get_report(report_id) if _ID_RE.fullmatch(report_id) else None
    if not value:
        raise SixModuleError(404, "没有找到这份六模块分析。", "REPORT_NOT_FOUND")
    result = _public_report(value)
    result["project_id"] = value.get("workbench_project_id")
    return result


def report_module(report_id: str, module_id: str) -> dict[str, Any]:
    try:
        return module_result_endpoint(report_id, module_id)
    except Exception as exc:
        raise SixModuleError(getattr(exc, "status_code", 500), str(getattr(exc, "detail", exc))) from exc


def research(report_id: str) -> dict[str, Any]:
    try:
        return research_report_endpoint(report_id)
    except Exception as exc:
        raise SixModuleError(getattr(exc, "status_code", 500), str(getattr(exc, "detail", exc))) from exc


def retry(report_id: str, module_id: str, session_id: str) -> dict[str, Any]:
    value = get_report(report_id)
    if not value:
        raise SixModuleError(404, "没有找到这份六模块分析。", "REPORT_NOT_FOUND")
    register_report_model(report_id, _model_for_session(session_id))
    try:
        return retry_module_endpoint(report_id, module_id)
    except Exception as exc:
        raise SixModuleError(getattr(exc, "status_code", 500), str(getattr(exc, "detail", exc))) from exc


def regenerate(report_id: str, session_id: str) -> dict[str, Any]:
    value = get_report(report_id)
    if not value:
        raise SixModuleError(404, "没有找到这份六模块分析。", "REPORT_NOT_FOUND")
    register_report_model(report_id, _model_for_session(session_id))
    try:
        result = regenerate_report_endpoint(report_id)
        result["project_id"] = value.get("workbench_project_id")
        return result
    except Exception as exc:
        raise SixModuleError(getattr(exc, "status_code", 500), str(getattr(exc, "detail", exc))) from exc


def page(report_id: str, page_number: int) -> dict[str, Any]:
    try:
        return report_page_endpoint(report_id, page_number)
    except Exception as exc:
        raise SixModuleError(getattr(exc, "status_code", 500), str(getattr(exc, "detail", exc))) from exc


def sample() -> dict[str, Any]:
    return report(SAMPLE_REPORT_ID)


def report_pdf_path(report_id: str) -> Path:
    value = get_report(report_id) if _ID_RE.fullmatch(report_id) else None
    if not value:
        raise SixModuleError(404, "没有找到这份六模块分析。", "REPORT_NOT_FOUND")
    path = Path(value["file_path"]).resolve()
    if UPLOAD_DIR.resolve() not in path.parents or not path.is_file():
        raise SixModuleError(404, "原始财报 PDF 已不存在。", "PDF_NOT_FOUND")
    return path


def sample_module(module_id: str) -> dict[str, Any]:
    return report_module(SAMPLE_REPORT_ID, module_id)


def sample_research() -> dict[str, Any]:
    return research(SAMPLE_REPORT_ID)


def labels() -> dict[str, str]:
    return dict(MODULE_LABELS)
