"""P3 稳定性：统一失败分类和不联网故障矩阵。

失败码只描述可安全展示的故障类别，不携带密钥、认证头或服务端原文。
"""
from __future__ import annotations

import copy
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from backend_runtime import DEFAULTS, ExecutionLimit
from llm_http import LLMHttp, TransportError


@dataclass(frozen=True)
class FailureInfo:
    code: str
    label: str
    user_message: str
    retryable: bool
    http_status: int | None

    def as_dict(self) -> dict:
        return asdict(self)


_LABELS = {
    "auth_failed": "认证失败",
    "invalid_request": "请求参数无效",
    "rate_limited": "接口限流",
    "upstream_unavailable": "上游服务不可用",
    "redirect_rejected": "重定向已拒绝",
    "timeout": "请求超时",
    "network_error": "网络连接异常",
    "response_truncated": "响应不完整",
    "invalid_response": "响应协议无效",
    "model_identity_changed": "模型身份变化",
    "budget_exhausted": "运行预算耗尽",
    "checkpoint_invalid": "检查点无效",
    "secret_blocked": "检测到敏感凭证",
    "cancelled": "任务已取消",
    "tool_failed": "工具执行失败",
    "unexpected_exception": "未预期异常",
    "unknown": "未知失败",
}

_MESSAGES = {
    "auth_failed": "模型认证失败，请检查服务地址、密钥权限和模型名",
    "invalid_request": "模型请求参数或模型名无效，请修正配置后重试",
    "rate_limited": "模型接口限流，有限重试后仍未完成",
    "upstream_unavailable": "模型上游服务暂不可用，有限重试后仍未完成",
    "redirect_rejected": "模型接口发生重定向，密钥未转发",
    "timeout": "模型请求超时或总时限已用尽",
    "network_error": "模型网络连接失败，有限重试后仍未完成",
    "response_truncated": "模型响应中断或截断，本次未判定",
    "invalid_response": "模型响应不符合JSON协议，本次未判定",
    "model_identity_changed": "同一请求或运行中的模型身份发生变化",
    "budget_exhausted": "运行预算已耗尽，保留已完成结果和检查点",
    "checkpoint_invalid": "检查点缺失、被篡改或与当前输入不一致",
    "secret_blocked": "检测到敏感凭证，已拒绝保存或发送",
    "cancelled": "任务已取消",
    "tool_failed": "工具执行失败，已保留其他已完成结果",
    "unexpected_exception": "发生未预期异常，已显式转复核",
    "unknown": "发生未分类失败，已显式转复核",
}


def _info(code: str, *, retryable: bool = False, status: int | None = None,
          message: str | None = None) -> FailureInfo:
    code = code if code in _LABELS else "unknown"
    return FailureInfo(code=code, label=_LABELS[code],
                       user_message=message or _MESSAGES[code],
                       retryable=retryable, http_status=status)


def classify_failure(error: BaseException) -> FailureInfo:
    """把传输、协议和执行异常归到稳定失败码。"""
    explicit = getattr(error, "failure_code", None)
    if isinstance(explicit, str) and explicit:
        return _info(explicit, retryable=bool(getattr(error, "retryable", False)),
                     status=getattr(error, "request_status", None) or getattr(error, "status", None))
    if isinstance(error, ExecutionLimit):
        return _info("budget_exhausted")
    status = getattr(error, "status", None) or getattr(error, "request_status", None)
    if type(status) is int:
        if status in {301, 302, 303, 307, 308}:
            return _info("redirect_rejected", status=status)
        if status in {401, 403}:
            return _info("auth_failed", status=status)
        if status == 429:
            return _info("rate_limited", retryable=True, status=status)
        if status in {400, 404, 422}:
            return _info("invalid_request", status=status)
        if 500 <= status <= 599:
            return _info("upstream_unavailable", retryable=True, status=status)
    message = str(error)
    lowered = message.lower()
    if "身份发生变化" in message:
        return _info("model_identity_changed", status=status)
    if any(token in message for token in ("流式响应中断", "未收到结束标记", "截断", "未正常完成")):
        return _info("response_truncated", status=status)
    if any(token in message for token in ("超时", "总时限", "readtimeout", "timed out")) or (
            "timeout" in lowered):
        return _info("timeout", status=status)
    if any(token in message for token in ("不是合法JSON", "不是JSON", "协议", "字段类型",
                                          "字段缺失", "额外字段", "枚举值", "编码无效")):
        return _info("invalid_response", status=status)
    if "检查点" in message:
        return _info("checkpoint_invalid", status=status)
    if "凭证" in message or "密钥" in message:
        return _info("secret_blocked", status=status)
    if "取消" in message or type(error).__name__ in {"CancelledError", "CancelError"}:
        return _info("cancelled", status=status)
    if "预算" in message:
        return _info("budget_exhausted", status=status)
    if isinstance(error, TransportError):
        return _info("network_error", retryable=bool(error.retryable), status=status)
    if type(error).__name__ == "LLMError":
        return _info("invalid_response", status=status)
    return _info("unexpected_exception", status=status)


