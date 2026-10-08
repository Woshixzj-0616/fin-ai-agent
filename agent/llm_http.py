"""LLM HTTP 层：httpx + 退避重试 + 可选流式。

设计
----
- 只暴露 post_json / post_stream；凭证只进 Authorization，永不进日志/异常正文
- 重试：429/502/503/504/超时/连接错误，指数退避；4xx 业务错误不重试
- 流式：OpenAI 兼容 SSE，聚合成完整 message（含 tool_calls 增量）
- 无 httpx 时退回 urllib（功能等价，无流式）
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

try:
    import httpx
except ImportError:  # pragma: no cover — 可选依赖
    httpx = None  # type: ignore

RETRY_STATUS = {429, 502, 503, 504}
MAX_BODY = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 3


class TransportError(Exception):
    """网络/HTTP 失败（已重试）。不携带响应正文，避免回显密钥。"""

    def __init__(self, message: str, status: int | None = None, retries: int = 0):
        super().__init__(message)
        self.status = status
        self.retries = retries


def _backoff(attempt: int) -> float:
    """0.5s, 1s, 2s … + 抖动，避免打爆服务商。"""
    return min(8.0, 0.5 * (2 ** attempt)) + random.uniform(0, 0.25)


class LLMHttp:
    def __init__(self, url: str, key: str, *,
                 timeout: float = DEFAULT_TIMEOUT, max_retries: int = MAX_RETRIES):
        if not url.lower().startswith("https://"):
            raise TransportError("LLM 仅接受 HTTPS 接口")
        self.url = url
        self.key = key
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(timeout=timeout, follow_redirects=False) if httpx else None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # ── 内部：一次请求 ──────────────────────────────────────

    def _post_once_json(self, payload: dict) -> tuple[int, bytes]:
        body = json.dumps(payload).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
        if self._client is not None:
            resp = self._client.post(self.url, content=body, headers=headers)
            if resp.status_code in {301, 302, 303, 307, 308}:
                raise TransportError("模型接口发生重定向，请核实 Base URL；密钥未转发",
                                     status=resp.status_code)
            content = resp.content or b""
            if len(content) > MAX_BODY:
                raise TransportError("模型响应超过2MiB限制", status=resp.status_code)
            return resp.status_code, content
        req = urllib.request.Request(self.url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                content = response.read(MAX_BODY + 1)
                if len(content) > MAX_BODY:
                    raise TransportError("模型响应超过2MiB限制")
                return response.status, content
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                raise TransportError("模型接口发生重定向，请核实 Base URL；密钥未转发",
                                     status=exc.code) from None
            # 不读取正文，防止回显鉴权信息
            return exc.code, b""
        except urllib.error.URLError as exc:
            raise TransportError(f"模型接口连接失败或超时：{type(exc).__name__}") from None

    def post_json(self, payload: dict, *,
                  on_retry: Callable[[int, int | None, float], None] | None = None) -> dict:
        """POST + 退避重试，返回解析后的 JSON body。"""
        last_status: int | None = None
        retries = 0
        for attempt in range(self.max_retries + 1):
            try:
                status, content = self._post_once_json(payload)
            except TransportError as exc:
                last_status = exc.status
                if attempt >= self.max_retries:
                    raise TransportError(str(exc), status=exc.status, retries=retries) from None
                retries += 1
                wait = _backoff(attempt)
                if on_retry:
                    on_retry(retries, last_status, wait)
                time.sleep(wait)
                continue

            if status == 200:
                try:
                    return json.loads(content)
                except ValueError as exc:
                    raise TransportError("模型响应不是 JSON", status=200, retries=retries) from exc

            if status in RETRY_STATUS and attempt < self.max_retries:
                retries += 1
                wait = _backoff(attempt)
                if on_retry:
                    on_retry(retries, status, wait)
                time.sleep(wait)
                last_status = status
                continue

            raise TransportError(f"模型接口HTTP {status}", status=status, retries=retries)

        raise TransportError(f"模型接口HTTP {last_status}", status=last_status, retries=retries)

    # ── 流式 SSE ───────────────────────────────────────────

    def _accumulate_sse(self, lines: list[str]) -> dict:
        """把 OpenAI 流式增量聚成完整 message。"""
        content_parts: list[str] = []
        tool_map: dict[int, dict] = {}
        finish_reason = None
        usage: dict = {}
        for raw in lines:
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content_parts.append(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_map.setdefault(idx, {
                        "id": tc.get("id") or f"call_{idx}",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts) or None}
        if tool_map:
            message["tool_calls"] = [tool_map[i] for i in sorted(tool_map)]
        return {"choices": [{"finish_reason": finish_reason or "stop", "message": message}],
                "usage": usage}

    def post_stream(self, payload: dict, *,
                    on_delta: Callable[[str], None] | None = None,
                    on_retry: Callable[[int, int | None, float], None] | None = None) -> dict:
        """SSE 流式调用，返回与 post_json 同构的聚合 body。"""
        if httpx is None or self._client is None:
            # urllib 路径不支持流式，退回非流式
            body = dict(payload)
            body["stream"] = False
            return self.post_json(body, on_retry=on_retry)

        body = dict(payload)
        body["stream"] = True
        last_status: int | None = None
        retries = 0
        for attempt in range(self.max_retries + 1):
            data = json.dumps(body).encode("utf-8")
            headers = {"Authorization": "Bearer " + self.key,
                       "Content-Type": "application/json",
                       "Accept": "text/event-stream"}
            try:
                with self._client.stream("POST", self.url, content=data, headers=headers) as resp:
                    if resp.status_code in {301, 302, 303, 307, 308}:
                        raise TransportError("模型接口发生重定向，请核实 Base URL；密钥未转发",
                                             status=resp.status_code)
                    if resp.status_code != 200:
                        # 排空但不记录正文
                        try:
                            resp.read()
                        except Exception:  # noqa: BLE001
                            pass
                        if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                            retries += 1
                            wait = _backoff(attempt)
                            if on_retry:
                                on_retry(retries, resp.status_code, wait)
                            time.sleep(wait)
                            last_status = resp.status_code
                            continue
                        raise TransportError(f"模型接口HTTP {resp.status_code}",
                                             status=resp.status_code, retries=retries)
                    lines: list[str] = []
                    for line in resp.iter_lines():
                        if line.startswith("data:"):
                            piece = line[5:].strip()
                            if piece and piece != "[DONE]" and on_delta:
                                try:
                                    chunk = json.loads(piece)
                                    for choice in chunk.get("choices") or []:
                                        delta = (choice.get("delta") or {}).get("content")
                                        if delta:
                                            on_delta(delta)
                                except ValueError:
                                    pass
                        lines.append(line)
                    return self._accumulate_sse(lines)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_status = None
                if attempt >= self.max_retries:
                    raise TransportError(f"模型接口连接失败或超时：{type(exc).__name__}",
                                         retries=retries) from None
                retries += 1
                wait = _backoff(attempt)
                if on_retry:
                    on_retry(retries, last_status, wait)
                time.sleep(wait)
                continue
            except TransportError as exc:
                if exc.status in RETRY_STATUS and attempt < self.max_retries:
                    retries += 1
                    wait = _backoff(attempt)
                    if on_retry:
                        on_retry(retries, exc.status, wait)
                    time.sleep(wait)
                    continue
                raise TransportError(str(exc), status=exc.status, retries=retries) from None

        raise TransportError(f"模型接口HTTP {last_status}", status=last_status, retries=retries)


def parse_chat_body(body: dict) -> dict:
    """校验 chat/completions 并返回 message。不解析自由文本里的 JSON。"""
    try:
        choice = body["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("模型响应不是 chat/completions 协议") from exc
    if message.get("refusal"):
        raise ValueError("模型拒绝了本次请求")
    return {"message": message, "finish_reason": choice.get("finish_reason"),
            "usage": {k: v for k, v in (body.get("usage") or {}).items()
                      if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                      and type(v) is int}}
