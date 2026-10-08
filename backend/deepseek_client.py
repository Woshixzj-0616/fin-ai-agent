"""DeepSeek API call and response validation for the first prototype."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError

from backend.core.config import PROJECT_ROOT, load_runtime_config
from backend.core.model_io import recorded_completion

ROOT = PROJECT_ROOT
load_runtime_config()
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")


class ModelCallError(RuntimeError):
    """A safe, user-readable model configuration or API error."""


def api_key_configured() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


def _prompt() -> str:
    prompt_path = Path(__file__).resolve().parent / "prompts" / "财务概览_v1.md"
    return prompt_path.read_text(encoding="utf-8")


def _normalize_result(raw: str, allowed_pages: set[int]) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelCallError("DeepSeek 返回内容格式不完整，请重新运行。") from exc
    if not isinstance(result, dict):
        raise ModelCallError("DeepSeek 返回格式不符合要求，请重新运行。")

    def text_field(name: str, default: str = "") -> str:
        value = result.get(name, default)
        return value.strip() if isinstance(value, str) else default

    facts_in = result.get("facts", [])
    if not isinstance(facts_in, list):
        facts_in = []
    facts: list[dict[str, Any]] = []
    invalid_references = False
    for item in facts_in[:8]:
        if not isinstance(item, dict):
            continue

        def item_text(name: str, default: str = "", limit: int = 240) -> str:
            value = item.get(name, default)
            if value is None:
                return default
            return str(value).strip()[:limit]

        refs = item.get("source_pages", [])
        if not isinstance(refs, list):
            refs = []
        valid_refs: list[int] = []
        for ref in refs:
            try:
                page = int(ref)
            except (TypeError, ValueError):
                invalid_references = True
                continue
            if page in allowed_pages and page not in valid_refs:
                valid_refs.append(page)
            else:
                invalid_references = True
        facts.append(
            {
                "name": item_text("name", "未命名指标", 80) or "未命名指标",
                "value": item_text("value", "未识别", 100) or "未识别",
                "unit": item_text("unit", "", 40),
                "period": item_text("period", "", 40),
                "change": item_text("change", "", 120),
                "source_pages": valid_refs,
                "note": item_text("note", "", 240),
            }
        )

    observations = result.get("observations", [])
    uncertainties = result.get("uncertainties", [])
    if not isinstance(observations, list):
        observations = []
    if not isinstance(uncertainties, list):
        uncertainties = []
    uncertainties = [str(item).strip()[:300] for item in uncertainties[:8] if item is not None and str(item).strip()]
    if invalid_references:
        uncertainties.append("模型引用了本次未提供的页码；这些页码已从引用中移除，请人工复核。")

    return {
        "company": text_field("company", "未能识别"),
        "period": text_field("period", "未能识别"),
        "summary": text_field("summary", "模型未能生成概览。"),
        "facts": facts,
        "observations": [str(item).strip()[:500] for item in observations[:8] if item is not None and str(item).strip()],
        "uncertainties": uncertainties,
    }


def analyze_pages(file_name: str, page_count: int, pages: list[dict[str, Any]]) -> dict[str, Any]:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ModelCallError("尚未配置 DeepSeek API Key。请先在页面顶部打开配置窗口。")

    page_text = "\n\n".join(
        f"【文档：{file_name}；PDF第{page['page']}页】\n{page['text']}" for page in pages
    )
    user_message = (
        f"文件名：{file_name}\nPDF总页数：{page_count}\n"
        f"以下是程序从原 PDF 中提取的候选页，请按系统流程生成财务概览。\n\n{page_text}"
    )

    try:
        client = OpenAI(
            api_key=key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "90")),
            max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "1")),
        )
        response = recorded_completion(
            client=client,
            model=MODEL,
            messages=[
                {"role": "system", "content": _prompt()},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            stream=False,
        )
    except AuthenticationError as exc:
        raise ModelCallError("DeepSeek API Key 无效，请检查项目根目录的 .env 配置。") from exc
    except RateLimitError as exc:
        raise ModelCallError("DeepSeek 当前请求受限或账户余额不足，请稍后重试并检查账户状态。") from exc
    except APITimeoutError as exc:
        raise ModelCallError("DeepSeek 响应超时，请稍后重新运行。") from exc
    except APIConnectionError as exc:
        raise ModelCallError("后台服务无法连接 DeepSeek。请检查后台进程的网络访问权限或代理设置，然后重试。") from exc
    except APIStatusError as exc:
        raise ModelCallError(f"DeepSeek 接口暂不可用（HTTP {exc.status_code}），请稍后重试。") from exc
    except Exception as exc:
        raise ModelCallError("调用 DeepSeek 时发生错误，请查看后端运行窗口。") from exc

    if not response.choices:
        raise ModelCallError("DeepSeek 没有返回结果，请重新运行。")
    message = response.choices[0].message
    if not message.content:
        raise ModelCallError("DeepSeek 返回了空结果，请重新运行。")
    result = _normalize_result(message.content, {int(page["page"]) for page in pages})
    result["model"] = response.model or MODEL
    usage = response.usage
    result["usage"] = {
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
    }
    return result
