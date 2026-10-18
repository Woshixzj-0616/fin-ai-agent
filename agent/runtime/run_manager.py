"""运行上下文、会话隔离、工作线程、取消和超时。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime
from pathlib import Path
from typing import Any

from .event_bus import EventBus
from .events import (
    TERMINAL_STATUSES,
    TZ,
    RunEvent,
    RunStatus,
    phase_for_event,
    timestamp_now,
)
from .redaction import redact_result, redact_text, redact_value
from .sessions import SessionRegistry


class RunManagerError(RuntimeError):
    pass


class RunNotVisible(RunManagerError):
    pass


class RunStateError(RunManagerError):
    pass


class RunCancelled(RunStateError):
    pass


MAX_RESTORED_STATE_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024


def _atomic_write(path: Path, text: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _event_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return redact_value(value)
    except (TypeError, ValueError):
        return {
            "type": type(value).__name__,
            "preview": redact_text(str(value), limit=200),
        }


class RunContext:
    """一次运行的隔离上下文；兼容旧 Run 的常用方法与事件属性。"""

    def __init__(
        self,
        *,
        bus: EventBus,
        root: Path,
        run_id: str,
        session_id: str,
        command: str,
        parameters: dict[str, Any] | None = None,
        restored: dict[str, Any] | None = None,
    ):
        self._bus = bus
        self.root = Path(root).resolve()
        self.id = run_id
        self.session_id = session_id
        self.command = command
        self.parameters = dict(parameters or {})
        self.folder = bus.storage_root / run_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.created_at = timestamp_now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.cancelled_event = threading.Event()
        self._status = RunStatus.QUEUED
        self._cancel_reason: str | None = None
        self._result: Any = None
        self._prepared_finish: tuple[RunStatus, dict[str, Any]] | None = None
        self._error: str | None = None
        self._error_type: str | None = None
        self._lock = threading.RLock()
        if restored is None:
            self._write_state()
        else:
            self._restore_state(restored)

    def _restore_state(self, restored: dict[str, Any]) -> None:
        """从 run.json/result.json 恢复上下文；不重写磁盘状态，也不新增事件。"""
        self.created_at = str(restored.get("created_at") or self.created_at)
        self.started_at = restored.get("started_at")
        self.finished_at = restored.get("finished_at")
        self._cancel_reason = str(restored.get("cancel_reason") or "") or None
        self._error = str(restored.get("error") or "") or None
        self._error_type = str(restored.get("error_type") or "") or None
        try:
            self._status = RunStatus(str(restored.get("status") or RunStatus.FAILED.value))
        except ValueError:
            self._status = RunStatus.FAILED
        if isinstance(restored.get("parameters"), dict):
            self.parameters = dict(restored["parameters"])
        result_path = self.folder / "result.json"
        if result_path.is_file() and result_path.stat().st_size <= MAX_RESULT_BYTES:
            try:
                self._result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._result = None

    @property
    def status(self) -> RunStatus:
        with self._lock:
            return self._status

    @property
    def events(self) -> list[str]:
        return [event.to_json() for event in self._bus.replay(self.id)]

    @property
    def events_path(self) -> Path:
        return self._bus.events_path(self.id)

    def output(self, name: str) -> Path:
        if not name or name in {".", ".."} or any(char in name for char in "/\\:"):
            raise ValueError("输出文件必须直接放在运行目录，不能嵌套")
        return self.folder / name

    def event(
        self,
        event_type: str,
        *,
        phase: str | None = None,
        actor: str = "runtime",
        title: str = "",
        summary: str = "",
        status: str = "completed",
        duration_ms: float | None = None,
        evidence_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        **data: Any,
    ) -> RunEvent:
        merged = dict(payload or {})
        merged.update(data)
        return self._bus.publish(
            run_id=self.id,
            event_type=event_type,
            phase=phase or phase_for_event(event_type),
            actor=actor,
            title=title or event_type,
            summary=summary,
            status=status,
            duration_ms=duration_ms,
            evidence_ids=evidence_ids,
            payload=merged,
        )

    def read(self, path: Path) -> bytes:
        blob = Path(path).read_bytes()
        self.event(
            "file_read",
            actor="program",
            title="读取文件",
            summary="记录文件路径、SHA-256 与字节数",
            payload={
                "path": str(Path(path).resolve()),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "size_bytes": len(blob),
            },
        )
        return blob

    def start(self) -> RunContext:
        with self._lock:
            if self._status != RunStatus.QUEUED:
                raise RunStateError(f"运行状态为 {self._status.value}，不能开始")
            self._status = RunStatus.RUNNING
            self.started_at = timestamp_now()
            self._write_state()
        self.event(
            "run_started",
            phase="run",
            actor="runtime",
            title="开始运行",
            summary="运行已进入执行状态",
            status=RunStatus.RUNNING.value,
        )
        return self

    def prepare_finish(self, status: RunStatus | str, **summary: Any) -> bool:
        """流水线登记期望终态，最终结果落盘前不对外发布结束事件。"""
        terminal = RunStatus.coerce(status)
        if terminal not in TERMINAL_STATUSES:
            raise RunStateError("prepare_finish 需要终态")
        with self._lock:
            if self._status in TERMINAL_STATUSES:
                return False
            self._prepared_finish = (terminal, dict(summary))
        return True

    def finish(self, status: RunStatus | str, **summary: Any) -> bool:
        terminal = RunStatus.coerce(status)
        if terminal not in TERMINAL_STATUSES:
            raise RunStateError(f"finish 需要终态，收到 {terminal.value}")
        with self._lock:
            if self._status in TERMINAL_STATUSES:
                return False
            previous_result = self._result
            if "result" in summary:
                self._result = summary["result"]
            try:
                self._write_result()
            except Exception:
                self._result = previous_result
                raise
            self._status = terminal
            self.finished_at = timestamp_now()
            self._write_state()
        self.event(
            "run_finished",
            phase="run",
            actor="runtime",
            title="运行结束",
            summary=f"运行终态：{terminal.value}",
            status=terminal.value,
            payload={key: _event_safe(value) for key, value in summary.items()},
        )
        return True

    def cancel(self, reason: str = "user") -> bool:
        with self._lock:
            if self._status in TERMINAL_STATUSES:
                return False
            self._status = RunStatus.CANCELLED
            self._cancel_reason = str(reason or "user")
            self.finished_at = timestamp_now()
            self._write_state()
        self.event(
            "run_cancelled",
            phase="run",
            actor="runtime",
            title="运行已取消",
            summary=f"取消原因：{self._cancel_reason}",
            status=RunStatus.CANCELLED.value,
            payload={"reason": self._cancel_reason},
        )
        self.cancelled_event.set()
        return True

    def raise_if_cancelled(self) -> None:
        if self.cancelled_event.is_set():
            raise RunCancelled(self._cancel_reason or "cancelled")

    def _record_result(self, result: Any) -> None:
        with self._lock:
            self._result = result
            self._write_state()
            self._write_result()

    def _write_result(self) -> None:
        """完整结果落盘；过大或不可序列化须明确失败，不能伪装交付成功。"""
        if self._result is None:
            return
        try:
            encoded = json.dumps(redact_result(self._result), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise RunStateError("运行结果无法序列化为 JSON") from exc
        if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
            raise RunStateError("运行结果超过允许大小，未生成完整交付产物")
        _atomic_write(self.folder / "result.json", encoded + "\n")

    def _record_error(self, exc: Exception) -> None:
        with self._lock:
            self._error_type = type(exc).__name__
            self._error = str(exc)
            self._write_state()

    def snapshot(self, *, result_transform: Callable[[Any], Any] | None = None) -> dict[str, Any]:
        with self._lock:
            result = result_transform(self._result) if result_transform else self._result
            return {
                "run_id": self.id,
                "session_id": self.session_id,
                "command": self.command,
                "parameters": redact_result(dict(self.parameters)),
                "status": self._status.value,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "cancel_reason": redact_text(self._cancel_reason or ""),
                "result": redact_result(result),
                "error_type": self._error_type,
                "error": redact_text(self._error or ""),
                "events_path": str(self.events_path),
            }

    def _write_state(self) -> None:
        payload = {
            "schema_version": 1,
            "run_id": self.id,
            "session_id": self.session_id,
            "command": self.command,
            "parameters": redact_result(dict(self.parameters)),
            "status": self._status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_reason": redact_text(self._cancel_reason or ""),
            "error_type": self._error_type,
            "error": redact_text(self._error or ""),
        }

        temporary = self.folder / "run.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.folder / "run.json")


class RunManager:
    def __init__(self, root: Path, *, max_workers: int = 4, restore: bool = True,
                 shutdown_timeout: float = 2.0):
        if max_workers < 1:
            raise ValueError("max_workers 必须大于 0")
        if shutdown_timeout < 0:
            raise ValueError("shutdown_timeout 不能为负")
        self.shutdown_timeout = float(shutdown_timeout)
        self.root = Path(root).resolve()
        self.storage_root = self.root / "results" / "runs"
        self.bus = EventBus(self.storage_root)
        self.sessions = SessionRegistry()
        self._runs: dict[str, RunContext] = {}
        self._futures: dict[str, Future] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fin-agent-run")
        self._closed = False
        self.restored: list[str] = []
        if restore:
            self.restore()

    def restore(self) -> list[str]:
        """服务重启后从磁盘恢复历史运行；未结束的运行明确标记为未完成。

        会话隔离不变：恢复的运行仍绑定 run.json 里的 session_id，只能被同一会话看到。
        """
        if not self.storage_root.is_dir():
            return []
        for folder in sorted(self.storage_root.iterdir()):
            state_path = folder / "run.json"
            if not folder.is_dir() or not state_path.is_file():
                continue
            try:
                if state_path.stat().st_size > MAX_RESTORED_STATE_BYTES:
                    continue
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            run_id = state.get("run_id") or folder.name
            session_id = state.get("session_id")
            if run_id != folder.name or not isinstance(session_id, str) or not session_id.strip():
                continue
            with self._lock:
                if run_id in self._runs:
                    continue
            try:
                status = RunStatus(str(state.get("status") or RunStatus.FAILED.value))
            except ValueError:
                status = RunStatus.FAILED
            context = RunContext(bus=self.bus, root=self.root, run_id=run_id,
                                 session_id=session_id,
                                 command=str(state.get("command") or "restored"),
                                 parameters=state.get("parameters") if isinstance(state.get("parameters"), dict) else {},
                                 restored={**state, "status": status.value})
            if status not in TERMINAL_STATUSES:
                # 中断运行不伪装成功：写终态标记并留事件，历史仍可查。
                context._status = RunStatus.FAILED
                context._error_type = "InterruptedByRestart"
                context._error = "服务重启时该运行尚未结束，已标记为未完成"
                context.finished_at = context.finished_at or timestamp_now()
                context._write_state()
                context.event("run_interrupted", phase="run", actor="runtime",
                              title="运行被重启中断", status="failed",
                              summary=context._error, payload={"previous_status": state.get("status")})
            self.sessions.ensure(session_id)
            with self._lock:
                self._runs[run_id] = context
            self.restored.append(run_id)
        return self.restored

    def create_run(
        self,
        session_id: str,
        command: str,
        parameters: dict[str, Any] | None = None,
    ) -> RunContext:
        if self._closed:
            raise RunStateError("RunManager 已关闭")
        self.sessions.ensure(session_id)
        run_id = datetime.now(TZ).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        context = RunContext(
            bus=self.bus,
            root=self.root,
            run_id=run_id,
            session_id=session_id,
            command=command,
            parameters=parameters,
        )
        with self._lock:
            self._runs[run_id] = context
        context.event(
            "run_created",
            phase="run",
            actor="runtime",
            title="创建运行",
            summary="运行已创建并等待执行",
            status=RunStatus.QUEUED.value,
            payload={"command": command},
        )
        return context

    def adopt_legacy_run(self, legacy_run, session_id: str) -> RunContext:
        self.sessions.ensure(session_id)
        with self._lock:
            existing = self._runs.get(legacy_run.id)
            if existing is not None:
                return existing
            context = RunContext(
                bus=self.bus,
                root=self.root,
                run_id=legacy_run.id,
                session_id=session_id,
                command=getattr(legacy_run, "command", "legacy"),
                parameters={},
            )
            context._status = RunStatus.RUNNING
            context.started_at = timestamp_now()
            context._write_state()
            self._runs[context.id] = context
        context.event(
            "run_adopted",
            phase="run",
            actor="runtime",
            title="接入旧运行",
            summary="旧 Run 已接入事件镜像",
            status=RunStatus.RUNNING.value,
        )
        return context

    def start(
        self,
        run_id: str,
        target: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> RunContext:
        context = self._require_run(run_id)
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        context.start()
        future = self._executor.submit(self._execute, context, target, args, kwargs)
        with self._lock:
            self._futures[run_id] = future
        if timeout is not None:
            timer = threading.Timer(timeout, self._on_timeout, args=(context,))
            timer.daemon = True
            with self._lock:
                self._timers[run_id] = timer
            timer.start()
        return context

    def _execute(
        self,
        context: RunContext,
        target: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        try:
            result = target(context, *args, **kwargs)
            with context._lock:
                if not context.cancelled_event.is_set():
                    if context.status in TERMINAL_STATUSES:
                        # 兼容直接管理终态的旧调用者；新版桥接流水线使用 prepare_finish。
                        context._record_result(result)
                    else:
                        terminal, summary = context._prepared_finish or (RunStatus.SUCCEEDED, {})
                        context.finish(terminal, **{**summary, "result": result})
        except RunCancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - 工作线程必须记录所有用户代码异常
            if not context.cancelled_event.is_set():
                context._record_error(exc)
                context.finish(
                    RunStatus.FAILED,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
        finally:
            with self._lock:
                timer = self._timers.pop(context.id, None)
            if timer is not None:
                timer.cancel()

    def _on_timeout(self, context: RunContext) -> None:
        context.cancel("timeout")

    def wait(self, run_id: str, timeout: float | None = None) -> RunContext:
        context = self._require_run(run_id)
        with self._lock:
            future = self._futures.get(run_id)
        if future is None:
            return context
        try:
            future.result(timeout=timeout)
        except FutureTimeout as exc:
            raise TimeoutError(f"等待运行 {run_id} 超时") from exc
        return context

    def cancel(self, run_id: str, *, session_id: str | None = None, reason: str = "user") -> bool:
        context = self.get_run(run_id, session_id=session_id)
        return context.cancel(reason)

    def get_run(self, run_id: str, *, session_id: str | None = None) -> RunContext:
        with self._lock:
            context = self._runs.get(run_id)
        if context is None:
            raise RunNotVisible("运行不存在或无权访问")
        if session_id is not None and context.session_id != session_id:
            raise RunNotVisible("运行不存在或无权访问")
        return context

    def list_runs(self, session_id: str) -> list[RunContext]:
        self.sessions.ensure(session_id)
        with self._lock:
            return [run for run in self._runs.values() if run.session_id == session_id]

    def _require_run(self, run_id: str) -> RunContext:
        return self.get_run(run_id)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            timers = list(self._timers.values())
            self._timers.clear()
            futures = list(self._futures.values())
        for timer in timers:
            timer.cancel()
        # 有限等待在途运行写完 run.json/result.json，避免调用方随后清理目录时踩到写文件。
        for future in futures:
            if future.done():
                continue
            try:
                future.result(timeout=self.shutdown_timeout)
            except FutureTimeout:
                break
            except BaseException:  # 取消或运行失败都不该阻塞关闭
                continue
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.bus.close()


class LegacyRunAdapter:
    """把旧 Run.event() 镜像到新事件总线；不改变旧运行目录和历史格式。"""

    def __init__(self, legacy_run, manager: RunManager, *, session_id: str):
        self.legacy_run = legacy_run
        self.manager = manager
        self.context = manager.adopt_legacy_run(legacy_run, session_id)
        self.attached = True
        self.legacy_run.set_event_sink(self._mirror)

    def _mirror(self, entry: dict[str, Any]) -> None:
        if not self.attached:
            return
        event_type = str(entry.get("event") or "legacy_event")
        payload = {
            key: value
            for key, value in entry.items()
            if key not in {"time", "run_id", "event"}
        }
        status = str(payload.pop("status", "completed"))
        self.manager.bus.publish(
            run_id=self.legacy_run.id,
            event_type=event_type,
            phase=phase_for_event(event_type),
            actor="legacy",
            title=event_type,
            summary="旧 Run 事件镜像",
            status=status,
            payload=payload,
        )

    def detach(self) -> None:
        if not self.attached:
            return
        self.attached = False
        self.legacy_run.set_event_sink(None)
