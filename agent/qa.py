"""受限问答：只基于已抽取 facts 回答，答案必挂 evidence_id。

铁律（与全仓一致）
------------------
- 数值只来自本地 tools（find_evidence / compute_yoy），模型不写数；
- 无证据就说「证据不足」，不猜、不补；
- LLM 只在确定性匹配失败时做问题→意图解析，解析结果仍交给本地工具出数；
- `confirmed_error` / `evidence_supported` 不在问答里产生（那是 compare_claim 的事）。
"""
from __future__ import annotations

import json
import re

from llm_check import METRICS
from tools import compute_yoy, find_evidence

# 问题类型：value=查数 yoy=同比 issues=异常 overview=概览
KINDS = ("value", "yoy", "issues", "overview")

_YEAR = re.compile(r"(20\d{2})")
_YOY_CUE = re.compile(r"同比|增长|增幅|降幅|变化|yoy|YoY|回落|上升|下降", re.IGNORECASE)
_ISSUE_CUE = re.compile(r"问题|异常|issue|错误|风险|瑕疵|待复核|不一致")
_OVERVIEW_CUE = re.compile(r"哪些|有什么|列表|概览|总览|一览|整体|全部指标|分析结果")


def match_metric(question: str) -> str | None:
    """最长别名命中指标键。别名来自 METRICS，不另建词表。"""
    best, best_len = None, 0
    for key, aliases in METRICS.items():
        for alias in (key, *aliases):
            if alias and alias in question and len(alias) > best_len:
                best, best_len = key, len(alias)
    return best


def match_year(question: str, facts: list[dict] | None = None) -> int | None:
    m = _YEAR.search(question)
    if m:
        return int(m.group(1))
    years = [f.get("period_year") for f in (facts or []) if f.get("period_year")]
    return int(max(years)) if years else None


def match_kind(question: str) -> str:
    if _YOY_CUE.search(question):
        return "yoy"
    if _ISSUE_CUE.search(question):
        return "issues"
    if _OVERVIEW_CUE.search(question) and not re.search(r"多少|是啥|是多少|几多", question):
        return "overview"
    return "value"


def match_intent(question: str, facts: list[dict] | None = None) -> dict:
    """确定性解析：指标 + 年度 + 问题类型。匹配不上就交 LLM。"""
    return {
        "metric": match_metric(question),
        "period_year": match_year(question, facts),
        "kind": match_kind(question),
        "source": "deterministic",
    }


def parse_intent_with_llm(question: str, facts: list[dict], client, run) -> dict:
    """LLM 只做问题→意图翻译，不回答、不写数。失败退回 deterministic。"""
    catalog = sorted(METRICS)
    system = (
        "你是问题解析器。把用户问题解析成 JSON，不要回答问题本身，不要输出任何数字结论。\n"
        "输出格式（只输出 JSON）：\n"
        '{"metric": "目录键或null", "period_year": 2024或null, "kind": "value|yoy|issues|overview"}\n'
        f"指标目录：{json.dumps(catalog, ensure_ascii=False)}\n"
        "kind 含义：value=查某指标数值；yoy=问同比/增减；issues=问问题/异常；overview=要指标概览。\n"
        "指标对不上就填 null，禁止硬凑。"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question[:500]},
    ]
    try:
        raw = client.chat(messages, run)
        # 只认整段 JSON，不从自由文本里抠
        payload = json.loads(raw.strip())
        if not isinstance(payload, dict):
            raise TypeError("非对象")
    except Exception:  # noqa: BLE001 — LLM 任意异常都退回确定性解析，不外抛
        return match_intent(question, facts)
    metric = payload.get("metric")
    if metric not in METRICS:
        metric = None
    kind = payload.get("kind")
    if kind not in KINDS:
        kind = match_kind(question)
    year = payload.get("period_year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    return {"metric": metric, "period_year": year or match_year(question, facts),
            "kind": kind, "source": "llm"}


