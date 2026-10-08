"""DeepSeek model calls that preserve the actual request and response locally."""

from __future__ import annotations

import os
import uuid
from typing import Any

from openai import OpenAI

from backend.core.config import load_runtime_config


load_runtime_config()


def model_name() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-flash")


def new_client() -> OpenAI:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        from backend.deepseek_client import ModelCallError

        raise ModelCallError("尚未配置 DeepSeek API Key。请先在页面顶部打开配置窗口。")
    return OpenAI(
        api_key=key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "90")),
        max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "1")),
    )


def recorded_completion(*, recorder: Any = None, client: Any = None, **request: Any) -> Any:
    """Persist request before network I/O and response/error immediately after."""
    client = client or new_client()
    call_id = uuid.uuid4().hex
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
