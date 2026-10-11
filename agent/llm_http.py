"""LLM HTTP层：有限重试、逐次留证、绝对时限和有界流式响应。

保留 fin 的 post_json/post_stream 契约；凭证不进入日志或异常正文。
"""
from __future__ import annotations

import json
import codecs
import random
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Callable

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

RETRY_STATUS = {429, 502, 503, 504}
MAX_BODY = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 3


class TransportError(Exception):
    def __init__(self, message: str, status: int | None = None, retries: int = 0,
                 *, retryable: bool | None = None):
        super().__init__(message)
        self.status, self.retries = status, retries
        self.retryable = (status is None or status in RETRY_STATUS) if retryable is None else retryable


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _backoff(attempt: int) -> float:
    return min(8.0, 0.5 * 2 ** attempt) + random.uniform(0, 0.25)


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        if not 0 <= seconds < float("inf"):
            return None
    except (ValueError, TypeError):
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = max(0, (when - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None
    return min(30.0, seconds)


class LLMHttp:
    def __init__(self, url: str, key: str, *, timeout: float = DEFAULT_TIMEOUT,
                 max_retries: int = MAX_RETRIES, request_deadline: float = 240):
        if not url.lower().startswith("https://"):
            raise TransportError("LLM仅接受HTTPS接口", retryable=False)
        self.url, self.key = url, key
        self.timeout, self.max_retries = timeout, max_retries
        self.request_deadline = request_deadline
        self._client = httpx.Client(timeout=timeout, follow_redirects=False) if httpx else None
        self._opener = urllib.request.build_opener(_NoRedirect())
        self._deadline, self._retry_after = None, None
        self._on_delta = None

    def close(self):
        if self._client is not None:
            self._client.close()

    def _remaining(self):
        remaining = self.timeout if self._deadline is None else self._deadline - time.monotonic()
        if remaining <= 0:
            raise TransportError("模型请求总时限已用尽", retryable=False)
        return min(self.timeout, remaining)

    def _headers(self):
        return {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}

    def _post_once_json(self, payload: dict) -> tuple[int, bytes]:
        data = json.dumps(payload).encode("utf-8")
        if self._client is not None:
            try:
                with self._client.stream("POST", self.url, content=data, headers=self._headers(),
                                         timeout=self._remaining()) as response:
                    self._retry_after = response.headers.get("Retry-After")
                    if response.status_code != 200:
                        return response.status_code, b""
                    parts, size = [], 0
                    for chunk in response.iter_bytes():
                        self._remaining()
                        size += len(chunk)
                        if size > MAX_BODY:
                            raise TransportError("模型响应超过2MiB限制", status=200, retryable=False)
                        parts.append(chunk)
                    return response.status_code, b"".join(parts)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise TransportError("模型接口连接失败或超时：" + type(exc).__name__) from None
        request = urllib.request.Request(self.url, data=data, headers=self._headers())
        try:
            with self._opener.open(request, timeout=self._remaining()) as response:
                self._retry_after = response.headers.get("Retry-After")
                parts, size = [], 0
                read = getattr(response, "read1", response.read)
                while True:
                    self._remaining()
                    chunk = read(min(65536, MAX_BODY + 1 - size))
                    if not chunk:
                        break
                    parts.append(chunk)
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise TransportError("模型响应超过2MiB限制", status=200, retryable=False)
                return response.status, b"".join(parts)
        except urllib.error.HTTPError as exc:
            self._retry_after = exc.headers.get("Retry-After") if exc.headers else None
            code = exc.code
            exc.close()
            return code, b""
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise TransportError("模型接口连接失败或超时：" + type(exc).__name__) from None

    def _accumulate_sse(self, lines: list[str]) -> dict:
        content, tools, usage = [], {}, {}
        finish, model, completed = None, None, False
        for raw in lines:
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                completed = True
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                raise TransportError("模型流式数据不是合法JSON", status=200, retryable=False) from None
            if not isinstance(chunk, dict) or chunk.get("error"):
                raise TransportError("模型流式返回错误协议帧", status=200, retryable=False)
            if isinstance(chunk.get("model"), str):
                if model is not None and model != chunk["model"]:
                    raise TransportError("同一流式请求内模型身份发生变化", status=200, retryable=False)
                model = chunk["model"]
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                if delta.get("refusal"):
                    raise TransportError("模型拒绝了本次请求", status=200, retryable=False)
                for call in delta.get("tool_calls") or []:
                    index = call.get("index", 0)
                    slot = tools.setdefault(index, {"id": call.get("id") or f"call_{index}",
                                                   "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
                    if call.get("id"):
                        slot["id"] = call["id"]
                    fn = call.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
        if not completed and finish is None:
            raise TransportError("模型流式响应中断，未收到结束标记", status=200, retryable=False)
        message = {"role": "assistant", "content": "".join(content) or None}
        if tools:
            message["tool_calls"] = [tools[index] for index in sorted(tools)]
        return {"choices": [{"finish_reason": finish, "message": message}],
                "usage": usage, "model": model}

    def _post_once_stream(self, payload: dict) -> tuple[int, dict | None]:
        data = json.dumps(payload).encode("utf-8")
        headers = {**self._headers(), "Accept": "text/event-stream"}
        try:
            with self._client.stream("POST", self.url, content=data, headers=headers,
                                     timeout=self._remaining()) as response:
                self._retry_after = response.headers.get("Retry-After")
                if response.status_code != 200:
                    return response.status_code, None
                lines, size, pending = [], 0, ""
                decoder = codecs.getincrementaldecoder("utf-8")()
                for piece in response.iter_bytes():
                    self._remaining()
                    size += len(piece)
                    if size > MAX_BODY:
                        raise TransportError("模型流式响应超过2MiB限制", status=200, retryable=False)
                    try:
                        pending += decoder.decode(piece)
                    except UnicodeError:
                        raise TransportError("模型流式编码无效", status=200, retryable=False) from None
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line = line.rstrip("\r")
                        if self._on_delta and line.startswith("data:") and line[5:].strip() != "[DONE]":
                            try:
                                chunk = json.loads(line[5:].strip())
                                for choice in chunk.get("choices") or []:
                                    delta = (choice.get("delta") or {}).get("content")
                                    if delta:
                                        self._on_delta(delta)
                            except (ValueError, AttributeError):
                                pass  # 完整聚合时按严格协议校验。
                        lines.append(line)
                        if line.strip() == "data: [DONE]":
                            return 200, self._accumulate_sse(lines)
                try:
                    pending += decoder.decode(b"", final=True)
                except UnicodeError:
                    raise TransportError("模型流式编码未完整结束", status=200, retryable=False) from None
                if pending.strip():
                    lines.append(pending)
                return 200, self._accumulate_sse(lines)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransportError("模型接口连接失败或超时：" + type(exc).__name__) from None

    def _request(self, payload, stream, on_retry, on_attempt, on_result, deadline):
        self._deadline = min(time.monotonic() + self.request_deadline,
                             deadline if deadline is not None else float("inf"))
        retries = 0
        for attempt in range(self.max_retries + 1):
            self._remaining()
            self._retry_after = None
            if on_attempt:
                on_attempt(payload)
            started, status, body = time.monotonic(), None, None
            error = None
            try:
                if stream:
                    status, body = self._post_once_stream(payload)
                else:
                    status, content = self._post_once_json(payload)
                    if status == 200:
                        try:
                            body = json.loads(content)
                        except (ValueError, TypeError):
                            raise TransportError("模型响应不是JSON", status=200, retryable=False) from None
                self._remaining()
                if status != 200:
                    message = ("模型接口发生重定向，密钥未转发；请核实Base URL"
                               if status in {301, 302, 303, 307, 308} else f"模型接口HTTP {status}")
                    raise TransportError(message, status=status)
                if not isinstance(body, dict):
                    raise TransportError("模型响应不是JSON对象", status=200, retryable=False)
                return body
            except TransportError as exc:
                error, status = exc, exc.status
            finally:
                if on_result:
                    on_result(status, body, time.monotonic() - started)
            if not error.retryable or attempt >= self.max_retries:
                raise TransportError(str(error), status=error.status, retries=retries,
                                     retryable=error.retryable) from None
            wait = retry_after_seconds(self._retry_after)
            wait = _backoff(attempt) if wait is None else wait
            if time.monotonic() + wait >= self._deadline:
                raise TransportError("模型请求剩余时限不足以重试", status=status,
                                     retries=retries, retryable=False)
            retries += 1
            if on_retry:
                on_retry(retries, status, wait)
            time.sleep(wait)
        raise TransportError("模型请求未完成", retryable=False)

    def post_json(self, payload: dict, *, on_retry: Callable | None = None,
                  on_attempt: Callable | None = None, on_result: Callable | None = None,
                  deadline: float | None = None) -> dict:
        return self._request(payload, False, on_retry, on_attempt, on_result, deadline)

    def post_stream(self, payload: dict, *, on_delta: Callable | None = None,
                    on_retry: Callable | None = None, on_attempt: Callable | None = None,
                    on_result: Callable | None = None, deadline: float | None = None) -> dict:
        body = dict(payload)
        if self._client is None:
            body["stream"] = False
            body.pop("stream_options", None)
            return self.post_json(body, on_retry=on_retry, on_attempt=on_attempt,
                                  on_result=on_result, deadline=deadline)
        body.update(stream=True, stream_options={"include_usage": True})
        self._on_delta = on_delta
        return self._request(body, True, on_retry, on_attempt, on_result, deadline)


def parse_chat_body(body: dict) -> dict:
    try:
        choice = body["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("模型响应不是chat/completions协议") from exc
    if message.get("refusal"):
        raise ValueError("模型拒绝了本次请求")
    return {"message": message, "finish_reason": choice.get("finish_reason"),
            "usage": {k: v for k, v in (body.get("usage") or {}).items()
                      if k in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(v) is int}}
