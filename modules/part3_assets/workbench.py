"""独立运行的模块三财报分析工作台。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[3]
WORKBENCH_DATA = ROOT / "data" / "模块三工作台"
LEGACY_DATA = ROOT / "data" / "开发运行"
STATIC_ROOT = ROOT / "frontend" / "模块三工作台"
LOCAL_CONFIG = ROOT / ".env"
FALLBACK_CONFIG = ROOT.parents[1] / "金融AI智能体_V3.0.1" / ".env"
LOG_DIR = ROOT / "logs"

# 任务03优先使用自己的 .env；尚未配置时只读 V3.0.1 本机配置作兼容。
os.environ["FINLAB_CONFIG_PATH"] = str(LOCAL_CONFIG)
if LOCAL_CONFIG.is_file():
    load_dotenv(LOCAL_CONFIG, override=True)
elif FALLBACK_CONFIG.is_file():
    load_dotenv(FALLBACK_CONFIG, override=False)

from backend.core.context import ReportContext
from backend.core.recorder import RunRecorder
from backend.deepseek_client import MODEL, ModelCallError, api_key_configured
from modules.part3_assets.agent import _safe_error, analyze_asset_report
from modules.part3_assets.entry import MODULE_ID, MODULE_VERSION
from modules.part3_assets.followup import answer_asset_question
from backend.pdf_reader import MAX_UPLOAD_BYTES, PdfInputError, extract_all_pages, rank_search_pages

LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("assets_workbench")
logger.setLevel(logging.INFO)
logger.propagate = False
if not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
    handler = logging.FileHandler(LOG_DIR / "模块三工作台.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

_RUN_ID = re.compile(r"^[a-f0-9]{32}$")
_state_lock = threading.RLock()
_turn_lock = threading.RLock()
_run_states: dict[str, dict[str, Any]] = {}
_followup_locks: dict[str, threading.Lock] = {}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assets-workbench")


class DeepSeekKeyUpdate(BaseModel):
    api_key: str = Field(min_length=1, max_length=500)


class FollowupQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1200)


app = FastAPI(title="模块三：资产质量与经营效率", version=MODULE_VERSION)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _update_state(run_id: str, **changes: Any) -> dict[str, Any]:
    with _state_lock:
        state = _run_states.setdefault(run_id, {"id": run_id, "progress": []})
        state.update(changes)
        _write_json_atomic(WORKBENCH_DATA / run_id / "state.json", state)
        return dict(state)


def _read_pages(run_dir: Path) -> list[dict[str, Any]]:
    pages = _read_json(run_dir / "artifacts" / "full_extracted_pages.json", [])
    return pages if isinstance(pages, list) else []


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_content(item) for item in value)
    if isinstance(value, dict):
        return any(_has_content(item) for item in value.values())
    return True


def _analysis_class(result: dict[str, Any] | None, error: str = "") -> str:
    if error:
        return "failed"
    if not isinstance(result, dict):
        return "insufficient"
    facts = result.get("facts") if isinstance(result.get("facts"), list) else []
    findings = result.get("findings") if isinstance(result.get("findings"), list) else []
    sections = [result.get(name) for name in ("asset_map", "receivables", "inventory", "long_term_assets", "efficiency")]
    substantive = any(_has_content(value) for value in sections) or bool(findings) or _has_content(result.get("summary"))
    if not facts and not findings and not any(_has_content(value) for value in sections):
        return "insufficient"
    review_needed = bool(
        result.get("quality_issues")
        or result.get("analysis_shape_issues")
        or result.get("analysis_missing_fields")
        or result.get("extraction_status") not in (None, "complete")
        or result.get("evidence_reference_notes")
        or any(
            isinstance(item, dict)
            and isinstance(item.get("citation_audit"), dict)
            and item["citation_audit"].get("status") in {"needs_review", "missing"}
            for item in findings
        )
    )
    if result.get("analysis_status") == "complete":
        return "needs_review" if review_needed else "complete"
    return "partial" if substantive else "insufficient"


def _public_run(state: dict[str, Any], *, include_result: bool = False) -> dict[str, Any]:
    output = {key: value for key, value in state.items() if key != "result"}
    if include_result:
        output["result"] = state.get("result")
    return output


def _load_current_run(run_id: str) -> dict[str, Any] | None:
    if not _RUN_ID.fullmatch(run_id):
        return None
    with _state_lock:
        state = dict(_run_states[run_id]) if run_id in _run_states else _read_json(WORKBENCH_DATA / run_id / "state.json")
    if isinstance(state, dict):
        envelope = _read_json(WORKBENCH_DATA / run_id / "result.json")
        if isinstance(envelope, dict):
            state["result"] = envelope.get("result")
            state["analysis_status"] = state.get("analysis_status") or envelope.get("analysis_status")
        state["legacy"] = False
        return state

    # 早期 CLI 分析只读纳入历史列表，不改写旧运行目录。
    legacy_dir = LEGACY_DATA / run_id
    envelope = _read_json(legacy_dir / "artifacts" / "complete_result.json")
    if not isinstance(envelope, dict) or envelope.get("module_id") != MODULE_ID:
        return None
    manifest = _read_json(legacy_dir / "manifest.json", {})
    report = manifest.get("report", {}) if isinstance(manifest, dict) else {}
    result = _read_json(WORKBENCH_DATA / run_id / "followup_result.json")
    if not isinstance(result, dict):
        result = envelope.get("result") if isinstance(envelope.get("result"), dict) else None
    return {
        "id": run_id,
        "file_name": report.get("file_name") or "历史运行报告",
        "created_at": manifest.get("started_at") or envelope.get("completed_at") or "",
        "updated_at": envelope.get("completed_at") or manifest.get("started_at") or "",
        "run_status": "completed" if envelope.get("status") in {"completed", "partial"} else envelope.get("status", "failed"),
        "analysis_status": _analysis_class(result, envelope.get("error") or ""),
        "analysis_model_status": envelope.get("analysis_status"),
        "stage": "历史模块三分析结果",
        "page_count": report.get("page_count") or 0,
        "readable_pages": report.get("extracted_page_count") or 0,
        "progress": [],
        "error": envelope.get("error"),
        "result": result,
        "legacy": True,
    }


def _list_runs() -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for root, pattern in ((WORKBENCH_DATA, "*/state.json"), (LEGACY_DATA, "*/artifacts/complete_result.json")):
        if not root.is_dir():
            continue
        for path in root.glob(pattern):
            run_id = path.parent.name if root == WORKBENCH_DATA else path.parents[1].name
            if run_id not in found:
                state = _load_current_run(run_id)
                if state:
                    found[run_id] = state
    output = []
    for state in found.values():
        result = state.get("result") if isinstance(state.get("result"), dict) else {}
        report = result.get("report") if isinstance(result.get("report"), dict) else {}
        output.append({
            "id": state.get("id"), "file_name": state.get("file_name"),
            "company": report.get("company"), "report_year": report.get("report_year"),
            "created_at": state.get("created_at"), "run_status": state.get("run_status"),
            "analysis_status": state.get("analysis_status"), "stage": state.get("stage"),
            "page_count": state.get("page_count"),
            "fact_count": len(result.get("facts", [])) if isinstance(result.get("facts"), list) else 0,
            "calculation_count": len(result.get("calculations", [])) if isinstance(result.get("calculations"), list) else 0,
            "legacy": bool(state.get("legacy")),
        })
    return sorted(output, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _run_dir(run_id: str) -> Path:
    if not _RUN_ID.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="没有找到这次分析。")
    return WORKBENCH_DATA / run_id


def _source_revision() -> str | None:
    import subprocess

    try:
        completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True, timeout=5)
        return completed.stdout.strip()
    except Exception:
        return None


def _run_worker(run_id: str, file_name: str, content: bytes) -> None:
    run_dir = _run_dir(run_id)
    recorder = RunRecorder(run_dir, run_id=run_id, module_id=MODULE_ID)
    digest = hashlib.sha256(content).hexdigest()

    def progress(stage: str, status: str, detail: str = "") -> None:
        recorder.progress(stage, status, detail)
        with _state_lock:
            state = _run_states.setdefault(run_id, {"id": run_id, "progress": []})
            state.setdefault("progress", []).append({"at": _now(), "stage": stage, "status": status, "detail": detail})
            state.update({"stage": stage, "stage_status": status, "stage_detail": detail, "updated_at": _now()})
            _write_json_atomic(run_dir / "state.json", state)

    try:
        recorder.record("run_started", module_version=MODULE_VERSION, file_name=file_name, report_sha256=digest)
        progress("读取 PDF", "进行中", "正在提取年报页码和文字。")
        extracted = extract_all_pages(content)
        pages = extracted.selected_pages
        page_map = {int(page["page"]): str(page.get("text") or "") for page in pages}
        readable = sum(bool(page.get("text")) for page in pages)
        recorder.write_manifest({
            "run_id": run_id, "module_id": MODULE_ID, "module_version": MODULE_VERSION,
            "file_name": file_name, "sha256": digest, "page_count": extracted.page_count,
            "readable_page_count": readable, "started_at": _run_states[run_id].get("created_at"),
            "source_revision": _source_revision(),
        })
        recorder.record("input_file_access", stored_as="input/input.pdf", sha256=digest, bytes=len(content), page_count=extracted.page_count)
        recorder.save_artifact("full_extracted_pages", pages)
        _update_state(run_id, run_status="running", stage="分析资产质量与经营效率", stage_status="进行中", stage_detail=f"已读取 {readable}/{extracted.page_count} 页，开始模块三专属分析。", page_count=extracted.page_count, readable_pages=readable, updated_at=_now())
        context = ReportContext(
            report_id=digest, file_name=file_name, file_sha256=digest,
            page_count=extracted.page_count, pages=pages, recorder=recorder,
            _get_page=lambda page: page_map.get(page),
            _search_pages=lambda query: rank_search_pages(pages, query, limit=8, excerpt_chars=3600),
            _on_progress=progress,
            _on_tool=lambda name, detail="": progress("查询年报原文", "进行中", f"{name}：{detail}"[:300]),
        )
        workflow = analyze_asset_report(context)
        result = workflow.get("result", workflow)
        if not isinstance(result, dict):
            raise RuntimeError("模块三没有返回结构化结果。")
        analysis_status = _analysis_class(result)
        envelope = {
            "report_id": digest, "module_id": MODULE_ID, "module_version": MODULE_VERSION,
            "status": "completed", "analysis_status": analysis_status,
            "analysis_model_status": result.get("analysis_status"), "error": None,
            "read_pages": workflow.get("read_pages", []), "result": result, "completed_at": _now(),
        }
        recorder.write_result(envelope)
        progress("模块三分析", "完成", "运行已结束；分析完整度和待复核状态见结果标记。")
        _update_state(
            run_id, run_status="completed", analysis_status=analysis_status,
            analysis_model_status=result.get("analysis_status"), stage="模块三分析已结束",
            stage_status="完成", stage_detail=f"{len(result.get('facts', []))} 条事实，{len(result.get('calculations', []))} 项计算，{len(result.get('findings', []))} 条发现。",
            result_available=True, updated_at=_now(),
        )
    except Exception as exc:
        try:
            safe_error = str(exc) if isinstance(exc, (ModelCallError, PdfInputError)) else str(_safe_error(exc))
        except Exception:
            safe_error = "模块三运行失败；已保留当前进度和可用产物。"
        logger.warning("run_id=%s failed type=%s message=%s", run_id, type(exc).__name__, safe_error)
        recorder.record("run_failed", error_type=type(exc).__name__, error=safe_error)
        progress("模块三分析", "失败", safe_error)
        _update_state(run_id, run_status="failed", analysis_status="failed", stage="本次运行失败", stage_status="失败", stage_detail=safe_error, error=safe_error, updated_at=_now())


def _update_turn(run_id: str, turn_id: str, **changes: Any) -> dict[str, Any]:
    path = _run_dir(run_id) / "followup.json"
    with _turn_lock:
        turns = _read_json(path, [])
        if not isinstance(turns, list):
            turns = []
        for turn in turns:
            if turn.get("id") == turn_id:
                turn.update(changes)
                break
        _write_json_atomic(path, turns)
        return next((turn for turn in turns if turn.get("id") == turn_id), {})


def _followup_worker(run_id: str, turn_id: str, question: str) -> None:
    lock = _followup_locks.setdefault(run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        _update_turn(run_id, turn_id, status="failed", error="当前报告已有一条追问正在处理。")
        return
    try:
        state = _load_current_run(run_id)
        if not state or not isinstance(state.get("result"), dict):
            _update_turn(run_id, turn_id, status="failed", error="没有可供追问的模块三结果。")
            return
        run_dir = _run_dir(run_id)
        source_dir = LEGACY_DATA / run_id if state.get("legacy") else run_dir
        pages = _read_pages(source_dir)
        if not pages:
            _update_turn(run_id, turn_id, status="failed", error="该历史结果没有保留年报原文页，无法进行原文追问。")
            return
        recorder = RunRecorder(run_dir, run_id=run_id, module_id=MODULE_ID)
        page_map = {int(page["page"]): str(page.get("text") or "") for page in pages if page.get("page") is not None}
        report = state["result"].get("report", {})
        context = ReportContext(
            report_id=str(report.get("sha256") or run_id), file_name=str(state.get("file_name") or "年报"),
            file_sha256=str(report.get("sha256") or ""), page_count=int(state.get("page_count") or len(pages)),
            pages=pages, recorder=recorder, _get_page=lambda page: page_map.get(page),
            _search_pages=lambda query: rank_search_pages(pages, query, limit=8, excerpt_chars=3600),
        )
        turns = _read_json(run_dir / "followup.json", [])
        history = [item for item in (turns if isinstance(turns, list) else []) if item.get("status") == "completed" and item.get("id") != turn_id][-6:]
        answer, source_pages, changed_result = answer_asset_question(context, state["result"], question, history)
        if changed_result:
            if state.get("legacy"):
                _write_json_atomic(run_dir / "followup_result.json", state["result"])
            else:
                envelope = _read_json(run_dir / "result.json", {})
                if isinstance(envelope, dict):
                    envelope["result"] = state["result"]
                    envelope["updated_at"] = _now()
                    recorder.write_result(envelope)
        _update_turn(run_id, turn_id, status="completed", answer=answer, source_pages=source_pages, error=None, updated_at=_now())
        recorder.record("followup_completed", turn_id=turn_id, source_pages=source_pages)
    except Exception as exc:
        try:
            safe_error = str(_safe_error(exc)) if isinstance(exc, (ValueError, ModelCallError)) else "模块三追问失败；已有分析结果仍保留。"
        except Exception:
            safe_error = "模块三追问失败；已有分析结果仍保留。"
        logger.warning("followup run_id=%s turn_id=%s failed type=%s message=%s", run_id, turn_id, type(exc).__name__, safe_error)
        _update_turn(run_id, turn_id, status="failed", error=safe_error, updated_at=_now())
        RunRecorder(_run_dir(run_id), run_id=run_id, module_id=MODULE_ID).record("followup_failed", turn_id=turn_id, error_type=type(exc).__name__, error=safe_error)
    finally:
        lock.release()


@app.on_event("startup")
def startup() -> None:
    WORKBENCH_DATA.mkdir(parents=True, exist_ok=True)
    for state_path in WORKBENCH_DATA.glob("*/state.json"):
        state = _read_json(state_path)
        if isinstance(state, dict):
            if state.get("run_status") == "running":
                state.update({"run_status": "failed", "analysis_status": "failed", "stage": "服务重启时任务中断", "error": "工作台曾在分析过程中关闭；运行记录已保留。", "updated_at": _now()})
                _write_json_atomic(state_path, state)
            _run_states[state_path.parent.name] = state


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "module_id": MODULE_ID, "module_version": MODULE_VERSION, "module_name": "资产质量与经营效率", "api_configured": api_key_configured(), "model": MODEL, "port": 8103}


@app.post("/api/settings/deepseek-key")
def save_deepseek_key(payload: DeepSeekKeyUpdate) -> dict[str, bool]:
    key = payload.api_key.strip()
    if len(key) < 20 or any(character.isspace() for character in key):
        raise HTTPException(status_code=400, detail="Key 看起来不完整，请检查复制内容后重试。")
    set_key(str(LOCAL_CONFIG), "DEEPSEEK_API_KEY", key, quote_mode="always")
    os.environ["DEEPSEEK_API_KEY"] = key
    return {"saved": True, "api_configured": True}


@app.get("/api/runs")
def recent_runs() -> dict[str, Any]:
    return {"runs": _list_runs()}


@app.post("/api/runs")
async def create_run(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "年度报告.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传 PDF 格式的年报。")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="PDF 超过 25 MB，请先压缩后再上传。")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="文件内容不是有效的 PDF。")
    run_id = uuid.uuid4().hex
    run_dir = WORKBENCH_DATA / run_id
    (run_dir / "input").mkdir(parents=True, exist_ok=True)
    (run_dir / "input" / "input.pdf").write_bytes(content)
    created_at = _now()
    state = {
        "id": run_id, "file_name": filename[:240], "created_at": created_at, "updated_at": created_at,
        "run_status": "running", "analysis_status": "pending", "stage": "等待读取 PDF",
        "stage_status": "排队中", "stage_detail": "上传已接收，准备开始资产质量与经营效率分析。",
        "page_count": None, "readable_pages": None, "progress": [], "result_available": False, "legacy": False,
    }
    with _state_lock:
        _run_states[run_id] = state
        _write_json_atomic(run_dir / "state.json", state)
    _executor.submit(_run_worker, run_id, filename[:240], content)
    return _public_run(state)


@app.get("/api/runs/{run_id}")
def read_run(run_id: str) -> dict[str, Any]:
    state = _load_current_run(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="没有找到这次分析。")
    return _public_run(state, include_result=True)


@app.get("/api/runs/{run_id}/pages/{page_number}")
def read_source_page(run_id: str, page_number: int) -> dict[str, Any]:
    state = _load_current_run(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="没有找到这次分析。")
    source_dir = LEGACY_DATA / run_id if state.get("legacy") else WORKBENCH_DATA / run_id
    page = next((item for item in _read_pages(source_dir) if int(item.get("page") or -1) == page_number), None)
    if page is None:
        raise HTTPException(status_code=404, detail="该页没有保留可检索的原文文字。")
    return {"page": page_number, "text": str(page.get("text") or ""), "file_name": state.get("file_name"), "source": "本次上传的原年报文字提取"}


@app.get("/api/runs/{run_id}/file")
def read_original_pdf(run_id: str) -> FileResponse:
    state = _load_current_run(run_id)
    if not state or state.get("legacy"):
        raise HTTPException(status_code=404, detail="这条历史运行没有随任务保存原始 PDF。")
    pdf_path = WORKBENCH_DATA / run_id / "input" / "input.pdf"
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="未找到该次上传的 PDF。")
    return FileResponse(pdf_path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@app.get("/api/runs/{run_id}/followup")
def read_followup(run_id: str) -> dict[str, Any]:
    state = _load_current_run(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="没有找到这次分析。")
    turns = _read_json(_run_dir(run_id) / "followup.json", [])
    return {"turns": turns if isinstance(turns, list) else []}


@app.post("/api/runs/{run_id}/followup")
def ask_followup(run_id: str, payload: FollowupQuestion) -> dict[str, Any]:
    state = _load_current_run(run_id)
    if not state or not isinstance(state.get("result"), dict):
        raise HTTPException(status_code=404, detail="当前没有可追问的模块三分析结果。")
    if state.get("run_status") != "completed":
        raise HTTPException(status_code=409, detail="请等本次模块三分析运行结束后再追问。")
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入追问内容。")
    turn_id = uuid.uuid4().hex
    turn = {"id": turn_id, "question": question, "status": "running", "answer": "", "source_pages": [], "created_at": _now(), "updated_at": _now(), "error": None}
    with _turn_lock:
        turns = _read_json(_run_dir(run_id) / "followup.json", [])
        if not isinstance(turns, list):
            turns = []
        turns.append(turn)
        _write_json_atomic(_run_dir(run_id) / "followup.json", turns)
    _executor.submit(_followup_worker, run_id, turn_id, question)
    return turn


@app.get("/")
def workbench_page() -> HTMLResponse:
    page = STATIC_ROOT / "index.html"
    if not page.is_file():
        raise HTTPException(status_code=500, detail="模块三工作台页面文件缺失。")
    return HTMLResponse(page.read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="assets-workbench-static")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("ASSETS_WORKBENCH_PORT", "8103")), log_level="warning")


if __name__ == "__main__":
    main()
