"""Solvency facts with report identity, explicit financial frame, and page evidence."""

from __future__ import annotations

import json
import re
import uuid
from decimal import InvalidOperation
from typing import Any, Callable

from modules.part5_solvency.calculations import normalize_money, parse_decimal


FACT_TOOL_KEYS = {
    "fact_key",
    "label",
    "value",
    "unit",
    "currency",
    "scope",
    "period_type",
    "period_start",
    "period_end",
    "as_of_date",
    "measurement_basis",
    "liability_type",
    "included_fact_keys",
    "evidence",
    "source_context",
    "note",
}

ALLOWED_LIABILITY_TYPES = {
    "financing",
    "operating",
    "cash",
    "cash_restriction",
    "cash_restricted_net",
    "interest_expense",
    "equity",
    "other",
}


def _normalize_quote(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("−", "-").replace("﹣", "-")


def _contains_number(quote: str, value: Any) -> bool:
    try:
        target = parse_decimal(value)
    except (InvalidOperation, ValueError):
        return False
    # Chinese narrative commonly attaches a number directly to a word (e.g.
    # "余额为249,462.64元"). Use numeric boundaries instead of \w boundaries,
    # because Python treats Chinese characters as word characters.
    for token in re.findall(r"(?<![\d.,，])[+-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:\.\d+)?(?![\d.])", quote):
        try:
            if parse_decimal(token) == target:
                return True
        except (InvalidOperation, ValueError):
            continue
    return False


def _verify_evidence(
    evidence: Any,
    value: Any,
    page_count: int,
    get_page: Callable[[int], str | None],
) -> tuple[list[dict[str, Any]], list[str]]:
    checked: list[dict[str, Any]] = []
    problems: list[str] = []
    if not isinstance(evidence, list) or not evidence:
        return checked, ["没有提交页码和原文摘录。"]
    for raw in evidence[:8]:
        if not isinstance(raw, dict):
            problems.append("存在格式错误的证据项。")
            continue
        try:
            page = int(raw.get("page"))
        except (TypeError, ValueError):
            problems.append("证据页码不是整数。")
            continue
        quote = str(raw.get("quote") or "").strip()
        if page < 1 or page > page_count or not quote:
            problems.append(f"第 {page} 页证据缺少页码范围或摘录。")
            continue
        page_text = get_page(page) or ""
        if not _normalize_quote(quote) or _normalize_quote(quote) not in _normalize_quote(page_text):
            problems.append(f"第 {page} 页摘录未能在对应原文中匹配。")
            checked.append({"page": page, "quote": quote[:1000], "matched": False, "role": str(raw.get("role") or "")[:50]})
            continue
        number_present = value is None or _contains_number(quote, value)
        checked.append(
            {
                "page": page,
                "quote": quote[:1000],
                "matched": True,
                "number_present": number_present,
                "role": str(raw.get("role") or "")[:50],
            }
        )
    if not checked:
        problems.append("没有留下可核对的页码证据。")
    if value is not None and not any(item.get("matched") and item.get("number_present") for item in checked):
        problems.append("全部匹配的摘录中都没有找到申报数值。")
    return checked, problems


def _layout_alignment(
    page_texts: list[str],
    row_label: str,
    column_header: str,
    value: Any,
) -> str:
    """Check row/column/value by index or cell geometry in retained PDF tables."""
    row_target = _normalize_quote(row_label)
    header_target = _normalize_quote(column_header)
    try:
        value_target = parse_decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return "unavailable"
    saw_relevant_layout = False
    layouts: list[dict[str, Any]] = []
    for page_text in page_texts:
        for match in re.finditer(r"\[PDF_TABLE_LAYOUT\]\s+page=\d+\s+table=\d+\s+(\{[^\n]+\})", page_text):
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                continue
            normalized_rows = [
                [_normalize_quote(str(cell or "")) for cell in row] if isinstance(row, list) else []
                for row in rows
            ]
            cell_bboxes = payload.get("cell_bboxes")
            if not isinstance(cell_bboxes, list) or len(cell_bboxes) != len(normalized_rows):
                cell_bboxes = []
            page_match = re.search(r"\[PDF_TABLE_LAYOUT\]\s+page=(\d+)", match.group(0))
            table_match = re.search(r"\[PDF_TABLE_LAYOUT\]\s+page=\d+\s+table=(\d+)", match.group(0))
            layouts.append({
                "page": int(page_match.group(1)) if page_match else None,
                "table": int(table_match.group(1)) if table_match else None,
                "rows": normalized_rows,
                "cell_bboxes": cell_bboxes,
            })

    for layout in layouts:
        for row_index, row in enumerate(layout["rows"]):
            label_matches = any(
                # Substring matching confuses parent and child totals (for
                # example, "负债合计" with "流动负债合计"). The model must
                # name the exact row retained in the PDF table matrix.
                row_target and row_target == cell
                for cell in row if cell
            )
            if not label_matches:
                continue
            saw_relevant_layout = True
            for header_layout in layouts:
                same_page = header_layout["page"] == layout["page"]
                same_table = header_layout["table"] == layout["table"]
                page_gap = (
                    layout["page"] is not None
                    and header_layout["page"] is not None
                    and abs(layout["page"] - header_layout["page"])
                )
                has_geometry = bool(layout["cell_bboxes"] and header_layout["cell_bboxes"])
                # A separate table on the same page may use similar headings.
                # Only same-table layouts can establish row/column alignment.
                if not same_table or (not same_page and (not page_gap or page_gap > (2 if has_geometry else 1))):
                    continue
                for header_row_index, header_row in enumerate(header_layout["rows"][:6]):
                    columns = {
                        column for column, cell in enumerate(header_row)
                        if header_target and header_target in cell
                    }
                    if not columns or len(header_row) != len(row):
                        continue
                    if not same_page and not has_geometry and (
                        len(header_row) < 3
                        or _normalize_quote("项目") not in header_row
                    ):
                        continue
                    matching_value_cells: list[int] = []
                    for value_column, cell in enumerate(row):
                        if not cell:
                            continue
                        try:
                            if parse_decimal(cell) == value_target:
                                matching_value_cells.append(value_column)
                        except (InvalidOperation, ValueError):
                            continue
                    for value_column in matching_value_cells:
                        if has_geometry:
                            row_boxes = layout["cell_bboxes"][row_index]
                            header_boxes = header_layout["cell_bboxes"][header_row_index]
                            value_box = row_boxes[value_column] if value_column < len(row_boxes) else None
                            aligned_columns: set[int] = set()
                            for column in columns:
                                header_box = header_boxes[column] if column < len(header_boxes) else None
                                if not value_box or not header_box:
                                    continue
                                try:
                                    overlap = max(0.0, min(float(value_box[2]), float(header_box[2])) - max(float(value_box[0]), float(header_box[0])))
                                    minimum_width = min(float(value_box[2]) - float(value_box[0]), float(header_box[2]) - float(header_box[0]))
                                except (TypeError, ValueError, IndexError):
                                    continue
                                if minimum_width > 0 and overlap >= minimum_width * 0.8:
                                    aligned_columns.add(column)
                            if len(aligned_columns) == 1 and next(iter(aligned_columns)) in columns:
                                return "confirmed"
                        elif value_column in columns:
                            return "confirmed"
    return "mismatch" if saw_relevant_layout else "unavailable"


def _verify_source_context(
    raw: Any,
    evidence: list[dict[str, Any]],
    get_page: Callable[[int], str | None],
    value: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Record the model's semantic review and verify its declared source anchors exist."""
    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get("kind") or "unknown").strip().lower()
    semantic_status = str(raw.get("semantic_status") or "unknown").strip().lower()
    if kind not in {"table", "narrative", "unknown"}:
        kind = "unknown"
    if semantic_status not in {"confirmed", "ambiguous", "unknown"}:
        semantic_status = "unknown"

    context = {
        "kind": kind,
        "row_label": str(raw.get("row_label") or "").strip()[:160],
        "column_header": str(raw.get("column_header") or "").strip()[:160],
        "unit_label": str(raw.get("unit_label") or "").strip()[:100],
        "semantic_status": semantic_status,
        "review_note": str(raw.get("review_note") or "").strip()[:500],
    }
    problems: list[str] = []
    matched_quotes = [item["quote"] for item in evidence if item.get("matched")]
    quote_norm = _normalize_quote("\n".join(matched_quotes))
    page_numbers = sorted({int(item["page"]) for item in evidence if item.get("matched")})
    full_source = "\n".join(get_page(page) or "" for page in page_numbers)
    layout_alignment = "unavailable"

    if kind == "table":
        for key, label in (("row_label", "报表行名"), ("column_header", "列标题"), ("unit_label", "单位标签")):
            anchor_value = context[key]
            if not anchor_value:
                problems.append(f"表格事实缺少{label}。")
            elif _normalize_quote(anchor_value) not in quote_norm:
                problems.append(f"声明的{label}未在已提交证据摘录中找到。")
        if context["row_label"] and _normalize_quote(context["row_label"]) not in quote_norm:
            problems.append("表格行名没有包含在事实摘录中。")
        # Plain-text extraction drops blank cells. In a comparative statement,
        # a row with one amount has no deterministic current/prior-column mapping.
        normalized_source = _normalize_quote(full_source)
        years = set(re.findall(r"20\d{2}(?:年(?:\d{1,2}月\d{1,2}日)?|年度)", normalized_source))
        comparative_headers_present = (
            "期末余额" in normalized_source and "期初余额" in normalized_source
        ) or (
            "期末数" in normalized_source and "期初数" in normalized_source
        ) or (
            "本期期末" in normalized_source and "上期期末" in normalized_source
        )
        cited_page_texts = [
            get_page(int(item["page"])) or ""
            for item in evidence
            if item.get("matched") and item.get("page") is not None
        ]
        layout_alignment = _layout_alignment(
            cited_page_texts or [full_source], context["row_label"], context["column_header"], value
        )
        context["layout_alignment"] = layout_alignment
        if layout_alignment == "mismatch":
            context["semantic_status"] = "ambiguous"
            semantic_status = "ambiguous"
            problems.append("保留的 PDF 表格布局与申报的行、列和值不一致。")
        elif layout_alignment == "unavailable":
            context["semantic_status"] = "ambiguous"
            semantic_status = "ambiguous"
            problems.append("保留的 PDF 表格布局中未能定位该行列和值，无法确认申报金额的财务语义。")
        for page_text in cited_page_texts:
            if _normalize_quote(context["row_label"]) not in _normalize_quote(page_text):
                continue
            lines = page_text.splitlines()
            for index, line in enumerate(lines):
                if _normalize_quote(context["row_label"]) not in _normalize_quote(line):
                    continue
                row_lines: list[str] = []
                for candidate in lines[index : index + 12]:
                    stripped = candidate.strip()
                    if not stripped:
                        continue
                    normalized = _normalize_quote(stripped)
                    if row_lines and re.search(r"[\u4e00-\u9fff]", stripped) and not normalized.startswith(("七(", "七（", "其中", "单位", "项目", "附注")):
                        break
                    row_lines.append(stripped)
                financial_tokens = re.findall(r"(?<![\d.,])[+-]?(?:\d{1,3}(?:[,，]\d{3})+|\d+)\.\d+(?!\d)", " ".join(row_lines))
                if (len(years) >= 2 or comparative_headers_present) and len(financial_tokens) == 1 and layout_alignment != "confirmed":
                    context["semantic_status"] = "ambiguous"
                    problem = "比较报表该行只提取到一个金额，纯文本已丢失空白单元格位置，无法确认本期/比较期列归属。"
                    if problem not in problems:
                        problems.append(problem)
                break
    elif kind == "narrative":
        if not context["review_note"]:
            problems.append("叙述性事实需说明如何从原文确认主体、期间和含义。")
    else:
        problems.append("事实来源类型未确认。")

    if semantic_status == "ambiguous":
        problems.append("模型标记该事实的报表语义或列归属有歧义。")
    elif semantic_status != "confirmed":
        problems.append("模型尚未确认事实的行列、单位、期间、范围和会计含义。")
    if not context["review_note"]:
        problems.append("缺少行列、单位、期间、范围和会计含义的核对说明。")

    if kind == "table":
        context["declared_anchors_present"] = all(
            bool(context[key]) and _normalize_quote(context[key]) in quote_norm
            for key in ("row_label", "column_header", "unit_label")
        )
    else:
        context["declared_anchors_present"] = kind == "narrative" and bool(context["review_note"])
    context["calculation_eligible"] = not problems
    context["problems"] = problems
    return context, problems


def add_facts(
    raw_facts: Any,
    *,
    facts: dict[str, dict[str, Any]],
    report_id: str,
    page_count: int,
    get_page: Callable[[int], str | None],
) -> dict[str, Any]:
    if not isinstance(raw_facts, list) or not raw_facts:
        raise ValueError("facts 必须是非空列表。")

    new_facts: list[dict[str, Any]] = []
    key_to_id: dict[str, str] = {
        str(item.get("fact_key")): fact_id
        for fact_id, item in facts.items()
        if item.get("fact_key")
    }
    seen_keys: set[str] = set()
    for index, raw in enumerate(raw_facts[:100], start=1):
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("fact_key") or f"fact_{uuid.uuid4().hex[:10]}").strip()[:100]
        if key in seen_keys:
            raise ValueError(f"本批 fact_key 重复：{key}")
        seen_keys.add(key)
        fact_id = key_to_id.get(key) or f"solv_{uuid.uuid4().hex[:14]}"
        key_to_id[key] = fact_id
        label = str(raw.get("label") or "").strip()[:160]
        if not label:
            raise ValueError("每条事实必须提供 label。")
        value_raw = raw.get("value")
        if value_raw is not None:
            value_raw = str(value_raw).strip()[:100]
            try:
                parse_decimal(value_raw)
            except (InvalidOperation, ValueError):
                raise ValueError(f"{label} 的 value 不是可识别的纯数字；区间/文字请作为 note 或单独事实。")

        period_type = str(raw.get("period_type") or "unknown")
        if period_type not in {"instant", "duration", "maturity_range", "unknown"}:
            raise ValueError(f"{label} 的 period_type 不受支持。")
        liability_type = str(raw.get("liability_type") or "other")
        if liability_type not in ALLOWED_LIABILITY_TYPES:
            raise ValueError(f"{label} 的 liability_type 不受支持。")
        checked, evidence_problems = _verify_evidence(raw.get("evidence"), value_raw, page_count, get_page)
        source_context, source_context_problems = _verify_source_context(
            raw.get("source_context"), checked, lambda page: get_page(page) or "", value_raw
        )
        if value_raw is None and source_context.get("kind") == "table":
            problem = "表格事实未提交明确数值；空白单元格或未识别金额不能自动解释为零。"
            source_context_problems.append(problem)
            source_context["calculation_eligible"] = False
            if source_context.get("semantic_status") == "confirmed":
                source_context["semantic_status"] = "unknown"
            if problem not in source_context["problems"]:
                source_context["problems"].append(problem)
        number_present_on_match = any(item.get("matched") and item.get("number_present") for item in checked)
        matched_number = value_raw is None or number_present_on_match
        evidence_matched = bool(checked) and not evidence_problems and matched_number
        status = (
            "quote_and_number_matched"
            if value_raw is not None and evidence_matched
            else "quote_matched"
            if value_raw is None and evidence_matched
            else "unmatched"
        )
        normalized_value, normalization_note = normalize_money(value_raw, raw.get("unit"), raw.get("currency")) if value_raw is not None else (None, None)

        new_facts.append(
            {
                "fact_id": fact_id,
                "fact_key": key,
                "report_id": report_id,
                "label": label,
                "original_label": label,
                "value": value_raw,
                "unit": str(raw.get("unit") or "")[:60],
                "currency": str(raw.get("currency") or "unknown")[:40],
                "normalized_value": normalized_value,
                "normalized_unit": "CNY" if normalized_value is not None else None,
                "scope": str(raw.get("scope") or "unknown")[:80],
                "period_type": period_type,
                "period_start": str(raw.get("period_start") or "")[:30],
                "period_end": str(raw.get("period_end") or "")[:30],
                "as_of_date": str(raw.get("as_of_date") or "")[:30],
                "measurement_basis": str(raw.get("measurement_basis") or "")[:100],
                "liability_type": liability_type,
                "included_fact_keys": [str(value)[:100] for value in raw.get("included_fact_keys", [])[:100]]
                if isinstance(raw.get("included_fact_keys"), list)
                else [],
                "evidence": checked,
                "validation": {
                    "status": status,
                    "quote_matched": bool(checked) and all(item.get("matched") for item in checked),
                    "number_appears_in_matching_quote": number_present_on_match if value_raw is not None else None,
                    "row_column_unit_scope_identity_checked": False,
                    "human_reviewed": False,
                    "problems": evidence_problems,
                    "normalization_note": normalization_note,
                    "semantic_context_status": (
                        "confirmed"
                        if source_context.get("calculation_eligible")
                        else "ambiguous"
                        if source_context.get("semantic_status") == "ambiguous"
                        else "unconfirmed"
                    ),
                    "semantic_context_problems": source_context_problems,
                    "calculation_eligible": bool(source_context.get("calculation_eligible")) and evidence_matched,
                },
                "source_context": source_context,
                "revision": int(facts.get(fact_id, {}).get("revision", 0)) + 1,
                "note": str(raw.get("note") or "")[:1000],
            }
        )

    for item in new_facts:
        included_keys = item.pop("included_fact_keys")
        unknown_children = [key for key in included_keys if key not in key_to_id]
        item["included_fact_ids"] = [key_to_id[key] for key in included_keys if key in key_to_id]
        if unknown_children:
            item["validation"]["status"] = "unmatched"
            item["validation"]["problems"].append("组成项没有在本次事实中出现：" + ", ".join(unknown_children))
        facts[item["fact_id"]] = item

    return {
        "accepted_count": len(new_facts),
        "evidence_match_count": sum(
            item["validation"]["status"] in {"quote_matched", "quote_and_number_matched"} for item in new_facts
        ),
        "unmatched_count": sum(item["validation"]["status"] == "unmatched" for item in new_facts),
        "fact_ids": [item["fact_id"] for item in new_facts],
        "facts": new_facts,
        "fact_keys": key_to_id,
    }
