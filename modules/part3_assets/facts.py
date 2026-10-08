"""Candidate asset facts and conservative, source-backed evidence checks."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


ALLOWED_METRICS = {
    "total_assets", "current_assets", "noncurrent_assets", "cash_and_equivalents",
    "accounts_receivable", "notes_receivable", "receivables_financing", "contract_assets",
    "other_receivables", "prepayments", "inventory", "inventory_component", "fixed_assets",
    "construction_in_progress", "goodwill", "intangible_assets", "right_of_use_assets",
    "investment_property", "long_term_equity_investment", "other_asset", "operating_revenue",
    "operating_cost", "asset_impairment_loss", "credit_impairment_loss", "other",
}

ALLOWED_BASES = {"gross", "allowance", "net", "carrying_value", "cost", "impairment", "amount", "unknown"}

METRIC_LABELS = {
    "资产总计": "total_assets", "资产合计": "total_assets",
    "流动资产合计": "current_assets", "非流动资产合计": "noncurrent_assets",
    "货币资金": "cash_and_equivalents", "应收账款": "accounts_receivable",
    "应收票据": "notes_receivable", "应收款项融资": "receivables_financing",
    "合同资产": "contract_assets", "其他应收款": "other_receivables",
    "预付款项": "prepayments", "存货": "inventory",
    "固定资产": "fixed_assets", "在建工程": "construction_in_progress",
    "商誉": "goodwill", "无形资产": "intangible_assets",
    "使用权资产": "right_of_use_assets", "投资性房地产": "investment_property",
    "长期股权投资": "long_term_equity_investment", "营业收入": "operating_revenue",
    "营业总收入": "operating_revenue", "主营业务收入": "operating_revenue",
    "营业成本": "operating_cost", "主营业务成本": "operating_cost",
    "资产减值损失": "asset_impairment_loss", "信用减值损失": "credit_impairment_loss",
}

INVENTORY_LABELS = {"原材料", "在产品", "半成品", "库存商品", "包装材料", "委托加工物资", "发出商品"}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s\u3000]+", "", text).casefold()


def _plain_number(value: Any) -> str:
    return normalize_text(value).replace(",", "").replace("，", "")


def _text_has_value(text: str, value: Any) -> bool:
    candidate = _plain_number(value)
    if not candidate:
        return False
    source = _plain_number(text)
    if candidate in source:
        return True
    if candidate.startswith("(") and candidate.endswith(")"):
        return "-" + candidate[1:-1] in source
    return False


def _page_map(context: Any) -> dict[int, str]:
    return {int(page["page"]): str(page.get("text") or "") for page in context.pages if page.get("page") is not None}


def _metric_key(raw_key: Any, label: str) -> tuple[str, bool]:
    key = str(raw_key or "other").strip().lower()
    if key in ALLOWED_METRICS and key != "other":
        return key, False
    normalized_label = normalize_text(label)
    if normalized_label in METRIC_LABELS:
        return METRIC_LABELS[normalized_label], key != METRIC_LABELS[normalized_label]
    for marker, mapped in (
        ("应收款项融资", "receivables_financing"), ("其他应收款", "other_receivables"),
        ("应收账款", "accounts_receivable"), ("应收票据", "notes_receivable"),
        ("合同资产", "contract_assets"), ("存货", "inventory"),
        ("固定资产", "fixed_assets"), ("在建工程", "construction_in_progress"),
        ("商誉", "goodwill"), ("无形资产", "intangible_assets"),
    ):
        if marker in normalized_label:
            return mapped, key != mapped
    if normalized_label in INVENTORY_LABELS:
        return "inventory_component", key != "inventory_component"
    if key in ALLOWED_METRICS:
        return key, False
    return "other", bool(key and key != "other")


def _measurement_basis(raw_basis: Any, metric_key: str, label: str, page_context: str) -> tuple[str, str]:
    basis = str(raw_basis or "unknown").strip().lower()
    if basis in ALLOWED_BASES and basis != "unknown":
        return basis, ""
    text = normalize_text(label)
    if "坏账准备" in text or "存货跌价准备" in text or "减值准备" in text or "累计折旧" in text:
        return "allowance", "根据科目名称识别为准备余额"
    if "减值损失" in text:
        return "impairment", "根据科目名称识别为本期减值损益"
    if "账面余额" in text or "原值" in text:
        return "gross", "根据科目名称识别为账面总额或原值"
    if "账面价值" in text or "净额" in text or "账面净值" in text:
        return "net", "根据科目名称识别为账面净额"
    if metric_key in {"operating_revenue", "operating_cost", "asset_impairment_loss", "credit_impairment_loss"}:
        return "amount", "期间损益按发生额记录"
    in_balance_sheet = "资产负债表" in normalize_text(page_context)
    if metric_key == "total_assets":
        return "carrying_value", "资产总计按报表列示金额记录"
    if metric_key in {"accounts_receivable", "inventory"} and in_balance_sheet:
        return "net", "资产负债表科目按扣除相关准备后的列示金额记录"
    if metric_key in {
        "current_assets", "noncurrent_assets", "cash_and_equivalents", "notes_receivable",
        "receivables_financing", "contract_assets", "other_receivables", "prepayments",
        "fixed_assets", "construction_in_progress", "goodwill", "intangible_assets",
        "right_of_use_assets", "investment_property", "long_term_equity_investment", "other_asset",
    } and in_balance_sheet:
        return "carrying_value", "资产负债表科目按列示账面价值记录"
    return "unknown", ""


def _nearby_page_context(page_number: int, page_texts: dict[int, str]) -> str:
    # Tables commonly put the title, currency, and column dates on adjacent pages.
    return "\n".join(page_texts.get(number, "") for number in (page_number - 1, page_number, page_number + 1))


def _unit_supported(unit: str, text: str) -> bool:
    normalized_unit = normalize_text(unit).replace("人民币", "").replace("cny", "").replace("rmb", "")
    normalized_text = normalize_text(text)
    if not normalized_unit:
        return False
    if normalized_unit == "元":
        return bool(re.search(r"(?:金额)?单位[:：]?为?(?:人民币)?元|币种[:：]?人民币", normalized_text))
    if normalized_unit in {"千元", "万元", "亿元"}:
        return bool(re.search(rf"(?:金额)?单位[:：]?为?(?:人民币)?{re.escape(normalized_unit)}|[（(](?:人民币)?{re.escape(normalized_unit)}[）)]", normalized_text))
    return normalized_unit in normalized_text


def _period_supported(original: dict[str, Any], text: str) -> bool:
    normalized_text = normalize_text(text)
    period_type = str(original.get("period_type") or "").strip().lower()
    fields = ("as_of_date", "period_end") if period_type == "instant" else ("period_start", "period_end")
    dates = [str(original.get(field) or "")[:10] for field in fields]
    dates = [value for value in dates if re.match(r"^\d{4}-\d{2}-\d{2}$", value)]
    if not dates:
        return False
    if period_type == "instant" and len(set(dates)) > 1:
        return False
    matched_dates = 0
    for value in dates:
        year, month, day = value.split("-")
        variants = (value, f"{year}年{int(month)}月{int(day)}日", f"{year}年{month}月{day}日")
        if any(normalize_text(variant) in normalized_text for variant in variants):
            matched_dates += 1
            continue
        if period_type == "flow" and year in normalized_text and any(term in normalized_text for term in ("年度", "本期", "上期")):
            matched_dates += 1
    if period_type == "instant":
        return matched_dates >= 1
    return matched_dates == len(dates)


def _scope_supported(scope: str, text: str) -> bool:
    normalized_scope = normalize_text(scope)
    normalized_text = normalize_text(text)
    if normalized_scope in {"合并", "合并财务报表", "consolidated"}:
        standalone = re.search(r"合并(?:资产负债表|利润表|现金流量表|财务报表|报表项目注释)", normalized_text)
        if standalone:
            return True
        # Some issuers put consolidated and parent-company figures side by side
        # under a joint title (e.g. "合并及公司资产负债表"). Require both that
        # title and explicit repeated column labels before accepting the scope.
        joint_statement = re.search(r"合并(?:及|和)(?:母公司|公司)(?:资产负债表|利润表|现金流量表)", normalized_text)
        joint_headers = re.search(r"合并{1,2}(?:母公司|公司){1,2}", normalized_text)
        return bool(joint_statement and joint_headers and "合并" in joint_headers.group(0))
    if normalized_scope in {"母公司", "母公司财务报表", "parent"}:
        if "母公司" in normalized_text or "公司本部" in normalized_text:
            return True
        joint_statement = re.search(r"合并(?:及|和)(?:母公司|公司)(?:资产负债表|利润表|现金流量表)", normalized_text)
        joint_headers = re.search(r"合并{1,2}(?:母公司|公司){1,2}", normalized_text)
        return bool(joint_statement and joint_headers and re.search(r"(?:母公司|公司)", joint_headers.group(0)))
    return bool(normalized_scope and normalized_scope in normalized_text)


def validate_asset_facts(raw_facts: Any, context: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Preserve candidate values, annotating what exact page text does and does not verify."""
    if not isinstance(raw_facts, list):
        return [], ["模型未返回资产事实列表。"]
    page_count = int(context.page_count)
    page_texts = _page_map(context)
    accepted: list[dict[str, Any]] = []
    review_notes: list[str] = []
    for index, original in enumerate(raw_facts[:240], start=1):
        if not isinstance(original, dict):
            continue
        value = str(original.get("value") or "").strip()[:80]
        label = str(original.get("original_label") or "").strip()[:160]
        raw_key = str(original.get("metric_key") or "other").strip().lower()
        metric_key, metric_inferred = _metric_key(raw_key, label)
        unit = str(original.get("unit") or "").strip()[:60]
        raw_basis = str(original.get("measurement_basis") or "unknown").strip().lower()
        if raw_basis not in ALLOWED_BASES:
            raw_basis = "unknown"
        evidence_items = original.get("evidence", [])
        if isinstance(evidence_items, dict):
            evidence_items = [evidence_items]
        if not isinstance(evidence_items, list):
            evidence_items = []
        verified_evidence: list[dict[str, Any]] = []
        quote_texts: list[str] = []
        role_texts: dict[str, list[str]] = {}
        cited_page_numbers: set[int] = set()
        all_quotes_match = bool(evidence_items)
        for citation in evidence_items[:8]:
            if not isinstance(citation, dict):
                continue
            try:
                page_number = int(citation.get("page"))
            except (TypeError, ValueError):
                all_quotes_match = False
                continue
            quote = str(citation.get("quote") or "").strip()[:1_000]
            role = str(citation.get("role") or "support").strip().lower()[:30]
            page_text = page_texts.get(page_number, "") if 1 <= page_number <= page_count else ""
            matched = bool(quote and normalize_text(quote) in normalize_text(page_text))
            if not matched:
                all_quotes_match = False
            else:
                quote_texts.append(quote)
                canonical_role = role if role in {"label", "value", "unit", "currency", "period", "scope"} else "support"
                role_texts.setdefault(canonical_role, []).append(quote)
                cited_page_numbers.add(page_number)
            verified_evidence.append({"page": page_number, "quote": quote, "role": role, "quote_matched": matched})

        cited_text = "\n".join(quote_texts)
        page_context = "\n".join(_nearby_page_context(page, page_texts) for page in sorted(cited_page_numbers))
        evidence_context = f"{cited_text}\n{page_context}"
        basis, basis_note = _measurement_basis(raw_basis, metric_key, label, page_context)
        raw_dimension = str(original.get("dimension") or "").strip()
        dimension = label if metric_key == "inventory_component" and not raw_dimension else raw_dimension[:180]
        value_in_quote = _text_has_value(cited_text, value)
        label_in_quote = bool(label and normalize_text(label) in normalize_text(cited_text))
        period_evidence_text = "\n".join(role_texts.get("period", []))
        unit_evidence_text = "\n".join(role_texts.get("unit", []))
        currency_evidence_text = "\n".join(role_texts.get("currency", []) + role_texts.get("unit", []))
        scope_evidence_text = "\n".join(role_texts.get("scope", []))
        unit_in_quote = _unit_supported(unit, f"{unit_evidence_text}\n{evidence_context}")
        period_in_quote = _period_supported(original, f"{period_evidence_text}\n{evidence_context}")
        currency = str(original.get("currency") or "unknown").strip()
        currency_context = normalize_text(f"{currency_evidence_text}\n{evidence_context}")
        currency_code = currency.upper()
        if currency_code in {"CNY", "RMB", "人民币"}:
            currency_supported = any(token in currency_context for token in ("人民币", "cny", "rmb", "¥", "￥"))
        elif currency_code in {"", "UNKNOWN", "N/A", "NA"}:
            currency_supported = False
        else:
            currency_supported = currency_code.casefold() in currency_context
        scope = str(original.get("reporting_scope") or "unknown").strip()
        scope_supported = _scope_supported(scope, f"{scope_evidence_text}\n{evidence_context}")
        normalization_notes = []
        if metric_inferred:
            normalization_notes.append(f"指标键由 {raw_key} 按原科目名规范为 {metric_key}")
        if raw_basis != basis and basis_note:
            normalization_notes.append(f"计量基础由 {raw_basis} 按原科目和报表页判断为 {basis}（{basis_note}）")
        value_status = "value_and_label_quoted" if value_in_quote and label_in_quote else "needs_source_or_row_review"
        if not value:
            value_status = "missing_value"
        fact = {
            "fact_id": f"assets-f{index:04d}",
            "metric_key": metric_key,
            "original_label": label,
            "dimension": dimension,
            "value": value,
            "unit": unit,
            "currency": currency[:24],
            "period_type": str(original.get("period_type") or "unknown").strip().lower()[:24],
            "period_label": str(original.get("period_label") or "").strip()[:100],
            "period_start": str(original.get("period_start") or "").strip()[:20],
            "period_end": str(original.get("period_end") or "").strip()[:20],
            "as_of_date": str(original.get("as_of_date") or "").strip()[:20],
            "reporting_scope": scope[:40],
            "measurement_basis": basis,
            "adjustment_basis": str(original.get("adjustment_basis") or "unknown").strip()[:40],
            "evidence": verified_evidence,
            "validation": {
                "all_quotes_matched_page": all_quotes_match,
                "value_found_in_matched_quote": value_in_quote,
                "original_label_found_in_matched_quote": label_in_quote,
                "unit_supported_by_cited_pages": unit_in_quote,
                "period_supported_by_cited_pages": period_in_quote,
                "currency_supported_by_unit_or_currency_quote": currency_supported,
                "scope_supported_by_cited_pages": scope_supported,
                "value_status": value_status,
                "row_column_semantics_independently_verified": False,
            },
            "normalization_notes": normalization_notes,
            "extraction_note": str(original.get("note") or "").strip()[:400],
        }
        fact["calculation_ready"] = bool(
            fact["validation"]["all_quotes_matched_page"]
            and fact["validation"]["value_found_in_matched_quote"]
            and fact["validation"]["original_label_found_in_matched_quote"]
            and fact["validation"]["unit_supported_by_cited_pages"]
            and fact["validation"]["period_supported_by_cited_pages"]
            and fact["validation"]["currency_supported_by_unit_or_currency_quote"]
            and fact["validation"]["scope_supported_by_cited_pages"]
            and fact["metric_key"] != "other"
            and fact["measurement_basis"] != "unknown"
        )
        if not fact["calculation_ready"]:
            review_notes.append(f"{fact['fact_id']}（{label or metric_key}）来源摘录、数字或科目行需复核。")
        if value:
            accepted.append(fact)
    return accepted, review_notes


def merge_asset_facts(existing: list[dict[str, Any]], additional: Any, context: Any) -> tuple[list[dict[str, Any]], list[str]]:
    validated, notes = validate_asset_facts(additional, context)
    def identity(fact: dict[str, Any]) -> tuple[str, ...]:
        return (
            str(fact.get("metric_key") or ""),
            normalize_text(fact.get("original_label")),
            normalize_text(fact.get("dimension")),
            _plain_number(fact.get("value")),
            str(fact.get("measurement_basis") or ""),
            str(fact.get("reporting_scope") or ""),
            str(fact.get("currency") or ""),
            str(fact.get("as_of_date") or fact.get("period_end") or fact.get("period_label") or ""),
        )

    seen = {identity(fact) for fact in existing}
    for fact in validated:
        key = identity(fact)
        if key in seen:
            notes.append(f"{fact.get('original_label') or fact.get('metric_key')} 已登记同口径事实，未重复加入。")
            continue
        fact["fact_id"] = f"assets-f{len(existing) + 1:04d}"
        existing.append(fact)
        seen.add(key)
    return existing, notes
