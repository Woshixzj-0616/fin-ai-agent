"""模型 HTTP 客户端、结构化响应校验和凭证门控。"""
from __future__ import annotations

import getpass
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from audit.prompt import SYSTEM_PROMPT
from audit.schema import (
    COMPANIES,
    METRICS,
    SECRET_PATTERN,
    LLMError,
    companies_from_facts,
    schema,
    validate_schema,
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LLMError("模型接口发生重定向，请核实Base URL后重试；密钥未转发")


class LLMClient:
    def __init__(self, base_url: str, model: str, key: str, mode="json_schema",
                 *, stream: bool | None = None):
        try:
            parts = urllib.parse.urlsplit(base_url)
        except ValueError:
            raise LLMError("LLM_BASE_URL格式无效，请检查HTTPS服务地址") from None
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment):
            raise LLMError("LLM_BASE_URL须为不含账号、查询参数或片段的HTTPS服务地址")
        if not key or not model or mode not in {"json_schema", "json_object"}:
            raise LLMError("请配置LLM_API_KEY、LLM_MODEL及有效的结构化输出模式")
        if not key.isascii() or re.search(r"\s", key):
            raise LLMError("密钥包含空白或无效字符，请重新输入；不要把接入说明整段作为密钥")
        if key in base_url or key in model or SECRET_PATTERN.search(base_url + model):
            raise LLMError("服务地址或模型名疑似混入密钥，请重新配置")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.key, self.mode = model, key, mode
        self.host = parts.hostname
        if stream is None:
            stream = os.environ.get("LLM_STREAM", "").lower() in {"1", "true", "yes", "on"}
        self.stream = bool(stream)
        from llm_http import LLMHttp
        self._http = LLMHttp(self.url, self.key)

    @classmethod
    def from_environment(cls, *, base_url=None, model=None, mode=None, ask_key=False):
        key = getpass.getpass("临时API密钥（不回显、不保存）：") if ask_key else os.environ.get("LLM_API_KEY", "")
        return cls(base_url or os.environ.get("LLM_BASE_URL", ""),
                   model or os.environ.get("LLM_MODEL", ""), key,
                   mode or os.environ.get("LLM_FORMAT", "json_schema"))

    def _guard_blob(self, blob: bytes) -> None:
        if self.key.encode() in blob or SECRET_PATTERN.search(blob.decode("utf-8", errors="replace")):
            raise LLMError("模型响应疑似含凭证，已拒绝保存")

    def _on_retry(self, run):
        def _cb(attempt, status, wait):
            run.event("llm_retry", attempt=attempt, status=status, wait_sec=round(wait, 2))
        return _cb

    def _request(self, payload: dict, run) -> dict:
        """带退避重试的统一请求；可选流式。"""
        try:
            if self.stream:
                body = self._http.post_stream(payload, on_retry=self._on_retry(run))
            else:
                body = self._http.post_json(payload, on_retry=self._on_retry(run))
        except Exception as exc:  # TransportError 等
            message = str(exc)
            # 服务端错误正文可能回显认证信息；不保存、不打印该正文。
            if re.search(r"HTTP\s*\d{3}", message):
                raise LLMError(f"{message}；请核实服务地址、模型权限和结构化输出模式") from None
            raise LLMError("模型接口连接失败或超时；请核实网络后重试") from None
        # 密钥泄漏门控：序列化后检查
        self._guard_blob(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        return body

    def extract(self, sentences: list[dict], run, facts: list[dict] | None = None) -> dict:
        contract = schema()
        response_format = {"type": "json_object"} if self.mode == "json_object" else {
            "type": "json_schema", "json_schema": {"name": "financial_claims", "strict": True, "schema": contract}}
        loaded = companies_from_facts(facts) or COMPANIES
        system = SYSTEM_PROMPT + "\n本次已加载公司（不限于此，新材料的公司同样适用）：" + json.dumps(loaded, ensure_ascii=False)
        system += "\n指标词典：" + json.dumps(METRICS, ensure_ascii=False)
        system += "\nJSON Schema：" + json.dumps(contract, ensure_ascii=False)
        payload = {"model": self.model, "stream": self.stream, "temperature": 0,
                   "response_format": response_format,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps({"sentences": sentences}, ensure_ascii=False)}]}
        run.event("llm_request", provider_host=self.host, model=self.model, response_format=self.mode,
                  sentence_count=len(sentences), stream=self.stream)
        body = self._request(payload, run)
        try:
            choice = body["choices"][0]
            if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                raise LLMError("模型未正常完成结构化输出（截断、拒绝或其他结束状态），本次未判定")
            content = choice["message"]["content"]
            parsed = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError("模型响应不符合JSON协议；不尝试从自由文本或代码块中猜测字段") from None
        validate_schema(parsed, contract)
        usage_source = body.get("usage") or {}
        usage = {k: v for k, v in usage_source.items()
                 if k in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(v) is int}
        run.event("llm_response", response_format=self.mode, item_count=len(parsed["items"]),
                  usage=usage, stream=self.stream)
        return parsed

    def chat(self, messages: list[dict], run) -> str:
        """JSON 多步协议用：不锁 json_schema，只要求返回文本（调用方自己 json.loads）。"""
        payload = {"model": self.model, "stream": self.stream, "temperature": 0,
                   "messages": messages}
        run.event("llm_chat", provider_host=self.host, model=self.model,
                  message_count=len(messages), stream=self.stream)
        body = self._request(payload, run)
        try:
            choice = body["choices"][0]
            if choice.get("finish_reason") not in {"stop", "length", None} or choice["message"].get("refusal"):
                raise LLMError("模型未正常完成多步协议帧")
            return choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError("模型响应不是 chat/completions 协议") from None

    def chat_tools(self, messages: list[dict], tools: list[dict], run) -> dict:
        """原生 function calling（OpenAI/DeepSeek tools 参数）。

        返回 message dict：可能含 tool_calls 或 content。
        """
        payload = {"model": self.model, "stream": self.stream, "temperature": 0,
                   "messages": messages, "tools": tools, "tool_choice": "auto"}
        run.event("llm_chat_tools", provider_host=self.host, model=self.model,
                  message_count=len(messages), tool_count=len(tools), stream=self.stream)
        body = self._request(payload, run)
        try:
            choice = body["choices"][0]
            message = choice.get("message") or {}
            if message.get("refusal"):
                raise LLMError("模型拒绝了本次工具调用请求")
            usage = {k: v for k, v in (body.get("usage") or {}).items()
                     if k in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(v) is int}
            run.event("llm_response_tools", finish_reason=choice.get("finish_reason"),
                      has_tool_calls=bool(message.get("tool_calls")), usage=usage,
                      stream=self.stream)
            return message
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError("模型响应不是 chat/completions 协议") from None