def _cite(fact: dict) -> dict:
    return {
        "evidence_id": fact["evidence_id"],
        "metric": fact.get("metric"),
        "metric_name": fact.get("metric_name") or fact.get("metric"),
        "period_year": fact.get("period_year"),
        "value": fact.get("value"),
        "unit": fact.get("unit"),
        "page": fact.get("page"),
        "page_image": fact.get("page_image"),
    }


def _ok(answer: str, cites: list[dict], intent: dict, **extra) -> dict:
    return {
        "status": "ok",
        "answer": answer,
        "evidence_ids": [c["evidence_id"] for c in cites],
        "citations": cites,
        "intent": intent,
        **extra,
    }


def _no_evidence(reason: str, intent: dict) -> dict:
    return {
        "status": "insufficient_evidence",
        "answer": reason,
        "evidence_ids": [],
        "citations": [],
        "intent": intent,
    }


def _company_of(facts: list[dict]) -> str:
    for f in facts:
        code = f.get("company_code")
        if code:
            return str(code)
    return ""


def _fmt_pct(val) -> str:
    """同比百分比展示：四舍五入两位，去掉多余 0。"""
    try:
        from decimal import ROUND_HALF_UP, Decimal
        d = Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        s = format(d, "f").rstrip("0").rstrip(".")
        return s or "0"
    except Exception:  # noqa: BLE001 — 展示层兜底，绝不因格式化炸问答
        return str(val)


def _answer_value(intent: dict, facts: list[dict], draft: str = "") -> dict:
    metric, year = intent.get("metric"), intent.get("period_year")
    if not metric:
        return {"status": "out_of_scope",
                "answer": "未识别出指标，本问答只答已抽取的财务指标（营收/净利/现金流等）。",
                "evidence_ids": [], "citations": [], "intent": intent}
    if not year:
        return _no_evidence("未识别出年度，无法查数。请写清「哪年·哪个指标」。", intent)
    out = find_evidence(facts, company_name_or_code=_company_of(facts),
                        metric=metric, period_year=int(year), draft=draft)
    if out.get("status") != "ok":
        return _no_evidence(
            f"证据不足：未找到 {year} 年「{METRICS.get(metric, [metric])[0]}」的唯一年报证据，不猜数值。",
            intent)
    fact = next(f for f in facts if f["evidence_id"] == out["items"][0]["evidence_id"])
    cite = _cite(fact)
    name = cite["metric_name"]
    unit = cite.get("unit") or ""
    scope = fact.get("scope") or "unknown"
    scope_txt = {"consolidated": "合并", "parent_shareholders": "归母",
                 "parent_company": "母公司"}.get(scope, scope)
    page = cite.get("page")
    page_txt = f"（PDF 第 {page} 页）" if page else ""
    answer = f"{year}年{name}为 {cite.get('value')} {unit}，口径：{scope_txt}{page_txt}。"
    return _ok(answer, [cite], intent)


def _answer_yoy(intent: dict, facts: list[dict], draft: str = "") -> dict:
    metric, year = intent.get("metric"), intent.get("period_year")
    if not metric or not year:
        return _no_evidence("未识别出指标或年度，无法算同比。", intent)
    out = compute_yoy(facts, company_name_or_code=_company_of(facts),
                      metric=metric, year=int(year), draft=draft)
    if out.get("status") != "ok" or not out.get("current_evidence_id"):
        return _no_evidence(
            f"证据不足：{year} 年「{METRICS.get(metric, [metric])[0]}」同比无法用已加载证据复算。",
            intent)
    yoy = out.get("yoy") or {}
    cur_id = out.get("current_evidence_id")
    prev_id = out.get("previous_evidence_id")
    cites = []
    for eid in (cur_id, prev_id):
        if not eid:
            continue
        fact = next((f for f in facts if f.get("evidence_id") == eid), None)
        if fact:
            cites.append(_cite(fact))
    name = METRICS.get(metric, [metric])[0]
    if yoy.get("status") == "ok":
        val = _fmt_pct(yoy.get("value"))
        calc = yoy.get("formula") or yoy.get("calculation") or ""
        answer = f"{year}年{name}同比 {val}%。"
        if calc:
            answer += f"计算式：{calc}。"
    else:
        answer = f"{year}年{name}同比未能复算（{yoy.get('reason') or yoy.get('status')}），证据见引用。"
    return _ok(answer, cites, intent)


