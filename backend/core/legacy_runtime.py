"""Shared legacy model/tool operations used by existing analysis modules.

These are compatibility helpers, not a required analysis workflow for new modules.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

from backend.deepseek_client import ModelCallError
from backend.core.model_io import model_name, new_client, recorded_completion

MODEL = model_name()
MAX_TOOL_ROUNDS = 5
MAX_PAGE_CHARS = 4800
MAX_HISTORY_TURNS = 6


def _client():
    return new_client()


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_pdf_pages",
            "description": "按指标、科目、风险或主题搜索当前年报全文，返回最相关页码和原文片段。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "简短、具体的中文搜索词"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_pdf_page",
            "description": "读取当前年报指定 PDF 页的文字原文。页码为 PDF 阅读器显示的从 1 开始的页码。",
            "parameters": {
                "type": "object",
                "properties": {"page_number": {"type": "integer", "minimum": 1}},
                "required": ["page_number"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_change",
            "description": "精确计算两个同单位财务数值的差额及同比变化。前期为零或负数时不计算普通同比百分比。",
            "parameters": {
                "type": "object",
                "properties": {
                    "current_value": {"type": "string", "description": "本期数字，可带千位逗号"},
                    "previous_value": {"type": "string", "description": "上期数字，可带千位逗号"},
                    "unit": {"type": "string", "description": "两项数值共同的单位"},
                },
                "required": ["current_value", "previous_value", "unit"],
                "additionalProperties": False,
            },
        },
    },
]

def _safe_model_error(exc: Exception) -> ModelCallError:
    if isinstance(exc, AuthenticationError):
        return ModelCallError("DeepSeek API Key 无效，请检查本机配置。")
    if isinstance(exc, RateLimitError):
        return ModelCallError("DeepSeek 当前请求受限或账户余额不足，请稍后重试并检查账户状态。")
    if isinstance(exc, APITimeoutError):
        return ModelCallError("DeepSeek 响应超时，请稍后重新运行。")
    if isinstance(exc, APIConnectionError):
        return ModelCallError("后台服务无法连接 DeepSeek。请检查后台进程的网络访问权限或代理设置，然后重试。")
    if isinstance(exc, APIStatusError):
        return ModelCallError(f"DeepSeek 接口暂不可用（HTTP {exc.status_code}），请稍后重试。")
    return ModelCallError("调用 DeepSeek 时发生错误，请检查本机服务日志。")

def _number(value: Any) -> Decimal:
    cleaned = str(value).replace(",", "").strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        raise InvalidOperation
    return Decimal(cleaned)

def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"

def _calculate_change(arguments: dict[str, Any]) -> dict[str, Any]:
    current = _number(arguments.get("current_value", ""))
    previous = _number(arguments.get("previous_value", ""))
    unit = str(arguments.get("unit", ""))[:40]
    difference = current - previous
    response: dict[str, Any] = {
        "current_value": _decimal_text(current),
        "previous_value": _decimal_text(previous),
        "difference": _decimal_text(difference),
        "unit": unit,
    }
    if previous <= 0:
        response["change_percent"] = None
        response["note"] = "上期基数为零或负数，未计算普通同比百分比。"
    else:
        response["change_percent"] = _decimal_text(difference / previous * Decimal(100))
        response["note"] = "变化率按（本期－上期）÷上期计算。"
    return response

def _json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start:index + 1])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
    return None

def _tool_result(
    name: str,
    arguments: dict[str, Any],
    *,
    page_count: int,
    get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[int]]:
    if name == "search_pdf_pages":
        query = str(arguments.get("query", "")).strip()[:120]
        if not query:
            return {"error": "搜索词不能为空。"}, []
        results = search_pages(query)
        if not results:
            return {"query": query, "results": [], "message": "没有找到明显匹配的可提取文字页面。"}, []
        return {
            "query": query,
            "results": [
                {"page": int(item["page"]), "text": str(item["text"])[:2600]}
                for item in results[:4]
            ],
        }, [int(item["page"]) for item in results[:4]]

    if name == "read_pdf_page":
        try:
            page_number = int(arguments.get("page_number"))
        except (TypeError, ValueError):
            return {"error": "页码必须是整数。"}, []
        if page_number < 1 or page_number > page_count:
            return {"error": f"页码超出范围；本 PDF 共 {page_count} 页。"}, []
        text = get_page(page_number)
        if text is None:
            return {"error": "没有找到该页的文字记录。"}, []
        return {"page": page_number, "text": text[:MAX_PAGE_CHARS]}, [page_number]

    if name == "calculate_change":
        try:
            return _calculate_change(arguments), []
        except (InvalidOperation, ValueError):
            return {"error": "数值格式无法识别；请只提供纯数字，并确认两项单位一致。"}, []

    return {"error": "该工具不可用。"}, []

def _run_tool_loop(
    *,
    messages: list[dict[str, Any]],
    page_count: int,
    get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
    on_tool: Callable[[str, str], None] | None = None,
    available_tools: list[dict[str, Any]] | None = None,
    max_rounds: int = MAX_TOOL_ROUNDS,
    recorder: Any = None,
) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    client = _client()
    trace: list[dict[str, Any]] = []
    used_pages: set[int] = set()
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    last_content = ""

    def complete(with_tools: bool = True):
        kwargs: dict[str, Any] = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
        }
        if with_tools:
            kwargs["tools"] = available_tools if available_tools is not None else TOOLS
            kwargs["tool_choice"] = "auto"
        return recorded_completion(recorder=recorder, client=client, **kwargs)

    try:
        for _round in range(max(1, min(int(max_rounds), MAX_TOOL_ROUNDS))):
            response = complete()
            if response.usage:
                usage["prompt_tokens"] += response.usage.prompt_tokens or 0
                usage["completion_tokens"] += response.usage.completion_tokens or 0
            if not response.choices:
                raise ModelCallError("DeepSeek 没有返回结果，请稍后重试。")
            message = response.choices[0].message
            last_content = message.content or ""
            tool_calls = message.tool_calls or []
            assistant_message = message.model_dump(exclude_none=True)
            messages.append(assistant_message)
            if not tool_calls:
                break

            for tool_call in tool_calls:
                name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                except (json.JSONDecodeError, ValueError):
                    arguments = {}
                    output = {"error": "工具参数不是有效对象，请重新调用。"}
                    pages: list[int] = []
                else:
                    output, pages = _tool_result(
                        name,
                        arguments,
                        page_count=page_count,
                        get_page=get_page,
                        search_pages=search_pages,
                    )
                used_pages.update(pages)
                if recorder is not None:
                    recorder.tool_call(name, arguments, output, pages=pages)
                trace.append({"tool": name, "arguments": arguments, "pages": pages, "result": output})
                if on_tool:
                    on_tool(name, json.dumps(arguments, ensure_ascii=False)[:200])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(output, ensure_ascii=False),
                    }
                )
        else:
            messages.append({"role": "user", "content": "请根据目前已经读取的材料和工具结果直接给出结论，不要再调用工具。明确写出出处页和不确定项。"})
            response = complete(with_tools=False)
            if response.usage:
                usage["prompt_tokens"] += response.usage.prompt_tokens or 0
                usage["completion_tokens"] += response.usage.completion_tokens or 0
            if not response.choices:
                raise ModelCallError("DeepSeek 没有返回结果，请稍后重试。")
            last_content = response.choices[0].message.content or ""
    except ModelCallError:
        raise
    except Exception as exc:
        raise _safe_model_error(exc) from exc

    if not last_content.strip():
        raise ModelCallError("DeepSeek 返回了空结果，请稍后重试。")
    return last_content, trace, usage | {"_tool_pages": sorted(used_pages)}
