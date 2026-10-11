"""线程安全的事件总线：分配序号、持久化、实时订阅和断点重放。"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .events import RunEvent, phase_for_event, timestamp_now


@dataclass(eq=False)
class EventSubscription:
    bus: EventBus
    run_id: str
    queue: queue.Queue[RunEvent] = field(default_factory=lambda: queue.Queue(maxsize=2048))
    closed: bool = False
    dropped: int = 0

    def _deliver(self, event: RunEvent) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            # 慢订阅者不能拖住运行；丢弃最旧事件并记录缺口。
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped += 1
            self.queue.put_nowait(event)

    def get(self, timeout: float | None = None) -> RunEvent:
        return self.queue.get(timeout=timeout)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.bus._unsubscribe(self)


class EventBus:
    """每个 run 独立序号和文件；订阅者只看到指定 run。"""

    def __init__(self, storage_root: Path):
        self.storage_root = Path(storage_root).resolve()
        self._lock = threading.RLock()
        self._events: dict[str, list[RunEvent]] = {}
        self._next_seq: dict[str, int] = {}
        self._subscribers: dict[str, set[EventSubscription]] = {}

    def _event_path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
            raise ValueError("run_id 不能包含路径分隔符")
        return self.storage_root / run_id / "events.jsonl"

    def _load_locked(self, run_id: str) -> None:
        if run_id in self._events:
            return
        path = self._event_path(run_id)
        events: list[RunEvent] = []
        if path.is_file():
            previous = 0
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    event = RunEvent.from_dict(json.loads(line))
                except Exception as exc:
                    raise ValueError(f"{path} 第 {line_number} 行事件无效") from exc
                if event.run_id != run_id:
                    raise ValueError(f"{path} 包含其他 run 的事件")
                if event.seq <= previous:
                    raise ValueError(f"{path} 的事件序号不递增")
                previous = event.seq
                events.append(event)
        self._events[run_id] = events
        self._next_seq[run_id] = events[-1].seq + 1 if events else 1

    def publish(
        self,
        *,
        run_id: str,
        event_type: str,
        phase: str | None = None,
        actor: str = "runtime",
        title: str = "",
        summary: str = "",
        status: str = "completed",
        duration_ms: float | None = None,
        evidence_ids: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        with self._lock:
            self._load_locked(run_id)
            seq = self._next_seq[run_id]
            event = RunEvent.create(
                run_id=run_id,
                seq=seq,
                timestamp=timestamp_now(),
                phase=phase or phase_for_event(event_type),
                event_type=event_type,
                actor=actor,
                title=title or event_type,
                summary=summary,
                status=status,
                duration_ms=duration_ms,
                evidence_ids=evidence_ids,
                payload=payload,
            )
            path = self._event_path(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(event.to_json() + "\n")
            self._events[run_id].append(event)
            self._next_seq[run_id] = seq + 1
            subscribers = list(self._subscribers.get(run_id, ()))
        for subscription in subscribers:
            subscription._deliver(event)
        return event

    def replay(self, run_id: str, after_seq: int = 0) -> list[RunEvent]:
        if not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError("after_seq 必须为非负整数")
        with self._lock:
            self._load_locked(run_id)
            return [event for event in self._events[run_id] if event.seq > after_seq]

    def subscribe(self, run_id: str, after_seq: int = 0) -> EventSubscription:
        if not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError("after_seq 必须为非负整数")
        subscription = EventSubscription(self, run_id)
        with self._lock:
            self._load_locked(run_id)
            for event in self._events[run_id]:
                if event.seq > after_seq:
                    subscription._deliver(event)
            self._subscribers.setdefault(run_id, set()).add(subscription)
        return subscription

    def _unsubscribe(self, subscription: EventSubscription) -> None:
        with self._lock:
            subscribers = self._subscribers.get(subscription.run_id)
            if subscribers is not None:
                subscribers.discard(subscription)
                if not subscribers:
                    self._subscribers.pop(subscription.run_id, None)

    def events_path(self, run_id: str) -> Path:
        return self._event_path(run_id)

    def close(self) -> None:
        with self._lock:
            subscriptions = [
                subscription
                for subscribers in self._subscribers.values()
                for subscription in subscribers
            ]
            self._subscribers.clear()
        for subscription in subscriptions:
            subscription.closed = True
