"""解析候选、冲突和下一步动作；供核查行与报告共用。"""
from __future__ import annotations

import re

from audit.grounding import explicit_companies


RESOLUTION_LABELS = {
    "unique": "唯一解析",
    "candidates": "候选待定",
    "materials_missing": "材料未加载",
    "ambiguous_company": "多公司歧义",
    "period_conflict": "期间冲突",
    "unit_conflict": "单位冲突",
    "scope_conflict": "口径冲突",
    "not_evaluated": "未进入解析",
}

_NUMBER_UNIT = re.compile(
    r"([-+−]?\d[\d,，]*(?:\.\d+)?)\s*(亿元|万元|千元|百万元|元/股|元／股|元|亿|万|百分点|%|％)"
)
_SCOPE_PARENT_SHAREHOLDERS = re.compile(
    r"归母|归属于(?:上市公司|母公司|本行|本公司)?(?:普通股)?股东|归属于.*?股东(?:的)?(?:净利润|净资产|权益)"
)
_SCOPE_PARENT_COMPANY = re.compile(r"母公司(?:报表|口径)?")
_SCOPE_CONSOLIDATED = re.compile(r"合并(?:报表|口径|范围)|集团口径")


def _normal_number(value: str) -> str:
    return value.replace(",", "").replace("，", "").replace("−", "-").lstrip("+")


def _loaded(code: str, facts: list[dict] | None) -> bool:
    return any(fact.get("company_code") == code for fact in facts or [])


def company_candidates(text: str, facts: list[dict] | None,
                       codes: set[str] | None = None) -> list[dict]:
    """只列原文中出现的公司；codes 用于缩小到某次主体绑定涉及的候选。"""
    rows = []
    seen = set()
    for mention in explicit_companies(text, facts):
        if codes is not None and mention["code"] not in codes:
            continue
        if mention["code"] in seen:
            continue
        seen.add(mention["code"])
        rows.append({
            "code": mention["code"],
            "name": mention["name"],
            "matched": mention["matched"],
            "basis": "source_mention",
            "loaded": _loaded(mention["code"], facts),
        })
    return rows


def source_scope(text: str) -> dict | None:
    """原文明确口径；归母股东口径优先于同一句里的“合并报表”上下文。"""
    parent = _SCOPE_PARENT_SHAREHOLDERS.search(text or "")
    if parent:
        return {"scope": "parent_shareholders", "matched": parent.group(0)}
    parent_company = _SCOPE_PARENT_COMPANY.search(text or "")
    if parent_company:
        return {"scope": "parent_company", "matched": parent_company.group(0)}
    consolidated = _SCOPE_CONSOLIDATED.search(text or "")
    if consolidated:
        return {"scope": "consolidated", "matched": consolidated.group(0)}
    return None


def unit_candidates(quote: str, value) -> list[dict]:
    """逐字找与模型 value 相同的数字及其相邻单位，不跨数值借用。"""
    if value is None:
        return []
    expected = _normal_number(str(value))
    rows = []
    for number, unit in _NUMBER_UNIT.findall(quote or ""):
        if _normal_number(number) != expected:
            continue
        rows.append({"unit": unit, "matched": f"{number}{unit}"})
    return rows


def _action(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def resolution_for_subject(subject: dict, *, source_text: str,
                           facts: list[dict] | None) -> dict:
    """从 resolve_subject 的结构生成用户可读候选、冲突和动作。"""
    codes = set(subject.get("candidate_codes") or subject.get("sentence_codes") or [])
    if subject.get("code"):
        codes.add(subject["code"])
    candidates = company_candidates(source_text, facts, codes)
    conflicts = []
    basis = subject.get("basis")
    if subject.get("code"):
        status = "unique"
        action = None
        if subject.get("conflict") and subject.get("model_code"):
            conflicts.append({
                "field": "company",
                "source_value": [{"code": subject["code"], "matched": subject.get("matched")}],
                "model_value": subject["model_code"],
                "basis": basis,
                "source_text": (subject.get("matched") or "")[:120],
            })
            action = _action("use_source_company", "已按原文公司解析；模型字段差异已留痕")
    elif basis and basis.startswith("ambiguous_"):
        status = "ambiguous_company"
        action = _action("select_company", "同句出现多家公司，请拆分句子或选择唯一主体")
    elif basis in {"company_not_in_loaded_materials"} or subject.get("note") == "句内公司不在本次已加载材料中":
        status = "materials_missing" if candidates else "candidates"
        missing = "、".join(row["code"] for row in candidates) or subject.get("matched") or "该公司"
        action = _action("load_materials", f"先加载 {missing} 对应材料后再核查")
    else:
        status = "candidates"
        action = _action("complete_company", "补全公司名称、年份或上下文，或转人工复核")
    return {
        "status": status,
        "label": RESOLUTION_LABELS[status],
        "candidates": candidates,
        "conflicts": conflicts,
        "next_action": action,
    }


def resolution_for_subjects(subjects: list[dict], *, source_text: str,
                            facts: list[dict] | None) -> dict:
    """跨公司比较：合并两侧候选；任一侧不唯一时整体不唯一。"""
    rows = [resolution_for_subject(subject, source_text=source_text, facts=facts)
            for subject in subjects]
    statuses = [row["status"] for row in rows]
    priority = ("ambiguous_company", "materials_missing", "candidates", "unique")
    status = next((name for name in priority if name in statuses), "candidates")
    candidates = []
    conflicts = []
    seen = set()
    for row in rows:
        for candidate in row["candidates"]:
            if candidate["code"] not in seen:
                seen.add(candidate["code"])
                candidates.append(candidate)
        conflicts.extend(row["conflicts"])
    action = rows[0]["next_action"] if len(rows) == 1 else next(
        (row["next_action"] for row in rows if row["status"] != "unique"), None)
    return {
        "status": status,
        "label": RESOLUTION_LABELS[status],
        "candidates": candidates,
        "conflicts": conflicts,
        "next_action": action,
    }


def mark_conflict(resolution: dict, *, status: str, field: str, source_value,
                  model_value, basis: str, source_text: str,
                  action_code: str, action_message: str) -> None:
    resolution["status"] = status
    resolution["label"] = RESOLUTION_LABELS[status]
    resolution["conflicts"].append({
        "field": field,
        "source_value": source_value,
        "model_value": model_value,
        "basis": basis,
        "source_text": (source_text or "")[:160],
    })
    resolution["next_action"] = _action(action_code, action_message)
