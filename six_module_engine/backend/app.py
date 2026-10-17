"""FastAPI application for the first working financial-report prototype."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import set_key
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.core.config import PROJECT_ROOT, config_path, load_runtime_config
from backend.core.context import ReportContext
from backend.core.recorder import RunRecorder

load_runtime_config()

from backend.database import (
    UPLOAD_DIR,
    create_conversation_turn,
    create_run,
    get_conversation_turn,
    get_run,
    get_run_page,
    has_pending_conversation_turn,
    init_db,
    list_analysis_steps,
    list_conversation_turns,
    list_runs,
    save_run_pages,
    search_run_pages,
    set_analysis_step,
    update_conversation_turn,
    update_run,
)
from backend.deepseek_client import MODEL, ModelCallError, api_key_configured
from backend.deepseek_agent import answer_question, analyze_report
from backend.profit_agent import analyze_profit_report, answer_profit_question
from backend.module_registry import registrations
from backend.review_api import router as review_router
from backend.review_database import init_review_db
from backend.pdf_reader import (
    MAX_UPLOAD_BYTES,
    PdfInputError,
    extract_all_pages,
    extract_relevant_pages,
    select_initial_pages,
)


ROOT = PROJECT_ROOT
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("financial_report_app")


class DeepSeekKeyUpdate(BaseModel):
    api_key: str


class ConversationQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1200)


def _source_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return None


app = FastAPI(title="金融投研工作台", version="3.0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    init_review_db()


def _public_run(run: dict[str, Any], *, include_result: bool = True) -> dict[str, Any]:
    result = run.get("result")
    return {
        "id": run["id"],
        "file_name": run["file_name"],
        "analysis_module": run.get("analysis_module", "overview"),
        "sha256": run["sha256"],
        "status": run["status"],
        "stage": run["stage"],
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
        "page_count": run["page_count"],
        "read_pages": run["read_pages"],
        "error": run["error"],
        "result": result if include_result else None,
        "steps": list_analysis_steps(run["id"]),
    }


def _record_page_access(
    recorder: RunRecorder,
    getter: Any,
    run_id: str,
    page_number: int,
) -> str | None:
    try:
        text = getter(run_id, page_number)
    except Exception as exc:
        recorder.record("page_read_failed", page=page_number, error_type=type(exc).__name__)
        raise
    artifact = recorder.save_artifact(
        f"page_read_{page_number}_{uuid.uuid4().hex}",
        {"page": page_number, "text": text},
    )
    recorder.record("page_read", page=page_number, available=text is not None, artifact=artifact)
    return text


def _record_page_search(
    recorder: RunRecorder,
    searcher: Any,
    run_id: str,
    query: str,
) -> list[dict[str, Any]]:
    try:
        results = searcher(run_id, query)
    except Exception as exc:
        recorder.record("page_search_failed", query=query, error_type=type(exc).__name__)
        raise
    artifact = recorder.save_artifact(
        f"page_search_{uuid.uuid4().hex}",
        {"query": query, "results": results},
    )
    recorder.record(
        "page_search",
        query=query,
        pages=[int(item["page"]) for item in results if item.get("page") is not None],
        result_count=len(results),
        artifact=artifact,
    )
    return results


def _process_run(run_id: str) -> None:
    run = get_run(run_id)
    if not run:
        return
    run_record_dir = ROOT / "data" / "app" / "run_records" / run_id
    run_recorder = RunRecorder(run_record_dir, run_id=run_id, module_id="run")
    run_recorder.record(
        "run_started",
        analysis_module=run.get("analysis_module", "overview"),
        file_name=run.get("file_name"),
        report_sha256=run.get("sha256"),
    )
    try:
        module_registrations = registrations()
        analysis_module = run.get("analysis_module", "overview")
        combined = analysis_module == "complete"
        module_keys = ["business", "profit"] if combined else [analysis_module]
        labels = {
            "business": "模块一｜业务与经营背景",
            "profit": "模块二｜盈利来源与变化",
            "overview": "财报概览",
        }
        module_results: dict[str, dict[str, Any]] = {
            key: {"status": "queued", "error": None, "result": None}
            for key in module_keys
        }
        module_pages: dict[str, list[int]] = {key: [] for key in module_keys}
        business_context: dict[str, Any] | None = None
        update_run(run_id, status="running", stage="正在读取并索引全文")
        set_analysis_step(run_id, "读取并索引 PDF 全文", "进行中")
        pdf_path = Path(run["file_path"])
        content = pdf_path.read_bytes()
        extracted = extract_all_pages(content)
        all_pages = extracted.selected_pages
        save_run_pages(run_id, all_pages)
        pages_ref = run_recorder.save_artifact("full_extracted_pages", all_pages)
        run_recorder.write_manifest(
            {
                "run_id": run_id,
                "source_revision": _source_revision(),
                "report": {
                    "file_name": run["file_name"],
                    "sha256": run["sha256"],
                    "page_count": extracted.page_count,
                    "pages_artifact": pages_ref,
                },
            }
        )
        run_recorder.record(
            "input_file_access",
            sha256=run["sha256"],
            page_count=extracted.page_count,
            extracted_page_count=len(all_pages),
        )
        set_analysis_step(
            run_id,
            "读取并索引 PDF 全文",
            "已完成",
            f"共 {extracted.page_count} 页；已保存可提取文字的页面，供后续追问检索。",
        )
        accumulated_pages: set[int] = set()

        def combined_payload() -> dict[str, Any]:
            business = module_results.get("business", {}).get("result") or {}
            profit = module_results.get("profit", {}).get("result") or {}
            return {
                "module": "combined",
                "company": business.get("company") or profit.get("company") or "",
                "period": business.get("period") or profit.get("period") or "",
                "document_sha256": run["sha256"],
                "modules": module_results,
                "read_pages": sorted(accumulated_pages),
                "usage": {
                    "prompt_tokens": sum(
                        (entry.get("result") or {}).get("usage", {}).get("prompt_tokens", 0)
                        for entry in module_results.values()
                    ),
                    "completion_tokens": sum(
                        (entry.get("result") or {}).get("usage", {}).get("completion_tokens", 0)
                        for entry in module_results.values()
                    ),
                },
                "module_order": ["business", "profit"] if combined else module_keys,
            }

        for module_key in module_keys:
            module_label = labels.get(module_key, "财报分析")
            module_version = (
                module_registrations[module_key].version
                if module_key in module_registrations
                else "财报助手_v2"
            )
            module_dir = run_record_dir / module_key / "attempt-0001"
            module_recorder = RunRecorder(module_dir, run_id=run_id, module_id=module_key)
            module_recorder.write_manifest(
                {
                    "run_id": run_id,
                    "module_id": module_key,
                    "module_version": module_version,
                    "report_sha256": run["sha256"],
                }
            )
            if combined:
                module_results[module_key] = {"status": "running", "error": None, "result": None}
                update_run(
                    run_id,
                    stage=f"正在开始{module_label}",
                    read_pages=sorted(accumulated_pages),
                    result=combined_payload(),
                )
            initial_pages = select_initial_pages(all_pages, analysis_module=module_key)
            if not initial_pages:
                error = "PDF 中没有找到可发送给模型的文字页面。"
                module_recorder.record("module_failed", error=error)
                module_recorder.write_result(
                    {
                        "report_id": run["sha256"],
                        "module_id": module_key,
                        "status": "failed",
                        "error": error,
                        "result": None,
                    }
                )
                module_results[module_key] = {"status": "failed", "error": error, "result": None}
                set_analysis_step(run_id, f"{module_label} · 选择分析页面", "需要处理", error)
                if combined:
                    update_run(
                        run_id,
                        stage=f"{module_label}未能开始",
                        read_pages=sorted(accumulated_pages),
                        result=combined_payload(),
                    )
                continue

            selected_numbers = {int(page["page"]) for page in initial_pages}
            accumulated_pages.update(selected_numbers)
            module_recorder.save_artifact("selected_initial_pages", initial_pages)
            module_recorder.record(
                "initial_pages_selected",
                pages=sorted(selected_numbers),
                candidate_count=len(initial_pages),
            )
            update_run(
                run_id,
                stage=f"正在分析{module_label}",
                page_count=extracted.page_count,
                read_pages=sorted(accumulated_pages),
                result=combined_payload() if combined else None,
            )
            set_analysis_step(
                run_id,
                f"{module_label} · 分析模块",
                "进行中",
                f"首批候选页：{len(initial_pages)} 页",
            )

            def on_stage(name: str, status: str, detail: str) -> None:
                module_recorder.progress(name, status, detail)
                step_name = f"{module_label} · {name}"
                set_analysis_step(run_id, step_name, status, detail)
                if status == "进行中":
                    update_run(run_id, stage=f"正在{name}")

            def on_tool(name: str, detail: str) -> None:
                if name in {"search_pdf_pages", "read_pdf_page"}:
                    step_name = f"{module_label} · 定向查阅原文"
                    set_analysis_step(run_id, step_name, "进行中", f"正在调用 {name}：{detail}")
                    update_run(run_id, stage=f"{module_label}正在查阅年报原文")
                elif name == "calculate_change":
                    set_analysis_step(run_id, f"{module_label} · 程序计算", "进行中", "正在核算可比数值")
                    update_run(run_id, stage=f"{module_label}正在核算数值")

            workflow_args = {
                "file_name": run["file_name"],
                "page_count": extracted.page_count,
                "initial_pages": initial_pages,
                "get_page": lambda page_number: _record_page_access(
                    module_recorder, get_run_page, run_id, page_number
                ),
                "search_pages": lambda query: _record_page_search(
                    module_recorder, search_run_pages, run_id, query
                ),
                "on_tool": on_tool,
                "on_stage": on_stage,
                "recorder": module_recorder,
            }
            try:
                module_recorder.record("module_started")
                if module_key in module_registrations:
                    module_context = ReportContext(
                        report_id=run["sha256"],
                        file_name=run["file_name"],
                        file_sha256=run["sha256"],
                        page_count=extracted.page_count,
                        pages=all_pages,
                        initial_pages=initial_pages,
                        recorder=module_recorder,
                        related_results={"business": business_context} if business_context else {},
                        _get_page=lambda page_number: get_run_page(run_id, page_number),
                        _search_pages=lambda query: search_run_pages(run_id, query),
                        _on_progress=on_stage,
                        _on_tool=on_tool,
                    )
                    workflow = module_registrations[module_key].run(module_context)
                elif module_key == "profit":
                    workflow = analyze_profit_report(
                        **workflow_args,
                        business_context=business_context if combined else None,
                    )
                else:
                    workflow = analyze_report(**workflow_args, analysis_module=module_key)
                result = workflow["result"]
                module_pages[module_key] = [int(page) for page in workflow["read_pages"]]
                accumulated_pages.update(module_pages[module_key])
                result["document_sha256"] = run["sha256"]
                result["prompt_version"] = module_version
                result["workflow_trace"] = workflow["trace"]
                module_recorder.record(
                    "module_result_ready",
                    read_pages=module_pages[module_key],
                    analysis_review_status=result.get("analysis_review_status"),
                )
                module_recorder.write_result(
                    {
                        "report_id": run["sha256"],
                        "module_id": module_key,
                        "module_version": result["prompt_version"],
                        "status": "completed",
                        "read_pages": module_pages[module_key],
                        "result": result,
                    }
                )
                module_results[module_key] = {"status": "completed", "error": None, "result": result}
                if module_key == "business":
                    business_context = result
                extra_pages = sorted(set(module_pages[module_key]) - selected_numbers)
                set_analysis_step(
                    run_id,
                    f"{module_label} · 定向查阅原文",
                    "已完成",
                    f"额外读取 {len(extra_pages)} 页" if extra_pages else "本模块未需要额外查页。",
                )
                set_analysis_step(
                    run_id,
                    f"{module_label} · 分析模块",
                    "已完成",
                    result.get("analysis_review_status", "结构化结果已保存。"),
                )
            except (PdfInputError, ModelCallError) as exc:
                module_recorder.record("module_failed", error_type=type(exc).__name__, error=str(exc))
                module_recorder.write_result(
                    {
                        "report_id": run["sha256"],
                        "module_id": module_key,
                        "status": "failed",
                        "error": str(exc),
                        "result": None,
                    }
                )
                if not combined:
                    raise
                module_results[module_key] = {"status": "failed", "error": str(exc), "result": None}
                set_analysis_step(run_id, f"{module_label} · 分析模块", "需要处理", str(exc))
            except Exception:
                logger.exception("Run %s module %s failed unexpectedly", run_id, module_key)
                module_recorder.record("module_failed", error_type="UnexpectedError")
                module_recorder.write_result(
                    {
                        "report_id": run["sha256"],
                        "module_id": module_key,
                        "status": "failed",
                        "error": "该模块处理时发生意外错误。",
                        "result": None,
                    }
                )
                if not combined:
                    raise
                error = "该模块处理时发生意外错误；另一模块结果仍会保留。"
                module_results[module_key] = {"status": "failed", "error": error, "result": None}
                set_analysis_step(run_id, f"{module_label} · 分析模块", "需要处理", error)

            update_run(
                run_id,
                stage=f"{module_label}分析完成" if module_results[module_key]["status"] == "completed" else f"{module_label}需要处理",
                read_pages=sorted(accumulated_pages),
                result=combined_payload() if combined else None,
            )

        if combined:
            completed = [key for key, value in module_results.items() if value["status"] == "completed"]
            if not completed:
                errors = "；".join(
                    f"{labels.get(key, key)}：{value['error']}"
                    for key, value in module_results.items()
                    if value.get("error")
                )
                update_run(
                    run_id,
                    status="failed",
                    stage="两个分析模块都未能完成",
                    read_pages=sorted(accumulated_pages),
                    result=combined_payload(),
                    error=errors or "模块一和模块二都未能完成。",
                )
                run_recorder.record("run_failed", completed_modules=[], failed_modules=module_keys)
                return
            failed = [key for key, value in module_results.items() if value["status"] != "completed"]
            if failed:
                stage = "部分完成：" + "、".join(labels[key] for key in completed) + "已完成"
            else:
                stage = "模块一和模块二分析完成"
            set_analysis_step(
                run_id,
                "整理完整财报分析结果",
                "已完成" if not failed else "部分完成",
                "两个模块分别保存分析和出处。" if not failed else "已完成模块的结果已保留；失败模块状态和原因已列出。",
            )
            update_run(
                run_id,
                status="completed",
                stage=stage,
                read_pages=sorted(accumulated_pages),
                result=combined_payload(),
                error=None,
            )
            run_recorder.record(
                "run_completed",
                completed_modules=completed,
                failed_modules=failed,
                result_artifact=run_recorder.save_artifact("combined_result", combined_payload()),
            )
        else:
            module_result = module_results[analysis_module]
            if module_result["status"] != "completed":
                error = module_result.get("error") or "分析模块未能完成。"
                update_run(run_id, status="failed", stage="需要处理", error=error)
                run_recorder.record("run_failed", completed_modules=[], failed_modules=[analysis_module], error=error)
                return
            set_analysis_step(run_id, "保存结构化结果", "已完成", "模块结果和出处已保存。")
            update_run(
                run_id,
                status="completed",
                stage="分析完成",
                read_pages=sorted(accumulated_pages),
                result=module_result["result"],
                error=None,
            )
            run_recorder.record(
                "run_completed",
                completed_modules=[analysis_module],
                failed_modules=[],
                result_artifact=run_recorder.save_artifact("run_result", module_result["result"]),
            )
    except (PdfInputError, ModelCallError) as exc:
        run_recorder.record("run_failed", error_type=type(exc).__name__, error=str(exc))
        set_analysis_step(run_id, "分析任务", "需要处理", str(exc))
        update_run(run_id, status="failed", stage="需要处理", error=str(exc))
    except Exception:
        logger.exception("Run %s failed unexpectedly", run_id)
        run_recorder.record("run_failed", error_type="UnexpectedError")
        set_analysis_step(run_id, "分析任务", "需要处理", "处理时发生意外错误。")
        update_run(
            run_id,
            status="failed",
            stage="需要处理",
            error="处理时发生意外错误。请查看后端运行窗口中的错误信息，再重试。",
        )


def _process_conversation_turn(run_id: str, turn_id: str) -> None:
    run = get_run(run_id)
    turn = get_conversation_turn(turn_id)
    if not run or not turn:
        return
    update_conversation_turn(turn_id, status="running", error=None)
    update_run(run_id, status="running", stage="正在检索原文并回答追问")
    set_analysis_step(run_id, "回答用户追问", "进行中", turn["question"][:240])
    turn_recorder = RunRecorder(
        ROOT / "data" / "app" / "run_records" / run_id / "turns" / turn_id,
        run_id=run_id,
        module_id=f"followup_{run.get('analysis_module', 'overview')}",
    )
    try:
        turn_recorder.record("followup_started", question=turn["question"])
        if not run.get("result"):
            raise ModelCallError("这次分析没有可继续追问的结果，请重新上传年报。")
        history = list_conversation_turns(run_id)

        def on_tool(name: str, detail: str) -> None:
            if name in {"search_pdf_pages", "read_pdf_page"}:
                update_run(run_id, stage="正在按需查阅原文")
                set_analysis_step(run_id, "回答用户追问", "进行中", f"正在调用 {name}：{detail}")
            elif name == "calculate_change":
                update_run(run_id, stage="正在核算可比数值")

        answer_function = answer_profit_question if run.get("analysis_module") == "profit" else answer_question
        answer_args = {
            "file_name": run["file_name"],
            "page_count": run["page_count"],
            "initial_result": run["result"],
            "history": history,
            "question": turn["question"],
            "get_page": lambda page_number: _record_page_access(
                turn_recorder, get_run_page, run_id, page_number
            ),
            "search_pages": lambda query: _record_page_search(
                turn_recorder, search_run_pages, run_id, query
            ),
            "on_tool": on_tool,
            "recorder": turn_recorder,
        }
        if answer_function is answer_profit_question:
            answer = answer_function(**answer_args)
        else:
            answer = answer_function(
                **answer_args,
                analysis_module=run.get("analysis_module", "overview"),
            )
        turn_recorder.save_artifact("followup_answer", answer)
        turn_recorder.record("followup_completed", read_pages=answer["read_pages"])
        update_conversation_turn(
            turn_id,
            answer=answer["answer"],
            trace=answer["trace"],
            status="completed",
            error=None,
        )
        result = run["result"]
        result["read_pages"] = sorted(
            set(int(page) for page in result.get("read_pages", [])) | set(answer["read_pages"])
        )
        result["usage"] = result.get("usage", {})
        for key in ("prompt_tokens", "completion_tokens"):
            result["usage"][key] = (result["usage"].get(key) or 0) + (answer["usage"].get(key) or 0)
        update_run(
            run_id,
            status="completed",
            stage="分析完成",
            read_pages=result["read_pages"],
            result=result,
            error=None,
        )
        set_analysis_step(
            run_id,
            "回答用户追问",
            "已完成",
            f"新增读取 {len(answer['read_pages'])} 页；本轮引用待核对页：{answer['unsupported_pages']}",
        )
    except (ModelCallError, PdfInputError) as exc:
        turn_recorder.record("followup_failed", error_type=type(exc).__name__, error=str(exc))
        update_conversation_turn(turn_id, status="failed", error=str(exc))
        update_run(run_id, status="completed", stage="分析完成", error=None)
        set_analysis_step(run_id, "回答用户追问", "需要处理", str(exc))
    except Exception:
        logger.exception("Conversation turn %s failed unexpectedly", turn_id)
        turn_recorder.record("followup_failed", error_type="UnexpectedError")
        update_conversation_turn(
            turn_id,
            status="failed",
            error="回答时发生意外错误，请稍后重试。",
        )
        update_run(run_id, status="completed", stage="分析完成", error=None)
        set_analysis_step(run_id, "回答用户追问", "需要处理", "回答时发生意外错误。")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "api_configured": api_key_configured(), "model": MODEL}


@app.post("/api/settings/deepseek-key")
def save_deepseek_key(payload: DeepSeekKeyUpdate) -> dict[str, bool]:
    api_key = payload.api_key.strip()
    if not api_key or len(api_key) > 512 or any(character.isspace() for character in api_key):
        raise HTTPException(status_code=400, detail="API Key 不能为空，且不能包含空格或换行。")
    try:
        saved, _, _ = set_key(config_path(), "DEEPSEEK_API_KEY", api_key, quote_mode="always")
    except OSError as exc:
        logger.warning("Could not save local DeepSeek API configuration: %s", exc)
        raise HTTPException(status_code=500, detail="无法写入本机配置文件，请检查项目文件夹权限。") from exc
    if not saved:
        raise HTTPException(status_code=500, detail="无法写入本机配置文件，请检查项目文件夹权限。")
    os.environ["DEEPSEEK_API_KEY"] = api_key
    return {"api_configured": True}


@app.post("/api/runs")
async def create_analysis_run(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    analysis_module: str = Form("overview"),
) -> dict[str, Any]:
    if not api_key_configured():
        raise HTTPException(
            status_code=503,
            detail="尚未配置 DeepSeek API Key。请先在页面顶部打开配置窗口。",
        )
    if analysis_module not in {"overview", "business", "profit", "complete"}:
        raise HTTPException(status_code=400, detail="未知的分析模块，请重新选择。")
    file_name = Path(file.filename or "").name.strip()
    if not file_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="请上传 PDF 文件。")

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 25 MB，请先压缩 PDF 后重试。")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="上传内容不是有效的 PDF 文件。")

    run_id = uuid.uuid4().hex
    pdf_path = UPLOAD_DIR / f"{run_id}.pdf"
    pdf_path.write_bytes(content)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    create_run(
        {
            "id": run_id,
            "file_name": file_name[:240],
            "file_path": str(pdf_path),
            "analysis_module": analysis_module,
            "sha256": hashlib.sha256(content).hexdigest(),
            "status": "queued",
            "stage": "已接收文件",
            "created_at": now,
            "updated_at": now,
        }
    )
    background_tasks.add_task(_process_run, run_id)
    run = get_run(run_id)
    return _public_run(run or {}, include_result=False)


@app.get("/api/runs")
def recent_runs() -> dict[str, Any]:
    return {"runs": [_public_run(run, include_result=False) for run in list_runs()]}


@app.get("/api/runs/{run_id}")
def read_run(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="没有找到这次任务。")
    return _public_run(run)


@app.get("/api/runs/{run_id}/pages/{page_number}")
def read_run_page(run_id: str, page_number: int) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="没有找到这次任务。")
    if page_number < 1 or page_number > run["page_count"]:
        raise HTTPException(status_code=404, detail="页码超出 PDF 范围。")
    text = get_run_page(run_id, page_number)
    if text is None:
        raise HTTPException(status_code=404, detail="没有找到该页的可提取文字。")
    return {"page": page_number, "text": text}


def _public_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        key: turn.get(key)
        for key in ("id", "question", "answer", "status", "error", "created_at", "updated_at")
    }


@app.get("/api/runs/{run_id}/conversation")
def read_conversation(run_id: str) -> dict[str, Any]:
    if not get_run(run_id):
        raise HTTPException(status_code=404, detail="没有找到这次任务。")
    return {"turns": [_public_turn(turn) for turn in list_conversation_turns(run_id)]}


@app.post("/api/runs/{run_id}/conversation")
def ask_about_run(
    run_id: str,
    payload: ConversationQuestion,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="没有找到这次任务。")
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请先输入要追问的问题。")
    if run["status"] != "completed" or not run.get("result"):
        raise HTTPException(status_code=409, detail="请等首次分析完成后再追问。")
    if not api_key_configured():
        raise HTTPException(status_code=503, detail="尚未配置 DeepSeek API Key。")
    if has_pending_conversation_turn(run_id):
        raise HTTPException(status_code=409, detail="上一条追问还在处理中，请稍候。")

    turn_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    create_conversation_turn({"id": turn_id, "run_id": run_id, "question": question, "created_at": now})
    background_tasks.add_task(_process_conversation_turn, run_id, turn_id)
    turn = get_conversation_turn(turn_id)
    return _public_turn(turn or {})


@app.get("/api/runs/{run_id}/file")
def read_original_pdf(run_id: str) -> FileResponse:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="没有找到这次任务。")
    pdf_path = Path(run["file_path"]).resolve()
    if pdf_path.parent != UPLOAD_DIR.resolve() or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="本机保存的 PDF 已不存在。")
    from urllib.parse import quote

    encoded_name = quote(run["file_name"], safe="")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


app.include_router(review_router)


dist_dir = ROOT / "frontend" / "dist"
if dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")
else:
    @app.get("/", include_in_schema=False)
    def frontend_not_built() -> HTMLResponse:
        return HTMLResponse(
            "<main style='font:16px system-ui;max-width:720px;margin:12vh auto;padding:24px'>"
            "<h1>阶段一前端尚未构建</h1><p>请按照项目 README 运行启动脚本，完成前端安装后再打开此页。</p></main>",
            status_code=503,
        )
