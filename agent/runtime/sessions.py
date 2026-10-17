"""运行会话模型：每个 run 必须绑定一个服务端会话。"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from .events import timestamp_now


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    created_at: str


class SessionRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}

    @staticmethod
    def _validate(session_id: str) -> str:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id 不能为空")
        return session_id.strip()

    def ensure(self, session_id: str) -> Session:
        session_id = self._validate(session_id)
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = Session(id=session_id, created_at=timestamp_now())
                self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Session:
        session_id = self._validate(session_id)
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise ValueError("session 不存在") from exc

    def exists(self, session_id: str) -> bool:
        session_id = self._validate(session_id)
        with self._lock:
            return session_id in self._sessions