def _run_http_case(secret: str, expected: str, *, max_retries: int = 0,
                   responses=None, action=None) -> dict:
    http = LLMHttp("https://example.invalid/v1/chat/completions", secret,
                   max_retries=max_retries)
    calls = []

    def fake(_payload):
        calls.append(1)
        result = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(result, BaseException):
            raise result
        return result

    try:
        if expected == "response_truncated":
            action = lambda: http._accumulate_sse(
                ['data: {"choices":[{"delta":{"content":"partial"}}]}'])
        elif expected == "model_identity_changed":
            action = lambda: http._accumulate_sse([
                'data: {"model":"model-a","choices":[{"delta":{"content":"a"}}]}',
                'data: {"model":"model-b","choices":[{"delta":{"content":"b"}}]}',
                'data: [DONE]',
            ])
        http._post_once_json = fake
        try:
            action = action or (lambda: http.post_json({}))
            action()
            code = expected if expected == "rate_limited" and len(calls) == 2 else "no_failure"
            recovered = expected == "rate_limited" and len(calls) == 2
        except Exception as exc:  # noqa: BLE001 - 矩阵必须记录所有注入故障
            code = classify_failure(exc).code
            recovered = False
        return {"code": code, "attempts": len(calls), "passed": code == expected,
                "recovered": recovered}
    finally:
        http.close()


def _partial_success_case(secret: str) -> dict:
    from audit.client import LLMClient
    from audit.service import check_text
    from materials import Run

    config = copy.deepcopy(DEFAULTS)
    config["http"]["max_retries"] = 0
    config["document"]["chunk_sentences"] = 1
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 2:
            return httpx.Response(503)
        payload = json.loads(request.content)
        submitted = json.loads(payload["messages"][-1]["content"])
        identifiers = [sentence["sentence_id"] for sentence in submitted["sentences"]]
        body = {"model": "actual-model", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"items": [], "unclaimed_sentences": identifiers})}}],
            "usage": {"total_tokens": 5}}
        return httpx.Response(200, json=body)

    with tempfile.TemporaryDirectory(prefix="p3_partial_") as directory:
        root = Path(directory)
        draft = root / "draft.txt"
        draft.write_text("贵州茅台2025年收入100元。\n贵州茅台2025年利润100元。",
                         encoding="utf-8")
        client = LLMClient("https://example.invalid/v1", "test-model", secret, config=config)
        client._http.close()
        client._http._client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            result = check_text(draft, [], Run(root, "partial", {}), client, use_loop=False)
            failed = result.get("failed_chunks") or []
            passed = (result.get("status") == "partial"
                      and result.get("unfinished_sentence_ids") == [2]
                      and failed
                      and failed[0].get("failure_code") == "upstream_unavailable")
            return {"code": "upstream_unavailable" if failed else "no_failure",
                    "attempts": len(calls), "passed": passed,
                    "recovered": False}
        finally:
            client._http.close()


def _secret_case(secret: str) -> dict:
    from audit.client import LLMClient
    from materials import Run

    client = LLMClient("https://example.invalid/v1", "test-model", secret)
    client._http.close()
    client._http._client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={
            "model": "actual-model",
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"items": [], "unclaimed_sentences": [1],
                                       "leak": secret})}}],
        })))
    try:
        with tempfile.TemporaryDirectory(prefix="p3_secret_") as directory:
            try:
                client.extract([{"sentence_id": 1, "text": "贵州茅台2025年收入100元。"}],
                               Run(Path(directory), "secret", {}), [])
                code = "no_failure"
            except Exception as exc:  # noqa: BLE001 - 安全门必须拒绝任意响应正文泄露
                code = classify_failure(exc).code
            passed = code == "secret_blocked"
            return {"code": code, "attempts": 1, "passed": passed, "recovered": False}
    finally:
        client._http.close()


def run_offline_fault_matrix(secret: str) -> dict:
    """运行不联网、无真实模型调用的 P3 首批故障矩阵。"""
    timeout = TransportError("模型接口连接失败或超时：ReadTimeout", retryable=True)
    cases = [
        ("timeout", _run_http_case(secret, "timeout", responses=[timeout])),
        ("rate_limited", _run_http_case(
            secret, "rate_limited", max_retries=1,
            responses=[(429, b""), (200, b'{"choices":[]}')])),
        ("auth_failed", _run_http_case(secret, "auth_failed", responses=[(401, b"")])),
        ("invalid_response", _run_http_case(
            secret, "invalid_response", responses=[(200, b"not-json")])),
        ("truncated_response", _run_http_case(secret, "response_truncated")),
        ("model_identity_changed", _run_http_case(secret, "model_identity_changed")),
        ("partial_success", _partial_success_case(secret)),
        ("secret_safety", _secret_case(secret)),
    ]
    rows = [{"name": name, **result} for name, result in cases]
    return {"schema_version": 1, "ok": all(row["passed"] for row in rows),
            "case_count": len(rows), "cases": rows}
