from __future__ import annotations

import json
from typing import Any


def _merge_interpretation(
    extraction_data: dict[str, Any],
    interpretation_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge analysis updates without losing entries that cannot be matched safely."""
    merged_data = dict(extraction_data)
    interpretation_review_items: list[dict[str, str]] = []
    for source_key, target_key, match_key in (
        ("industry_context_analysis", "industry_context", "topic"),
        ("strategy_analysis", "strategy_competitiveness", "aspect"),
    ):
        updates = interpretation_data.get(source_key, [])
        if not isinstance(updates, list):
            if updates not in (None, ""):
                interpretation_review_items.append(
                    {
                        "section": source_key,
                        "label": "字段格式错误",
                        "analysis": json.dumps(updates, ensure_ascii=False),
                        "limitation": "",
                        "reason": "解释字段不是预期的对象数组；原内容保留待复核。",
                    }
                )
            continue
        source_items = merged_data.get(target_key, [])
        if not isinstance(source_items, list):
            source_items = []
        source_labels = [
            str(item.get(match_key, "")).strip()
            for item in source_items
            if isinstance(item, dict) and str(item.get(match_key, "")).strip()
        ]
        source_label_counts = {label: source_labels.count(label) for label in set(source_labels)}
        source_label_set = set(source_labels)
        valid_updates: dict[str, dict[str, Any]] = {}
        duplicate_labels: set[str] = set()
        for item in updates:
            if not isinstance(item, dict):
                interpretation_review_items.append(
                    {
                        "section": source_key,
                        "label": "条目格式错误",
                        "analysis": json.dumps(item, ensure_ascii=False),
                        "limitation": "",
                        "reason": "解释数组包含非对象条目；原内容保留待复核。",
                    }
                )
                continue
            label = str(item.get(match_key, "")).strip()
            if not label or label not in source_label_set:
                interpretation_review_items.append(
                    {
                        "section": source_key,
                        "label": label or "未提供匹配名称",
                        "analysis": str(item.get("analysis", "")),
                        "limitation": str(item.get("limitation", "")),
                        "reason": "未找到名称完全相同的事实提取条目；此解释未合并进已引用的结论。",
                    }
                )
                continue
            if source_label_counts[label] > 1:
                interpretation_review_items.append(
                    {
                        "section": source_key,
                        "label": label,
                        "analysis": str(item.get("analysis", "")),
                        "limitation": str(item.get("limitation", "")),
                        "reason": "事实提取阶段有多个同名条目，无法确定该解释对应哪一项；待复核。",
                    }
                )
                continue
            if label in duplicate_labels:
                interpretation_review_items.append(
                    {
                        "section": source_key,
                        "label": label,
                        "analysis": str(item.get("analysis", "")),
                        "limitation": str(item.get("limitation", "")),
                        "reason": "解释阶段对同一名称返回多条更新，无法安全确定采用哪条；均待复核。",
                    }
                )
                continue
            if label in valid_updates:
                duplicate_labels.add(label)
                interpretation_review_items.append(
                    {
                        "section": source_key,
                        "label": label,
                        "analysis": str(item.get("analysis", "")),
                        "limitation": str(item.get("limitation", "")),
                        "reason": "解释阶段对同一名称返回多条更新，无法安全确定采用哪条；均待复核。",
                    }
                )
                previous = valid_updates.pop(label)
                interpretation_review_items.append(
                    {
                        "section": source_key,
                        "label": label,
                        "analysis": str(previous.get("analysis", "")),
                        "limitation": str(previous.get("limitation", "")),
                        "reason": "解释阶段对同一名称返回多条更新，无法安全确定采用哪条；均待复核。",
                    }
                )
            else:
                valid_updates[label] = item
        merged_data[target_key] = [
            {
                **item,
                **{
                    field: (
                        str(valid_updates.get(str(item.get(match_key, "")).strip(), {}).get(field, "")).strip()
                        or item.get(field, "")
                    )
                    for field in ("analysis", "limitation")
                },
            }
            if isinstance(item, dict)
            else item
            for item in source_items
        ]
    if interpretation_review_items:
        merged_data["interpretation_review_items"] = interpretation_review_items
    for key in ("growth_drivers", "follow_up_checks", "topics"):
        value = interpretation_data.get(key)
        if isinstance(value, list):
            merged_data[key] = value
    for key in ("summary", "summary_source_pages", "summary_evidence_quote"):
        if key in interpretation_data:
            merged_data[key] = interpretation_data[key]
    interpretation_uncertainties = interpretation_data.get("uncertainties", [])
    if isinstance(interpretation_uncertainties, list):
        merged_data["uncertainties"] = list(merged_data.get("uncertainties", [])) + interpretation_uncertainties
    return merged_data
