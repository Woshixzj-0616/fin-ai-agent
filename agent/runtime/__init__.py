"""金融投研智能体运行时公共接口。"""
from .event_bus import EventBus, EventSubscription
from .events import RunEvent, RunStatus, phase_for_event
from .run_manager import (
    LegacyRunAdapter,
    RunCancelled,
    RunContext,
    RunManager,
    RunManagerError,
    RunNotVisible,
    RunStateError,
)
from .sessions import Session, SessionRegistry

__all__ = [
    "EventBus",
    "EventSubscription",
    "LegacyRunAdapter",
    "RunCancelled",
    "RunContext",
    "RunEvent",
    "RunManager",
    "RunManagerError",
    "RunNotVisible",
    "RunStateError",
    "RunStatus",
    "Session",
    "SessionRegistry",
    "phase_for_event",
]
