"""独立的模块四现金流分析工作台，固定运行在 8104。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv, set_key
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"
DATA_ROOT = ROOT / "data" / "现金流工作台"
RUNS_ROOT = DATA_ROOT / "runs"
RECORDS_ROOT = DATA_ROOT / "records"
LOG_ROOT = DATA_ROOT / "logs"
LOCAL_CONFIG = ROOT / ".env"
FALLBACK_CONFIG = Path(os.getenv(
    "FINLAB_FALLBACK_CONFIG_PATH",
    str(ROOT / ".env"),
)).expanduser()
PORT = 8104
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
RUN_ID_PATTERN = re.compile(r"^[a-f0-9]{16,40}$")

# The task-local config wins. The existing V3.0.1 config is read-only fallback.
load_dotenv(FALLBACK_CONFIG, override=False)
load_dotenv(LOCAL_CONFIG, override=True)
os.environ["FINLAB_CONFIG_PATH"] = str(LOCAL_CONFIG)

from backend.core.context import ReportContext  # noqa: E402
from backend.core.recorder import RunRecorder  # noqa: E402
from backend.deepseek_client import ModelCallError  # noqa: E402
from modules.part4_cashflow.agent import MODULE_ID, MODULE_VERSION, PROMPT_PATH, analyze_cashflow  # noqa: E402
from backend.pdf_reader import PdfInputError, extract_all_pages, rank_search_pages  # noqa: E402

for directory in (DATA_ROOT, RUNS_ROOT, RECORDS_ROOT, LOG_ROOT):
    directory.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_ROOT / "模块四工作台.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("cashflow_workbench")
app = FastAPI(title="模块四｜现金流与利润兑现", version="cashflow-workbench-1.0")
_store_lock = threading.RLock()


class KeySettings(BaseModel):
    api_key: str = Field(default="", max_length=500)
    model: str = Field(default="deepseek-flash", min_length=1, max_length=100)
    base_url: str = Field(default="https://api.deepseek.com", min_length=8, max_length=300)


class FollowupQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1200)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_read(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def _run_dir(run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="没有找到这次模块四分析。")
    path = RUNS_ROOT / run_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="没有找到这次模块四分析。")
    return path


def _load_metadata(run_id: str) -> dict[str, Any]:
    metadata = _json_read(_run_dir(run_id) / "metadata.json")
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=404, detail="这次分析记录不可读取。")
    return metadata


def _save_metadata(run_id: str, metadata: dict[str, Any]) -> None:
    metadata["updated_at"] = _now()
    _json_write(_run_dir(run_id) / "metadata.json", metadata)


def _history_ids() -> list[str]:
    ids = _json_read(DATA_ROOT / "history.json", [])
    return [str(item) for item in ids if isinstance(item, str)] if isinstance(ids, list) else []


def _set_history_ids(ids: list[str]) -> None:
    _json_write(DATA_ROOT / "history.json", list(dict.fromkeys(ids))[:100])


def _result_path(run_id: str) -> Path:
    return _run_dir(run_id) / "result.json"


def _read_pages(run_id: str) -> list[dict[str, Any]]:
    pages = _json_read(_run_dir(run_id) / "pages.json", [])
    return pages if isinstance(pages, list) else []


def _add_progress(run_id: str, stage: str, status: str, detail: str = "") -> None:
    with _store_lock:
        metadata = _load_metadata(run_id)
        steps = metadata.get("steps", [])
        steps.append({"stage": stage, "status": status, "detail": detail[:900], "at": _now()})
        metadata["steps"] = steps[-80:]
        metadata["stage"] = stage
        _save_metadata(run_id, metadata)


def _update_run(run_id: str, **changes: Any) -> dict[str, Any]:
    with _store_lock:
        metadata = _load_metadata(run_id)
        metadata.update(changes)
        _save_metadata(run_id, metadata)
        return metadata


def _safe_error(exc: Exception) -> str:
    name = type(exc).__name__
    if isinstance(exc, ModelCallError):
        message = str(exc)
        if any(secret in message.lower() for secret in ("api_key", "authorization", "bearer", "sk-")):
            return "模型配置或调用失败，敏感凭据已省略。"
        return message[:600]
    if name == "AuthenticationError":
        return "DeepSeek 拒绝了当前 API Key，请检查密钥是否有效。"
    if name == "RateLimitError":
        return "DeepSeek 请求受限或账户余额不足；请检查账户状态后再试。"
    if name == "APITimeoutError":
        return "DeepSeek 响应超时；本次已保存的中间结果仍可查看。"
    if name == "APIConnectionError":
        return "后台无法连接 DeepSeek；本次已保存的中间结果仍可查看。"
    status_code = getattr(exc, "status_code", None)
    if status_code:
        return f"DeepSeek 接口返回 HTTP {status_code}；本次已保存的中间结果仍可查看。"
    return f"处理过程中出现 {name}；本次已保存的中间结果仍可查看。"


def _terminal_status(result: dict[str, Any], failure_reason: str | None = None) -> str:
    sections = result.get("sections") if isinstance(result.get("sections"), list) else []
    facts = result.get("facts") if isinstance(result.get("facts"), list) else []
    analyzed = [s for s in sections if isinstance(s, dict) and s.get("status") == "analyzed"]
    if failure_reason:
        return "partial" if facts or analyzed else "failed"
    if not facts and not analyzed and sections and all(
        isinstance(s, dict) and s.get("status") in {"insufficient_evidence", "not_applicable"}
        for s in sections
    ):
        return "insufficient_data"
    if result.get("analysis_completeness") == "partial" or len(analyzed) < 5:
        return "partial" if facts or analyzed else "insufficient_data"
    return "completed"


def _public_run(run_id: str, *, include_result: bool = True) -> dict[str, Any]:
    metadata = _load_metadata(run_id)
    result = _json_read(_result_path(run_id))
    followups = _json_read(_run_dir(run_id) / "followups.json", [])
    return {
        **metadata,
        "result": result if include_result else None,
        "followups": followups if isinstance(followups, list) else [],
    }


def _import_saved_run_once() -> None:
    """Bring the already-saved result into local history as a read-only record."""
    existing_ids = _history_ids()
    source_dir = ROOT / "data" / "开发运行" / "5ee97e4adbf64d81bf41c97739d3e040" / "attempt-0001"
    source_result = source_dir / "artifacts" / "complete_result.json"
    source_pages = source_dir / "artifacts" / "full_extracted_pages.json"
    if not source_result.is_file() or not source_pages.is_file():
        return
    envelope = _json_read(source_result)
    pages = _json_read(source_pages)
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict) or not isinstance(pages, list):
        return
    run_id = "5ee97e4adbf64d81bf41c97739d3e040"
    target_dir = RUNS_ROOT / run_id
    if run_id in existing_ids or target_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    _json_write(target_dir / "result.json", envelope["result"])
    _json_write(target_dir / "pages.json", pages)
    _json_write(target_dir / "followups.json", [])
    completed_at = envelope.get("completed_at") or _now()
    _json_write(target_dir / "metadata.json", {
        "id": run_id,
        "file_name": envelope.get("file_name") or "600887_伊利股份_2024年年度报告.pdf",
        "company": envelope["result"].get("company", ""),
        "report_year": envelope["result"].get("report_year", ""),
        "sha256": envelope.get("report_id", envelope["result"].get("report_id", "")),
        "page_count": len(pages),
        "read_pages": envelope.get("read_pages", []),
        "status": "completed" if envelope.get("status") == "completed" else "partial",
        "stage": "导入已有模块四分析结果",
        "analysis_completeness": envelope["result"].get("analysis_completeness"),
        "error": envelope.get("error"),
        "created_at": completed_at,
        "updated_at": completed_at,
        "source_kind": "已保存的模块四测试结果",
        "pdf_available": False,
        "steps": [{
            "stage": "读取已有完整结果和原文页文本",
            "status": "已完成",
            "detail": "这是此前保存的运行结果；可查阅提取页文本，但记录未包含原始 PDF 文件。",
            "at": completed_at,
        }],
    })
    _set_history_ids([run_id, *existing_ids])


@app.on_event("startup")
def startup() -> None:
    _import_saved_run_once()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "workbench": "cashflow",
        "module_id": MODULE_ID,
        "module_version": MODULE_VERSION,
        "workbench_version": app.version,
        "port": PORT,
        "configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        "storage": str(DATA_ROOT),
    }


@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    local_values = dotenv_values(LOCAL_CONFIG) if LOCAL_CONFIG.exists() else {}
    local_has_key = bool(str(local_values.get("DEEPSEEK_API_KEY") or "").strip())
    fallback_values = dotenv_values(FALLBACK_CONFIG) if FALLBACK_CONFIG.exists() else {}
    fallback_has_key = bool(str(fallback_values.get("DEEPSEEK_API_KEY") or "").strip())
    return {
        "configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "source": "本任务本机配置" if local_has_key else ("已有本机配置（只读）" if fallback_has_key else "未配置"),
    }


@app.post("/api/settings")
def save_settings(payload: KeySettings) -> dict[str, Any]:
    key = payload.api_key.strip()
    if not key and not os.getenv("DEEPSEEK_API_KEY", "").strip():
        raise HTTPException(status_code=400, detail="请填写 DeepSeek API Key。")
    if not payload.base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="API 地址需以 http:// 或 https:// 开头。")
    LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if key:
        set_key(str(LOCAL_CONFIG), "DEEPSEEK_API_KEY", key, quote_mode="never")
        os.environ["DEEPSEEK_API_KEY"] = key
    set_key(str(LOCAL_CONFIG), "DEEPSEEK_MODEL", payload.model.strip(), quote_mode="never")
    set_key(str(LOCAL_CONFIG), "DEEPSEEK_BASE_URL", payload.base_url.strip(), quote_mode="never")
    os.environ["DEEPSEEK_MODEL"] = payload.model.strip()
    os.environ["DEEPSEEK_BASE_URL"] = payload.base_url.strip()
    return {"saved": True, "configured": True, "model": payload.model.strip()}


@app.get("/api/history")
def history() -> dict[str, Any]:
    items = []
    for run_id in _history_ids():
        try:
            metadata = _load_metadata(run_id)
        except HTTPException:
            continue
        items.append({key: metadata.get(key) for key in (
            "id", "file_name", "company", "report_year", "status", "stage",
            "analysis_completeness", "created_at", "source_kind",
        )})
    return {"items": items}


@app.post("/api/runs")
async def upload_report(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> dict[str, Any]:
    file_name = Path(file.filename or "年报.pdf").name
    if not file_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="请上传 PDF 年报文件。")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 25 MB，请先压缩 PDF 后重试。")
    try:
        extracted = extract_all_pages(content)
    except PdfInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not any(str(page.get("text", "")).strip() for page in extracted.selected_pages):
        raise HTTPException(status_code=422, detail="PDF 没有可提取文字，当前版本不能分析扫描件。")
    run_id = uuid.uuid4().hex
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "report.pdf").write_bytes(content)
    _json_write(run_dir / "pages.json", extracted.selected_pages)
    _json_write(run_dir / "followups.json", [])
    created = _now()
    _json_write(run_dir / "metadata.json", {
        "id": run_id, "file_name": file_name, "company": "", "report_year": "",
        "sha256": hashlib.sha256(content).hexdigest(), "page_count": extracted.page_count,
        "read_pages": [], "status": "queued", "stage": "等待模块四分析",
        "analysis_completeness": None, "error": None, "created_at": created,
        "updated_at": created, "source_kind": "本次上传", "pdf_available": True,
        "steps": [{
            "stage": "PDF 已保存并完成全文文字提取", "status": "已完成",
            "detail": f"共 {extracted.page_count} 页；已保存在本任务工作台目录。", "at": created,
        }],
    })
    _set_history_ids([run_id, *_history_ids()])
    background_tasks.add_task(_process_analysis, run_id)
    return _public_run(run_id)


def _process_analysis(run_id: str) -> None:
    metadata = _load_metadata(run_id)
    recorder = RunRecorder(RECORDS_ROOT / run_id / "attempt-0001", run_id=run_id, module_id=MODULE_ID)
    recorder.write_manifest({
        "run_id": run_id, "module_id": MODULE_ID, "module_version": MODULE_VERSION,
        "report": {"file_name": metadata["file_name"], "sha256": metadata["sha256"], "page_count": metadata["page_count"]},
        "started_at": _now(), "workbench_port": PORT,
    })
    try:
        _update_run(run_id, status="running", stage="正在准备模块四现金流分析")
        if not os.getenv("DEEPSEEK_API_KEY", "").strip():
            raise ModelCallError("尚未配置 DeepSeek API Key。请点击页面右上角的配置按钮。")
        pages = _read_pages(run_id)
        _json_write(RECORDS_ROOT / run_id / "extracted_pages.json", pages)
        recorder.record("cashflow_workbench_started", initial_page_pool=len(pages))

        def on_progress(stage: str, status: str, detail: str) -> None:
            _add_progress(run_id, stage, status, detail)
            if status in {"进行中", "已完成", "部分完成", "失败"}:
                _update_run(run_id, stage=f"{stage} · {detail}" if detail else stage)

        context = ReportContext(
            report_id=metadata["sha256"], file_name=metadata["file_name"],
            file_sha256=metadata["sha256"], page_count=int(metadata["page_count"]),
            pages=pages, recorder=recorder,
            _get_page=lambda page_no: next(
                (str(page.get("text", "")) for page in pages if int(page.get("page", 0)) == page_no), None
            ),
            _search_pages=lambda query: rank_search_pages(pages, query, limit=8),
            _on_progress=on_progress,
        )
        workflow = analyze_cashflow(context)
        result = workflow.get("result", workflow)
        if not isinstance(result, dict):
            raise RuntimeError("模块四没有返回结构化结果。")
        failure_reason = str(result.get("failure_reason") or "").strip() or None
        status = _terminal_status(result, failure_reason)
        read_pages = sorted({int(page) for page in workflow.get("read_pages", []) if str(page).isdigit()})
        _json_write(_result_path(run_id), result)
        recorder.write_result({
            "run_id": run_id, "module_id": MODULE_ID, "module_version": MODULE_VERSION,
            "status": status, "error": failure_reason, "read_pages": read_pages,
            "result": result, "completed_at": _now(),
        })
        recorder.record("cashflow_workbench_finished", status=status, section_count=len(result.get("sections", [])), read_page_count=len(read_pages))
        _update_run(
            run_id, status=status,
            stage={"completed": "模块四分析结束", "partial": "模块四部分完成", "insufficient_data": "资料不足，分析未能展开", "failed": "模块四分析失败"}.get(status, "模块四分析结束"),
            company=result.get("company", ""), report_year=result.get("report_year", ""),
            read_pages=read_pages, analysis_completeness=result.get("analysis_completeness"),
            error=failure_reason, finished_at=_now(),
        )
        _add_progress(run_id, "保存模块四结果", "已完成" if status == "completed" else status,
                      failure_reason or "五个板块、引用、计算、专题与复核信息已保存。")
    except Exception as exc:
        reason = _safe_error(exc)
        logger.warning("cashflow run %s failed (%s)", run_id, type(exc).__name__)
        recorder.record("cashflow_workbench_failed", error_type=type(exc).__name__)
        failure_result = _json_read(_result_path(run_id))
        status = _terminal_status(failure_result, reason) if isinstance(failure_result, dict) and failure_result.get("sections") else "failed"
        recorder.write_result({
            "run_id": run_id, "module_id": MODULE_ID, "module_version": MODULE_VERSION,
            "status": status, "error": reason, "result": failure_result, "completed_at": _now(),
        })
        _add_progress(run_id, "分析运行结束", status, reason)
        _update_run(run_id, status=status, stage="模块四分析失败" if status == "failed" else "模块四部分完成",
                    error=reason, finished_at=_now())


@app.get("/api/runs/{run_id}")
def read_run(run_id: str) -> dict[str, Any]:
    return _public_run(run_id)


@app.get("/api/runs/{run_id}/pages/{page_number}")
def read_page(run_id: str, page_number: int) -> dict[str, Any]:
    pages = _read_pages(run_id)
    page = next((item for item in pages if int(item.get("page", 0)) == page_number), None)
    if page is None:
        raise HTTPException(status_code=404, detail="没有保存这一个 PDF 物理页的提取文本。")
    return {"page": page_number, "text": str(page.get("text", "")), "file_name": _load_metadata(run_id).get("file_name")}


@app.get("/api/runs/{run_id}/file")
def read_original_pdf(run_id: str) -> FileResponse:
    path = _run_dir(run_id) / "report.pdf"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="这是已导入的历史分析记录，工作台未保存原始 PDF；可以回查其提取页文本。")
    metadata = _load_metadata(run_id)
    return FileResponse(path, media_type="application/pdf", filename=metadata.get("file_name", "annual-report.pdf"))


FOLLOWUP_SYSTEM_GUIDANCE = """
你现在回答的是模块四「现金流与利润兑现」的年报追问。每次调用都必须遵守本模块指导：区分现金流量表期间金额与现金余额时点金额；现金流出和负数保持报告方向；不得把空值当作零；不得把文字或金额相似匹配说成已证实；每个金额说明期间、单位和合并或母公司范围，并尽可能给出 PDF 物理页码。当前模块结果是本轮分析的事实底稿，不能把缺项补成确定结论。遇到结果不足时，调用搜索或读取工具回到年报；只根据工具返回的页文本陈述原文证据。追问超出现金流模块范围时说明边界。回答中文、分点清楚，避免投资建议。
""".strip()

FOLLOWUP_TOOLS = [
    {"type": "function", "function": {
        "name": "search_cashflow_pages",
        "description": "搜索本次年报全文提取文本，返回现金流相关页码与原文片段。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        }, "required": ["query"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_cashflow_page",
        "description": "按 PDF 物理页码读取本次年报原文。",
        "parameters": {"type": "object", "properties": {"page": {"type": "integer", "minimum": 1}},
                       "required": ["page"], "additionalProperties": False},
    }},
]


def _process_followup(run_id: str, turn_id: str) -> None:
    run_dir = _run_dir(run_id)
    turns_path = run_dir / "followups.json"
    turns = _json_read(turns_path, [])
    turn = next((item for item in turns if item.get("id") == turn_id), None)
    if not isinstance(turn, dict):
        return
    turn["status"] = "running"
    turn["updated_at"] = _now()
    _json_write(turns_path, turns)
    metadata = _load_metadata(run_id)
    result = _json_read(_result_path(run_id))
    recorder = RunRecorder(RECORDS_ROOT / run_id / "followups" / turn_id, run_id=run_id, module_id="cashflow_followup")
    try:
        if not isinstance(result, dict):
            raise ModelCallError("这次分析没有可继续追问的结构化结果。")
        pages = _read_pages(run_id)
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8") + "\n\n" + FOLLOWUP_SYSTEM_GUIDANCE

        def on_tool(name: str, detail: str) -> None:
            _add_progress(run_id, "模块四追问", "进行中", f"正在使用 {name}：{detail}")

        context = ReportContext(
            report_id=str(metadata.get("sha256", "")), file_name=str(metadata.get("file_name", "")),
            file_sha256=str(metadata.get("sha256", "")), page_count=int(metadata.get("page_count", 0)),
            pages=pages, recorder=recorder,
            _get_page=lambda page_no: next((str(p.get("text", "")) for p in pages if int(p.get("page", 0)) == page_no), None),
            _search_pages=lambda query: rank_search_pages(pages, query, limit=8),
            _on_tool=on_tool,
        )
        all_turns = _json_read(turns_path, [])
        previous = [
            {"question": item.get("question", ""), "answer": item.get("answer", "")}
            for item in all_turns if item.get("id") != turn_id and item.get("status") == "completed"
        ][-6:]
        request_context = {
            "module": "模块四｜现金流与利润兑现",
            "report": {"file_name": metadata.get("file_name"), "page_count": metadata.get("page_count"), "sha256": metadata.get("sha256")},
            "full_module_result": result, "recent_followup_history": previous,
            "user_question": turn["question"],
            "instruction": "当前结果和历史是问题上下文；必要时用工具回查本次已保存年报页文本。每一轮请求都带模块专业提示和当前结果。",
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(request_context, ensure_ascii=False, separators=(",", ":"))},
        ]
        answer = ""
        read_pages: set[int] = set()
        for round_number in range(1, 7):
            recorder.record("followup_model_round_started", round=round_number)
            response = context.call_model(messages=messages, tools=FOLLOWUP_TOOLS, tool_choice="auto", temperature=0.15, max_tokens=2200)
            message = response.choices[0].message
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                answer = str(getattr(message, "content", "") or "").strip()
                break
            messages.append(message.model_dump(exclude_none=True))
            for call in calls[:6]:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                    if name == "search_cashflow_pages":
                        query = str(args.get("query", "")).strip()
                        limit = max(1, min(int(args.get("limit", 5)), 8))
                        hits = context.search_pages(query, limit=limit)[:limit]
                        read_pages.update(int(hit["page"]) for hit in hits if hit.get("page") is not None)
                        tool_result = {"query": query, "results": hits}
                    elif name == "read_cashflow_page":
                        page_number = int(args.get("page", 0))
                        page_text = context.read_page(page_number)
                        if page_text is None:
                            tool_result = {"error": "未找到该物理页的文本。", "page": page_number}
                        else:
                            read_pages.add(page_number)
                            tool_result = {"page": page_number, "text": page_text[:7000]}
                    else:
                        tool_result = {"error": "模块四追问不支持此工具。"}
                    context.record_tool(name, args, tool_result, pages=sorted(read_pages))
                except Exception as exc:
                    tool_result = {"error": _safe_error(exc)}
                    context.record_tool(name, {}, tool_result)
                messages.append({
                    "role": "tool", "tool_call_id": call.id, "name": name,
                    "content": json.dumps(tool_result, ensure_ascii=False, separators=(",", ":")),
                })
        if not answer:
            answer = "本轮追问没有生成完整回答。已保留可查阅的原文页码；可缩小问题范围后再次追问。"
        turn.update({"answer": answer, "read_pages": sorted(read_pages), "status": "completed", "error": None, "updated_at": _now()})
        if read_pages:
            merged_pages = sorted(set(int(page) for page in metadata.get("read_pages", [])) | read_pages)
            result["read_pages"] = sorted(set(int(page) for page in result.get("read_pages", [])) | read_pages)
            _json_write(_result_path(run_id), result)
            _update_run(run_id, read_pages=merged_pages)
        recorder.record("followup_completed", read_pages=sorted(read_pages))
    except Exception as exc:
        reason = _safe_error(exc)
        turn.update({"answer": "", "read_pages": [], "status": "failed", "error": reason, "updated_at": _now()})
        recorder.record("followup_failed", error_type=type(exc).__name__)
    turns = _json_read(turns_path, [])
    for index, item in enumerate(turns):
        if item.get("id") == turn_id:
            turns[index] = turn
            break
    _json_write(turns_path, turns)


@app.get("/api/runs/{run_id}/followups")
def read_followups(run_id: str) -> dict[str, Any]:
    turns = _json_read(_run_dir(run_id) / "followups.json", [])
    return {"items": turns if isinstance(turns, list) else []}


@app.post("/api/runs/{run_id}/followups")
def ask_followup(run_id: str, payload: FollowupQuestion, background_tasks: BackgroundTasks) -> dict[str, Any]:
    metadata = _load_metadata(run_id)
    if metadata.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="分析仍在运行，完成后即可追问。")
    if not _json_read(_result_path(run_id)):
        raise HTTPException(status_code=409, detail="这次分析没有可继续追问的结果。")
    turn_id = uuid.uuid4().hex
    turns_path = _run_dir(run_id) / "followups.json"
    turns = _json_read(turns_path, [])
    turn = {"id": turn_id, "question": payload.question.strip(), "answer": "", "read_pages": [],
            "status": "queued", "error": None, "created_at": _now(), "updated_at": _now()}
    turns.append(turn)
    _json_write(turns_path, turns)
    background_tasks.add_task(_process_followup, run_id, turn_id)
    return turn


@app.get("/", response_class=HTMLResponse)
def workbench_page() -> FileResponse:
    page = FRONTEND_DIR / "cashflow.html"
    if not page.is_file():
        raise HTTPException(status_code=500, detail="模块四页面文件不存在。")
    return FileResponse(page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/cashflow.css")
def workbench_css() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "cashflow.css", media_type="text/css; charset=utf-8")


@app.get("/cashflow.js")
def workbench_js() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "cashflow.js", media_type="application/javascript; charset=utf-8")
