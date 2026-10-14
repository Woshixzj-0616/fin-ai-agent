"""B2 新 API 的应用服务：运行时、SSE、会话和运行级追问。"""
from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from qa import answer as qa_answer
    from runtime import RunContext, RunManager, RunNotVisible
    from runtime.events import TERMINAL_STATUSES
    from runtime.redaction import SECRET_PATTERNS, redact_result, redact_value
    from tools import search_run_text
except ModuleNotFoundError:
    from agent.qa import answer as qa_answer
    from agent.runtime import RunContext, RunManager, RunNotVisible
    from agent.runtime.events import TERMINAL_STATUSES
    from agent.runtime.redaction import SECRET_PATTERNS, redact_result, redact_value
    from agent.tools import search_run_text

from .runtime_bridge import RuntimeRunBridge
from .security import validate_pdf_upload
from .artifacts import build_explanation, build_reproduction_bundle, capture_sources


PAGE_IMAGE_RE = re.compile(r"^\d{6}_\d{4}_[0-9a-f]{8}_p\d+\.png$")
IMAGE_SCALE = 1.6
MAX_COMPARISON_PDFS = 4
MAX_RUN_UPLOAD_BYTES = 80 * 1024 * 1024


class ApiError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            }
        }


class ApiApplication:
    def __init__(
        self,
        root: Path,
        *,
        manager: RunManager | None = None,
        workflow_runner: Callable[..., dict] | None = None,
        heartbeat_seconds: float = 15.0,
    ):
        self.root = Path(root).resolve()
        self.manager = manager or RunManager(self.root)
        self.workflow_runner = workflow_runner
        self.heartbeat_seconds = max(0.01, float(heartbeat_seconds))

    def create_run(
        self,
        session_id: str,
        command: str,
        parameters: dict[str, Any],
        pdf: bytes,
        comparison_pdfs: list[bytes] | None = None,
    ) -> dict:
        if command not in {"financial", "announcement"}:
            raise ApiError(400, "UNSUPPORTED_COMMAND", "不支持的任务类型")
        parameters = dict(parameters)
        draft = parameters.get("draft")
        if isinstance(draft, str):
            try:
                from backend_runtime import load_config
            except ModuleNotFoundError:
                from agent.backend_runtime import load_config
            if len(draft) > load_config()["document"]["max_characters"]:
                raise ApiError(400, "DRAFT_TOO_LARGE", "草稿超过当前配置的整篇字符上限")
            configured_key = os.environ.get("LLM_API_KEY", "")
            if ((configured_key and configured_key in draft)
                    or any(pattern.search(draft) for _, pattern in SECRET_PATTERNS)):
                raise ApiError(400, "SENSITIVE_INPUT", "草稿疑似包含凭证，请移除后重新提交")
        if command == "announcement":
            try:
                from announcements import SCHEMAS
            except ModuleNotFoundError:
                from agent.announcements import SCHEMAS
            event_type = str(parameters.get("event_type") or "auto").strip()
            if event_type not in {"auto", *SCHEMAS}:
                raise ApiError(400, "INVALID_EVENT_TYPE", "请选择自动识别、股份质押、中标或股权变动")
            parameters["event_type"] = event_type
        comparison_pdfs = list(comparison_pdfs or [])
        if len(comparison_pdfs) > MAX_COMPARISON_PDFS:
            raise ApiError(400, "INVALID_UPLOAD", "比较材料最多 4 份")
        uploads = [pdf, *comparison_pdfs]
        total_bytes = sum(len(blob) for blob in uploads if isinstance(blob, bytes))
        if total_bytes > MAX_RUN_UPLOAD_BYTES:
            raise ApiError(400, "INVALID_UPLOAD", "主 PDF 与比较 PDF 合计不能超过 80MiB")
        validate_pdf_upload(pdf)
        for blob in comparison_pdfs:
            validate_pdf_upload(blob)
        context = self.manager.create_run(session_id, command, dict(parameters))
        self.manager.start(
            context.id,
            self._execute,
            command,
            dict(parameters),
            bytes(pdf),
            [bytes(blob) for blob in comparison_pdfs],
        )
        return {
            "run_id": context.id,
            "status": context.status.value,
            "events_url": f"/api/runs/{context.id}/events",
            "poll_url": f"/api/runs/{context.id}",
        }

    def _execute(
        self,
        context: RunContext,
        command: str,
        parameters: dict[str, Any],
        pdf: bytes,
        comparison_pdfs: list[bytes],
    ) -> dict:
        bridge = RuntimeRunBridge(context)
        if self.workflow_runner is not None:
            return self.workflow_runner(
                context,
                command,
                parameters,
                pdf,
                comparison_pdfs,
            )
        capture_sources(context)
        return self._default_workflow(
            context,
            bridge,
            command,
            parameters,
            pdf,
            comparison_pdfs,
        )

    @staticmethod
    def _default_workflow(
        context: RunContext,
        bridge: RuntimeRunBridge,
        command: str,
        parameters: dict[str, Any],
        pdf: bytes,
        comparison_pdfs: list[bytes],
    ) -> dict:
        try:
            from webui import run_announcement_pipeline, run_pipeline
        except ModuleNotFoundError:
            from agent.webui import run_announcement_pipeline, run_pipeline

        if command == "announcement":
            return run_announcement_pipeline(
                pdf,
                str(parameters.get("event_type") or "auto"),
                root=context.root,
                ocr=bool(parameters.get("ocr")),
                run=bridge,
                publish_latest=False,
            )
        return run_pipeline(
            pdf,
            str(parameters.get("code") or ""),
            str(parameters.get("name") or ""),
            int(parameters.get("year") or 0),
            draft=parameters.get("draft"),
            root=context.root,
            comparison_blobs=comparison_pdfs,
            run=bridge,
            publish_latest=False,
            store_page_texts=True,
        )

    def get_run(self, session_id: str, run_id: str, *, include_result: bool = False) -> dict:
        context = self._visible_run(session_id, run_id)
        return self._serialize_run(context, include_result=include_result)

    def list_runs(self, session_id: str) -> dict:
        return {
            "runs": [
                self._serialize_run(context, include_result=False)
                for context in self.manager.list_runs(session_id)
            ]
        }

    def cancel_run(self, session_id: str, run_id: str) -> dict:
        context = self._visible_run(session_id, run_id)
        context.cancel("user")
        return {"run_id": context.id, "status": context.status.value}

    def get_evidence_image(self, session_id: str, run_id: str, name: str) -> Path:
        context = self._visible_run(session_id, run_id)
        if not PAGE_IMAGE_RE.fullmatch(str(name or "")):
            raise ApiError(404, "IMAGE_NOT_FOUND", "页图不存在或无权访问")
        try:
            path = context.output(str(name)).resolve()
            path.relative_to(context.folder.resolve())
        except (OSError, ValueError) as exc:
            raise ApiError(404, "IMAGE_NOT_FOUND", "页图不存在或无权访问") from exc
        if not path.is_file():
            raise ApiError(404, "IMAGE_NOT_FOUND", "页图不存在或无权访问")
        return path

    def export_run(self, session_id: str, run_id: str, kind: str) -> tuple[str, str, bytes]:
        context = self._completed_run(session_id, run_id)
        result = context.snapshot()["result"]
        if not isinstance(result, dict):
            raise ApiError(409, "RUN_RESULT_NOT_READY", "本次运行没有完整结果可导出")
        if kind == "result.json":
            blob = (json.dumps(redact_result(result), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            return f"{run_id}_result.json", "application/json; charset=utf-8", blob
        if kind == "report.md":
            if not result.get("report_md"):
                raise ApiError(409, "RUN_RESULT_NOT_READY", "本次运行没有报告可导出")
            return f"{run_id}_report.md", "text/markdown; charset=utf-8", result["report_md"].encode("utf-8")
        if kind == "reproduction.zip":
            try:
                blob = build_reproduction_bundle(context, self.manager.bus.replay(context.id))
            except ValueError as exc:
                raise ApiError(413, "EXPORT_TOO_LARGE", str(exc)) from exc
            return f"{run_id}_reproduction.zip", "application/zip", blob
        if kind == "explanation.json":
            blob = (json.dumps(build_explanation(context, self.manager.bus.replay(context.id)), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            return f"{run_id}_explanation.json", "application/json; charset=utf-8", blob
        raise ApiError(404, "EXPORT_NOT_FOUND", "导出文件不存在")

    def subscribe_events(self, session_id: str, run_id: str, *, after_seq: int = 0):
        context = self._visible_run(session_id, run_id)
        events = self.manager.bus.replay(context.id)
        latest = events[-1].seq if events else 0
        if not isinstance(after_seq, int) or after_seq < 0:
            raise ApiError(400, "INVALID_LAST_EVENT_ID", "Last-Event-ID 必须为非负整数")
        if after_seq > latest:
            raise ApiError(
                409,
                "EVENT_REPLAY_GAP",
                "断点位置超过服务端已有事件，请重新加载运行",
            )
        return self.manager.bus.subscribe(context.id, after_seq=after_seq)

    def ask(self, session_id: str, run_id: str, question: str) -> dict:
        context = self._completed_run(session_id, run_id)
        question = str(question or "").strip()
        if not question:
            raise ApiError(400, "INVALID_QUESTION", "缺少 question")
        facts = self._load_run_json(context, "webui_evidence.json")
        client = None
        try:
            from llm_check import LLMClient
        except ModuleNotFoundError:
            from agent.llm_check import LLMClient
        try:
            client = LLMClient.from_environment()
        except Exception:  # noqa: BLE001 - 未配置模型时按无模型模式降级
            client = None
        return qa_answer(
            question,
            facts,
            client=client,
            run=context,
            use_llm=client is not None,
        )

    def search(self, session_id: str, run_id: str, query: str, *, top_k: int = 8) -> dict:
        context = self._completed_run(session_id, run_id)
        query = str(query or "").strip()
        if not query or len(query) > 40:
            raise ApiError(400, "INVALID_QUERY", "query 需为 1–40 字")
        try:
            top_k = int(top_k)
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "INVALID_TOP_K", "top_k 必须为整数") from exc
        if not 1 <= top_k <= 20:
            raise ApiError(400, "INVALID_TOP_K", "top_k 需为 1–20")
        facts = self._load_run_json(context, "webui_evidence.json")
        docs = self._load_run_json(context, "webui_document_texts.json")
        if not docs:
            return {
                "status": "empty",
                "hits": [],
                "hint": "本次运行没有可检索的页文本",
            }
        result = search_run_text(facts, docs, query=query, top_k=top_k)
        result["note"] = result.get("note") or "检索命中为相关段落，不能单独作为数值裁决依据"
        return result

    def close(self) -> None:
        self.manager.close()

    def _visible_run(self, session_id: str, run_id: str) -> RunContext:
        try:
            return self.manager.get_run(run_id, session_id=session_id)
        except RunNotVisible as exc:
            raise ApiError(404, "RUN_NOT_FOUND", "运行不存在或无权访问") from exc

    def _completed_run(self, session_id: str, run_id: str) -> RunContext:
        context = self._visible_run(session_id, run_id)
        if context.status not in TERMINAL_STATUSES:
            raise ApiError(409, "RUN_NOT_READY", "运行尚未结束")
        return context

    def _load_run_json(self, context: RunContext, name: str) -> Any:
        path = context.output(name)
        if not path.is_file():
            raise ApiError(409, "RUN_RESULT_NOT_READY", "本次运行没有可供处理的结构化结果")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApiError(500, "RUN_RESULT_INVALID", "运行结果无法读取") from exc

    def _serialize_run(self, context: RunContext, *, include_result: bool) -> dict:
        snapshot = context.snapshot(result_transform=None if include_result else self._result_summary)
        events = self.manager.bus.replay(context.id)
        payload = snapshot.get("result")
        summary = self._result_summary(payload) if include_result else payload
        output = {
            "run_id": context.id,
            "command": context.command,
            "status": snapshot["status"],
            "current_phase": events[-1].phase if events else "run",
            "created_at": snapshot["created_at"],
            "started_at": snapshot["started_at"],
            "finished_at": snapshot["finished_at"],
            "cancel_reason": snapshot["cancel_reason"],
            "error": snapshot["error"],
            "result_summary": summary,
            "events_url": f"/api/runs/{context.id}/events",
            "input_summary": self._input_summary(snapshot.get("parameters")),
        }
        if include_result:
            output["result"] = redact_result(self._enrich_result(context, payload))
            if isinstance(payload, dict):
                output["result"]["exports"] = {
                    kind: f"/api/runs/{context.id}/exports/{kind}"
                    for kind in ("result.json", "report.md", "reproduction.zip", "explanation.json")
                    if kind != "report.md" or payload.get("report_md")
                }
                output["result"]["explanation"] = build_explanation(context, events)
                provenance = context.output("provenance.json")
                if provenance.is_file():
                    output["result"]["provenance"] = self._load_run_json(context, "provenance.json")
        return output

    @staticmethod
    def _input_summary(parameters: Any) -> dict:
        source = parameters if isinstance(parameters, dict) else {}
        summary = {
            key: source[key]
            for key in ("code", "name", "year", "event_type", "ocr")
            if source.get(key) is not None
        }
        draft = source.get("draft")
        if isinstance(draft, str) and draft.strip():
            summary["draft_present"] = True
            summary["draft_length"] = len(draft)
        elif draft:
            summary["draft_present"] = True
        return summary

    @classmethod
    def _enrich_result(cls, context: RunContext, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return copy.deepcopy(payload)
        output = copy.deepcopy(payload)
        dimensions_cache: dict[str, tuple[float, float] | None] = {}
        for key in ("evidence", "rows"):
            records = output.get(key)
            if not isinstance(records, list):
                continue
            output[key] = [
                cls._enrich_page_record(context, record, dimensions_cache=dimensions_cache)
                if isinstance(record, dict) else record
                for record in records
            ]
        return output

    @classmethod
    def _enrich_page_record(cls, context: RunContext, record: dict, *,
                            dimensions_cache: dict | None = None) -> dict:
        output = dict(record)
        name = str(record.get("page_image") or "")
        if not PAGE_IMAGE_RE.fullmatch(name):
            return output
        image_path = context.output(name)
        if not image_path.is_file():
            return output

        if dimensions_cache is not None and name in dimensions_cache:
            dimensions = dimensions_cache[name]
        else:
            dimensions = cls._page_dimensions(context, record, image_path)
            if dimensions_cache is not None:
                dimensions_cache[name] = dimensions
        output["page_image_url"] = f"/api/runs/{context.id}/images/{name}"
        output["image_scale"] = IMAGE_SCALE
        if dimensions is not None:
            output["page_width"], output["page_height"] = dimensions
        return output

    @staticmethod
    def _page_dimensions(
        context: RunContext,
        record: dict,
        image_path: Path,
    ) -> tuple[float, float] | None:
        page_number = record.get("page")
        try:
            page_number = int(page_number)
        except (TypeError, ValueError):
            page_number = 0

        source = record.get("source_file")
        candidates: list[Path] = []
        if isinstance(source, str) and source:
            raw = Path(source)
            candidates.append(raw if raw.is_absolute() else context.root / raw)
        candidates.append(context.folder / "source.pdf")
        try:
            import pymupdf
        except ImportError:
            pymupdf = None

        if page_number > 0 and pymupdf is not None:
            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                    resolved.relative_to(context.root.resolve())
                    if not resolved.is_file():
                        continue
                    with pymupdf.open(resolved) as document:
                        if page_number <= document.page_count:
                            rect = document[page_number - 1].rect
                            return float(rect.width), float(rect.height)
                except (OSError, ValueError, RuntimeError):
                    continue

        if pymupdf is not None:
            try:
                pixmap = pymupdf.Pixmap(image_path)
                try:
                    return (
                        float(pixmap.width) / IMAGE_SCALE,
                        float(pixmap.height) / IMAGE_SCALE,
                    )
                finally:
                    pixmap = None
            except (OSError, RuntimeError, ValueError):
                pass
        return None

    @staticmethod
    def _result_summary(payload: Any) -> dict:
        if not isinstance(payload, dict):
            return {"available": payload is not None}
        counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        failures = payload.get("failures") if isinstance(payload.get("failures"), list) else []
        return {
            "available": True,
            "ok": payload.get("ok"),
            "counts": dict(counts),
            "evidence_count": len(evidence),
            "failure_count": len(failures),
        }
