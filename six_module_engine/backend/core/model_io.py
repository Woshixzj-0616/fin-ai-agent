"""DeepSeek model calls that preserve the actual request and response locally."""

from __future__ import annotations

import os
import uuid
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from openai import OpenAI

from backend.core.config import load_runtime_config


load_runtime_config()

_MODEL_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar("six_module_model_context", default=None)
_REPORT_MODELS: dict[str, dict[str, str]] = {}
_REPORT_MODELS_LOCK = threading.RLock()


def register_report_model(report_id: str, settings: dict[str, str]) -> None:
    """Keep the submitting browser's model settings in memory for this report."""
    with _REPORT_MODELS_LOCK:
        _REPORT_MODELS[report_id] = {
            name: str(settings.get(name, "")).strip()
            for name in ("api_key", "base_url", "model")
            if str(settings.get(name, "")).strip()
        }


@contextmanager
def use_report_model(report_id: str):
    with _REPORT_MODELS_LOCK:
        settings = dict(_REPORT_MODELS.get(report_id, {}))
    token = _MODEL_CONTEXT.set(settings or None)
    try:
        yield
    finally:
        _MODEL_CONTEXT.reset(token)


def _settings() -> dict[str, str]:
    selected = _MODEL_CONTEXT.get() or {}
    return {
        "api_key": selected.get("api_key") or os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "base_url": selected.get("base_url") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": selected.get("model") or os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
    }


def model_name() -> str:
    return _settings()["model"]


def model_settings() -> dict[str, str]:
    return _settings()


def new_client() -> OpenAI:
    settings = _settings()
    key = settings["api_key"]
    if not key:
        from backend.deepseek_client import ModelCallError

        raise ModelCallError("尚未配置 DeepSeek API Key。请先在页面顶部打开配置窗口。")
    return OpenAI(
        api_key=key,
        base_url=settings["base_url"],
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "90")),
        max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "1")),
    )


def recorded_completion(*, recorder: Any = None, client: Any = None, **request: Any) -> Any:
    """Persist request before network I/O and response/error immediately after."""
    client = client or new_client()
    call_id = uuid.uuid4().hex
    context = _MODEL_CONTEXT.get()
    if context and context.get("model"):
        request["model"] = context["model"]
    model = str(request.get("model") or model_name())
    if recorder is not None:
        input_ref = recorder.save_artifact(f"model_{call_id}_request", request)
        recorder.record("model_request_started", call_id=call_id, model=model, input=input_ref)
    try:
        response = client.chat.completions.create(**request)
    except Exception as exc:
        if recorder is not None:
            recorder.record(
                "model_request_failed",
                call_id=call_id,
                model=model,
                error_type=type(exc).__name__,
            )
        raise
    if recorder is not None:
        body = response.model_dump(exclude_none=True) if hasattr(response, "model_dump") else response
        output_ref = recorder.save_artifact(f"model_{call_id}_response", body)
        usage = getattr(response, "usage", None)
        recorder.record(
            "model_request_completed",
            call_id=call_id,
            model=str(getattr(response, "model", None) or model),
            output=output_ref,
            usage=usage.model_dump(exclude_none=True) if hasattr(usage, "model_dump") else None,
        )
    return response