def _answer_issues(intent: dict, facts: list[dict]) -> dict:
    flagged = [f for f in facts if f.get("issues")]
    if not flagged:
        return _ok("已加载证据未带问题标记（issues 为空），不代表业务无风险，仅表示抽取层干净。", [], intent)
    cites = [_cite(f) for f in flagged[:20]]
    lines = [f"· {c['metric_name']}（{c['period_year']}）：{'；'.join(str(x) for x in next(f['issues'] for f in flagged if f['evidence_id'] == c['evidence_id']))}"
             for c in cites]
    answer = f"抽取层标记 {len(flagged)} 条待复核：\n" + "\n".join(lines)
    return _ok(answer, cites, intent, flagged_count=len(flagged))


def _answer_overview(intent: dict, facts: list[dict]) -> dict:
    if not facts:
        return _no_evidence("本次未抽到任何证据。", intent)
    # 每指标取最新一年、非 before
    picked: dict[str, dict] = {}
    for f in facts:
        if f.get("adjustment") == "before":
            continue
        key = f.get("metric") or ""
        cur = picked.get(key)
        if cur is None or (f.get("period_year") or 0) > (cur.get("period_year") or 0):
            picked[key] = f
    if not picked:
        return _no_evidence("证据均带 before 调整标记，无法汇总。", intent)
    cites = [_cite(f) for f in picked.values()]
    lines = [f"· {c['metric_name']}（{c['period_year']}）：{c.get('value')} {c.get('unit') or ''}"
             for c in cites]
    answer = f"本次共 {len(facts)} 条证据，指标概览：\n" + "\n".join(lines)
    return _ok(answer, cites, intent, total_facts=len(facts))


def answer(question: str, facts: list[dict], *, client=None, run=None,
           draft: str = "", use_llm: bool = True) -> dict:
    """受限问答主入口。

    - 确定性解析优先；失败且给了 client 才走 LLM 意图解析；
    - 正文一律本地拼装，数值只出自 find_evidence / compute_yoy；
    - 返回值必含 evidence_ids（可为空，但 status 必须是 insufficient_evidence）。
    """
    q = (question or "").strip()
    if not q:
        return {"status": "out_of_scope", "answer": "问题为空。",
                "evidence_ids": [], "citations": [], "intent": {}}
    if len(q) > 500:
        return {"status": "out_of_scope", "answer": "问题超过 500 字，请收窄。",
                "evidence_ids": [], "citations": [], "intent": {}}
    if not facts:
        return {"status": "insufficient_evidence",
                "answer": "尚未加载任何证据，请先分析一份年报。", "evidence_ids": [], "citations": [],
                "intent": {}}

    intent = match_intent(q, facts)
    if use_llm and client is not None and run is not None and not intent.get("metric"):
        intent = parse_intent_with_llm(q, facts, client, run)

    kind = intent.get("kind") or "value"
    if kind == "yoy":
        out = _answer_yoy(intent, facts, draft)
    elif kind == "issues":
        out = _answer_issues(intent, facts)
    elif kind == "overview":
        out = _answer_overview(intent, facts)
    else:
        out = _answer_value(intent, facts, draft)
    # 铁律：凡 status=ok 必须挂 evidence_id（issues/overview 允许 citations 为空的「干净」结论）
    if out.get("status") == "ok" and kind in ("value", "yoy") and not out.get("evidence_ids"):
        return _no_evidence("证据不足：结论无法落到 evidence_id。", intent)
    return out
