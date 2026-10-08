"""DeepSeek extraction and evidence-grounded review for V3 tasks."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from backend.deepseek_agent import _client, _safe_model_error
from backend.deepseek_client import MODEL, ModelCallError


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "研报核查_v3.md"
MAX_CLAIMS_PER_CHUNK = 24
MAX_TOOL_ROUNDS = 3
MAX_TOOL_PAGE_CHARS = 4200
MAX_PAGE_CHARS = 4200
OUTCOMES = {"已核对一致", "发现错误", "证据不足", "超出范围"}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_financial_report",
            "description": "只搜索本任务已上传的适用年度财报，返回候选 PDF 页及原文片段。",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_financial_page",
            "description": "读取已上传财报指定 PDF 页，支持页内分段；页码从 1 开始。",
            "parameters": {"type": "object", "properties": {"page_number": {"type": "integer", "minimum": 1}, "start_character": {"type": "integer", "minimum": 0}}, "required": ["page_number"], "additionalProperties": False},
        },
    },
        {
            "type": "function",
            "function": {
                "name": "calculate_financial_change",
            "description": "使用 Decimal 算术计算同单位、可比期间的差额和同比；上期为零或负数时不计算普通同比。",
                "parameters": {"type": "object", "properties": {"current_value": {"type": "string"}, "previous_value": {"type": "string"}, "unit": {"type": "string"}}, "required": ["current_value", "previous_value", "unit"], "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "convert_financial_amount",
                "description": "用 Decimal 在人民币元、千元、万元、百万元和亿元之间换算，并按研报显示精度比较主张数值。源数值和单位必须来自已读取的年报原文。",
                "parameters": {"type": "object", "properties": {"source_value": {"type": "string"}, "source_unit": {"type": "string"}, "target_unit": {"type": "string"}, "claim_value": {"type": "string"}}, "required": ["source_value", "source_unit", "target_unit", "claim_value"], "additionalProperties": False},
            },
        },
]


def _system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _parse_json(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ModelCallError("DeepSeek 返回内容格式不完整，请继续处理或重试。") from exc
    if not isinstance(data, dict):
        raise ModelCallError("DeepSeek 返回内容格式不符合要求。")
    return data


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def _zero_usage_trace() -> list[dict[str, Any]]:
    return [{"usage": {"prompt_tokens": 0, "completion_tokens": 0}}]


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def extract_claims(
    *, run_id: str, analyst_name: str, company: str, report_year: str,
    chunk_index: int, pages: list[dict[str, Any]],
) -> dict[str, Any]:
    page_set = {int(page["page"]) for page in pages}
    material = "\n\n".join(f"【PDF第{int(p['page'])}页】\n{p['text']}" for p in pages)
    user_text = (
        f"任务：{run_id}。研报文件：{analyst_name}。用户确认公司：{company}；目标年度：{report_year}。"
        f"这是研报分段 {chunk_index + 1}，仅提供了 PDF 页码 {sorted(page_set)}。"
        "逐页提取其中可核查的原子主张。source_quote 必须逐字来自指定页；不要引用文件外资料。\n\n" + material
    )
    try:
        response = _client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": _system_prompt()}, {"role": "user", "content": user_text}],
            response_format={"type": "json_object"},
            max_tokens=5000,
            reasoning_effort="none",
            stream=False,
        )
    except Exception as exc:
        raise _safe_model_error(exc) from exc
    if not response.choices or not response.choices[0].message.content:
        raise ModelCallError("DeepSeek 没有返回研报主张，请稍后继续。")
    data = _parse_json(response.choices[0].message.content)
    raw_claims = data.get("claims")
    if not isinstance(raw_claims, list):
        raise ModelCallError("DeepSeek 没有返回有效的主张清单。")
    claims: list[dict[str, Any]] = []
    for raw in raw_claims[:MAX_CLAIMS_PER_CHUNK]:
        if not isinstance(raw, dict):
            continue
        try:
            page = int(raw.get("source_page"))
        except (TypeError, ValueError):
            continue
        quote = str(raw.get("source_quote", "")).strip()[:1000]
        full_page = next((str(item["text"]) for item in pages if int(item["page"]) == page), "")
        verified = bool(quote and _norm(quote) in _norm(full_page))
        claim_type = str(raw.get("claim_type", "historical_fact"))
        category = str(raw.get("category", "financial_fact"))
        if category not in {"financial_fact", "prediction", "investment_opinion", "external_or_other"}:
            category = "financial_fact"
        if claim_type not in {"historical_fact", "forecast", "opinion", "external"}:
            claim_type = "historical_fact" if category == "financial_fact" else "external"
        text = str(raw.get("claim_text", "")).strip()[:1200]
        if not text or not quote or page not in page_set:
            continue
        claims.append({
            "source_page": page,
            "source_quote": quote,
            "claim_text": text,
            "category": category,
            "metric": str(raw.get("metric", ""))[:100],
            "period": str(raw.get("period", ""))[:80],
            "value": str(raw.get("value", ""))[:100],
            "unit": str(raw.get("unit", ""))[:50],
            "claim_type": claim_type,
            "extraction_verified": verified,
        })
    return {"claims": claims, "usage": _usage(response), "model": response.model or MODEL}


def _calculate(args: dict[str, Any]) -> dict[str, str]:
    try:
        current = Decimal(str(args["current_value"]).replace(",", "").strip())
        previous = Decimal(str(args["previous_value"]).replace(",", "").strip())
    except (KeyError, InvalidOperation, ValueError) as exc:
        return {"error": "数值格式无效；请从已读取原文中提取纯数字。"}
    unit = str(args.get("unit", "")).strip()
    if not unit:
        return {"error": "计算必须说明相同的单位。"}
    delta = current - previous
    result = {"difference": str(delta), "unit": unit}
    if previous > 0:
        result["change_percent"] = str((delta / previous * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    else:
        result["change_percent"] = "基数为零或负数，不计算普通同比"
    return result


_CURRENCY_UNITS = {
    "元": Decimal("1"), "人民币元": Decimal("1"),
    "千元": Decimal("1000"), "万元": Decimal("10000"),
    "百万元": Decimal("1000000"), "亿元": Decimal("100000000"),
}


def _convert_financial_amount(args: dict[str, Any]) -> dict[str, Any]:
    source_unit = str(args.get("source_unit", "")).strip()
    target_unit = str(args.get("target_unit", "")).strip()
    if source_unit not in _CURRENCY_UNITS or target_unit not in _CURRENCY_UNITS:
        return {"error": "暂不支持该金额单位；目前只支持元、千元、万元、百万元和亿元。"}
    try:
        source_value = Decimal(str(args["source_value"]).replace(",", "").strip())
        claim_text = str(args["claim_value"]).replace(",", "").strip()
        claim_value = Decimal(claim_text)
        precision = len(claim_text.partition(".")[2]) if "." in claim_text else 0
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return {"error": "源数值、主张数值或显示精度无效。"}
    if not source_value.is_finite() or not claim_value.is_finite() or not 0 <= precision <= 8:
        return {"error": "数值必须是有限金额，显示精度须在 0 到 8 位之间。"}
    exact = source_value * _CURRENCY_UNITS[source_unit] / _CURRENCY_UNITS[target_unit]
    quantum = Decimal(1).scaleb(-precision)
    rounded = exact.quantize(quantum, rounding=ROUND_HALF_UP)
    claimed_rounded = claim_value.quantize(quantum, rounding=ROUND_HALF_UP)
    return {
        "source_value": str(source_value), "source_unit": source_unit,
        "target_unit": target_unit, "exact_converted_value": str(exact),
        "rounded_value": str(rounded), "display_decimals": precision,
        "claim_value": str(claim_value), "claim_matches_at_display_precision": rounded == claimed_rounded,
    }


def _tool(
    name: str, arguments: dict[str, Any], *, financial_document_id: str,
    page_count: int, get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from backend.review_database import search_pages as db_search_pages

    if name == "search_financial_report":
        query = str(arguments.get("query", "")).strip()[:200]
        found = db_search_pages(financial_document_id, query, limit=5) if query else []
        items = [{"page": int(p["page"]), "text": str(p["text"])[:2000]} for p in found]
        return {"pages": items, "message": "候选页；引用前确认期间、表头和单位。"}, items
    if name == "read_financial_page":
        try:
            page_number = int(arguments.get("page_number", 0))
            start = max(0, int(arguments.get("start_character", 0)))
        except (TypeError, ValueError):
            return {"error": "页码或起始位置格式无效。"}, []
        if page_number < 1 or page_number > page_count:
            return {"error": "页码超出这份年报范围。"}, []
        text = get_page(page_number)
        if text is None:
            return {"error": "该页没有可提取文字，可能需要人工查看原件。"}, []
        excerpt = text[start:start + MAX_TOOL_PAGE_CHARS]
        end = start + len(excerpt)
        return {"page": page_number, "start_character": start, "text": excerpt, "has_more": end < len(text), "next_start_character": end if end < len(text) else None}, [{"page": page_number, "text": excerpt}]
    if name == "calculate_financial_change":
        return _calculate(arguments), []
    if name == "convert_financial_amount":
        return _convert_financial_amount(arguments), []
    return {"error": "未知工具。"}, []


def _causal_claim_has_only_absence_evidence(claim_text: str, rationale: str) -> bool:
    """Missing an explanation in the annual report does not disprove a cause."""
    causal_claim = bool(re.search(r"(?:原因|因|导致|造成|归因|源于|完全由)", claim_text))
    absence_reason = bool(
        re.search(
            r"未(?:出现|披露|载明|说明|提及|给出).{0,30}(?:原因|归因|因果|解释|表述)"
            r"|未(?:出现|披露|载明|说明|提及).{0,30}(?:汇率|价格|需求|成本)"
            r"|(?:原因|归因|因果|解释|表述).{0,30}未(?:出现|披露|载明|说明|提及)"
            r"|找不到.{0,30}(?:原因|归因|解释|表述)"
            r"|没有.{0,30}(?:归因|解释|说明)",
            rationale,
        )
    )
    return causal_claim and absence_reason


def verify_claim(
    *, claim: dict[str, Any], metadata: dict[str, Any], financial_document_id: str,
    financial_page_count: int, get_page: Callable[[int], str | None],
    search_pages: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
    from backend.review_database import search_pages as db_search_pages

    if claim.get("category") != "financial_fact" or claim.get("claim_type") != "historical_fact":
        return {"outcome": "超出范围", "rationale": "这是一项预测、投资判断或外部信息；当前版本只核对年度财报支持的历史财务事实。", "evidence": [], "calculation": "", "suggestion": "", "trace": _zero_usage_trace(), "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    period = str(claim.get("period", "")).upper().replace(" ", "")
    if re.search(r"(?:\d{2,4}Q[1-4]|Q[1-4]['’]?\d{2,4}|(?:19|20)\d{2}第[一二三四]季度)", period) or "季度" in period:
        return {"outcome": "超出范围", "rationale": "这条说法涉及季度数据；本阶段只按年度财报核查年度历史数据。", "evidence": [], "calculation": "", "suggestion": "请补充相应季度报告后再核对。", "trace": _zero_usage_trace(), "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    if not claim.get("extraction_verified"):
        return {"outcome": "证据不足", "rationale": "模型提取的研报引文未能与对应 PDF 页原文匹配，暂不对主张作判断。", "evidence": [], "calculation": "", "suggestion": "请对照研报原页确认该说法。", "trace": _zero_usage_trace(), "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    analyst_date = str(metadata.get("analyst_publish_date", ""))
    financial_date = str(metadata.get("financial_publish_date", ""))
    if not analyst_date or not financial_date or not metadata.get("version_temporally_confirmed"):
        return {"outcome": "证据不足", "rationale": "研报日期、财报披露日期或适用版本尚未确认；为避免使用研报发布后的信息判旧报告，目前不作确定判断。", "evidence": [], "calculation": "", "suggestion": "补齐两个日期并确认当时可用的财报版本后再运行。", "trace": _zero_usage_trace(), "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    if financial_date > analyst_date:
        return {"outcome": "证据不足", "rationale": "所选财报版本的公开日期晚于研报发表日期，不适合作为判断研报当时说法的依据。", "evidence": [], "calculation": "", "suggestion": "请上传研报发表时已经公开的原始财报版本。", "trace": _zero_usage_trace(), "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

    context = {
        "company": metadata.get("company", ""), "report_year": metadata.get("report_year", ""),
        "analyst_publish_date": metadata.get("analyst_publish_date", ""),
        "financial_publish_date": metadata.get("financial_publish_date", ""),
        "financial_version": metadata.get("financial_version", ""),
        "analyst_file_name": metadata.get("analyst_file_name", ""),
        "financial_file_name": metadata.get("financial_file_name", ""),
        "version_temporally_confirmed": bool(metadata.get("version_temporally_confirmed")),
        "version_date_rule": "若无法确认年报在研报发表前已公开，不得作确定的错误或一致结论；给出证据不足。",
    }
    query = " ".join(str(claim.get(key, "")) for key in ("metric", "period", "claim_text") if claim.get(key))[:240]
    candidates = db_search_pages(financial_document_id, query, limit=4) if query else []
    exposed: dict[int, str] = {}
    candidate_text = []
    for page in candidates:
        number = int(page["page"])
        excerpt = str(page["text"])[:1800]
        if excerpt:
            exposed[number] = excerpt
            candidate_text.append({"page": number, "excerpt": excerpt})
    initial = {
        "task": "核查一条研报主张，不能把缺证当错误。",
        "materials": context,
        "claim": {k: claim.get(k) for k in ("source_page", "source_quote", "claim_text", "metric", "period", "value", "unit", "claim_type")},
        "financial_report_candidate_pages": candidate_text,
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": "请核查这条主张。先检查适用时点与身份，再阅读候选证据；信息不足时可以用工具搜索和分段读取。\n" + json.dumps(initial, ensure_ascii=False)},
    ]
    trace: list[dict[str, Any]] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    program_calculations: list[dict[str, Any]] = []
    program_unit_conversions: list[dict[str, Any]] = []
    client = _client()
    last_content = ""
    try:
        for round_number in range(MAX_TOOL_ROUNDS):
            response = client.chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto",
                response_format={"type": "json_object"}, max_tokens=1500,
                reasoning_effort="low", stream=False,
            )
            used = _usage(response)
            usage["prompt_tokens"] += used["prompt_tokens"]
            usage["completion_tokens"] += used["completion_tokens"]
            if not response.choices:
                raise ModelCallError("DeepSeek 没有返回核查结果。")
            message = response.choices[0].message
            last_content = message.content or ""
            calls = message.tool_calls or []
            messages.append(message.model_dump(exclude_none=True))
            if not calls:
                break
            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("invalid arguments")
                except (json.JSONDecodeError, ValueError):
                    args = {}
                    output, pages = {"error": "工具参数格式无效。"}, []
                else:
                    output, pages = _tool(name, args, financial_document_id=financial_document_id, page_count=financial_page_count, get_page=get_page, search_pages=search_pages)
                    if name == "calculate_financial_change" and "error" not in output:
                        program_calculations.append({"arguments": args, "result": output})
                    if name == "convert_financial_amount" and "error" not in output:
                        program_unit_conversions.append({"arguments": args, "result": output})
                    for evidence in pages:
                        exposed[int(evidence["page"])] = str(evidence["text"])
                result_summary = output if name in {"calculate_financial_change", "convert_financial_amount"} else {"pages": [int(p["page"]) for p in pages], "message": output.get("message", "")}
                trace.append({"tool": name, "arguments": args, "pages": [int(p["page"]) for p in pages], "result": result_summary})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(output, ensure_ascii=False)})
        else:
            messages.append({"role": "user", "content": "请只根据已提供的财报材料给出 JSON 最终结论；不能再调用工具，材料不足就选证据不足。"})
            response = client.chat.completions.create(model=MODEL, messages=messages, response_format={"type": "json_object"}, max_tokens=1500, reasoning_effort="low", stream=False)
            used = _usage(response)
            usage["prompt_tokens"] += used["prompt_tokens"]
            usage["completion_tokens"] += used["completion_tokens"]
            last_content = response.choices[0].message.content or "" if response.choices else ""
    except ModelCallError:
        raise
    except Exception as exc:
        raise _safe_model_error(exc) from exc
    result = _parse_json(last_content)
    outcome = str(result.get("outcome", "证据不足"))
    if outcome not in OUTCOMES:
        outcome = "证据不足"
    evidence: list[dict[str, Any]] = []
    for item in result.get("evidence", [])[:5] if isinstance(result.get("evidence"), list) else []:
        if not isinstance(item, dict):
            continue
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        quote = str(item.get("quote", "")).strip()[:800]
        source_text = get_page(page) if page in exposed else None
        verified = bool(quote and source_text and _norm(quote) in _norm(source_text))
        if verified:
            evidence.append({"page": page, "quote": quote, "quote_verified": True})
    citation_missing = not evidence
    if outcome in {"已核对一致", "发现错误"} and citation_missing:
        outcome = "证据不足"
    if context["analyst_publish_date"] and context["financial_publish_date"]:
        if context["financial_publish_date"] > context["analyst_publish_date"]:
            outcome = "证据不足"
    elif outcome in {"已核对一致", "发现错误"}:
        outcome = "证据不足"
    if not context["version_temporally_confirmed"] and outcome in {"已核对一致", "发现错误"}:
        outcome = "证据不足"
    rationale = str(result.get("rationale", "")).strip()[:1400]
    absence_only_causal_claim = (
        outcome == "发现错误"
        and _causal_claim_has_only_absence_evidence(str(claim.get("claim_text", "")), rationale)
    )
    if absence_only_causal_claim:
        outcome = "证据不足"
        rationale = (
            "年报未提供足以核实这条因果解释的依据；仅凭年报没有这段解释，"
            "不能断言该因果关系一定错误。请补充直接证据或改为待核实说法。"
        )
    if outcome == "证据不足" and result.get("missing"):
        rationale = (rationale + "；还缺少：" + str(result["missing"]).strip())[:1400]
    if citation_missing and outcome == "证据不足":
        rationale = (rationale + "；程序未取得可与本次上传年报逐字匹配的引用，因此不将该说法判为一致或错误。")[:1400]
    calculation_parts = []
    if program_calculations:
        calculation_parts.extend(
            f"程序计算（{item['arguments'].get('unit', '')}）：差额 {item['result'].get('difference')}；同比 {item['result'].get('change_percent')}%"
            for item in program_calculations
        )
    if program_unit_conversions:
        calculation_parts.extend(
            f"程序换算：{item['result'].get('source_value')} {item['result'].get('source_unit')} = {item['result'].get('rounded_value')} {item['result'].get('target_unit')}（未舍入值 {item['result'].get('exact_converted_value')}）；与主张数值{'一致' if item['result'].get('claim_matches_at_display_precision') else '不一致'}"
            for item in program_unit_conversions
        )
    calculation = "；".join(calculation_parts) if calculation_parts else str(result.get("calculation", "")).strip()[:600]
    return {
        "outcome": outcome,
        "rationale": rationale or "目前提供的证据不足以作确定判断。",
        "evidence": evidence,
        "calculation": calculation[:600],
        "suggestion": (
            "请删除未获年报证实的确定性因果表述，或补充可核对的直接证据。"
            if absence_only_causal_claim else str(result.get("suggestion", "")).strip()[:800]
        ),
        "trace": trace + [{"usage": usage}],
        "usage": usage,
    }
