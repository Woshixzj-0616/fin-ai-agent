"""Stable fact-role mapping and report-scope selection for Module 2."""

from __future__ import annotations

import re
from typing import Any, Callable


def infer_profit_role(label: Any) -> str:
    raw = re.sub(r"\s+", "", str(label or ""))
    context = "".join(re.findall(r"[（(](.*?)[）)]", raw))
    text = re.sub(r"[（(].*?[）)]", "", raw)
    if "其中：" in text or "其中:" in text:
        detail = re.split(r"其中[：:]", text, maxsplit=1)[1]
        if detail.startswith("营业收入"):
            return "operating_revenue"
        if detail.startswith("营业成本"):
            return "operating_cost"
        if "营业总收入项下" in context and detail.startswith("利息收入"):
            return "interest_income"
        return "unmapped"
    if "归属于母公司" in raw and "非经常性损益" in raw and "合计" in raw:
        return "nonrecurring_parent_net"
    if "所得税影响" in text:
        return "nonrecurring_tax_effect"
    if "少数股东权益影响" in text or "少数股东影响" in text:
        return "nonrecurring_minority_effect"
    if "非经常性损益" in text and "净利润" not in text:
        return "nonrecurring_item"
    if "营业总收入项下" in context and "利息收入" in text:
        return "interest_income"
    if text.startswith(("其中：", "其中:")):
        detail = re.sub(r"^其中[：:]", "", text)
        if detail == "营业收入":
            return "operating_revenue"
        # Statement-note subtotals such as interest expense/income are already
        # included in financial expense and must not enter the profit bridge.
        return "unmapped"
    if "营业总收入" in text:
        return "total_operating_revenue"
    if "营业总成本" in text:
        return "total_operating_cost"
    if "营业收入" in text:
        return "operating_revenue"
    if text in {"利息收入", "营业总收入其中利息收入"}:
        return "interest_income"
    if "营业成本" in text:
        return "operating_cost"
    if "税金及附加" in text:
        return "tax_and_surcharges"
    if "销售费用" in text:
        return "selling_expense"
    if "管理费用" in text:
        return "administrative_expense"
    if "研发费用" in text or "研究开发费用" in text:
        return "research_development_expense"
    if "财务费用" in text:
        return "financial_expense"
    if "其他收益" in text:
        return "other_income"
    if "投资收益" in text and not text.startswith("其中"):
        return "investment_income"
    if "公允价值变动收益" in text:
        return "fair_value_change"
    if "信用减值损失" in text:
        return "credit_impairment_loss"
    if "资产减值损失" in text:
        return "asset_impairment_loss"
    if "资产处置收益" in text:
        return "asset_disposal_income"
    if "营业利润" in text:
        return "operating_profit"
    if "营业外收入" in text:
        return "non_operating_income"
    if "营业外支出" in text:
        return "non_operating_expense"
    if "利润总额" in text:
        return "profit_before_tax"
    if "所得税费用" in text:
        return "income_tax_expense"
    if "少数股东损益" in text:
        return "minority_profit"
    if "扣除非经常性损益" in text and "净利润" in text:
        return "deducted_parent_profit"
    if "归属于母公司" in text and "净利润" in text:
        return "parent_profit"
    if "净利润" in text:
        return "net_profit"
    return "unmapped"


_ROLE_LABELS = {
    "operating_revenue": "营业收入",
    "operating_cost": "营业成本",
    "tax_and_surcharges": "税金及附加",
    "selling_expense": "销售费用",
    "administrative_expense": "管理费用",
    "research_development_expense": "研发费用",
    "financial_expense": "财务费用",
    "other_income": "其他收益",
    "investment_income": "投资收益",
    "fair_value_change": "公允价值变动收益",
    "credit_impairment_loss": "信用减值损失",
    "asset_impairment_loss": "资产减值损失",
    "asset_disposal_income": "资产处置收益",
    "operating_profit": "营业利润",
    "non_operating_income": "营业外收入",
    "non_operating_expense": "营业外支出",
    "profit_before_tax": "利润总额",
    "income_tax_expense": "所得税费用",
    "net_profit": "净利润",
    "parent_profit": "归属于母公司股东的净利润",
    "minority_profit": "少数股东损益",
}


def _statement_label_priority(row: dict[str, Any], role: str) -> int:
    label = str(row.get("label", ""))
    if "利润表" in label and "附注" not in label:
        return 30
    canonical = _ROLE_LABELS.get(role)
    if not canonical:
        return 0
    # The exact profit-statement row is often prefixed by a Chinese statement
    # operator (e.g. "减：") or "其中：". Note disclosures and sub-item tables
    # have additional words and must not override that row.
    normalized = re.sub(r"\s+", "", label)
    normalized = re.sub(r"^[一二三四五六七八九十0-9、.．]+", "", normalized)
    normalized = re.sub(r"^(?:加|减|其中)[：:]", "", normalized)
    normalized = re.sub(r"[（(].*?[）)]", "", normalized)
    if normalized == canonical:
        return 20
    if normalized.startswith(canonical) and not any(marker in normalized for marker in ("明细", "其中", "分类", "变动", "构成", "情况")):
        return 10
    return 0


def select_unique_line(
    rows: list[dict[str, Any]], role: str, *, scope: str | None,
    scope_family: Callable[[Any], str],
) -> dict[str, Any] | None:
    matches = [
        row for row in rows
        if row.get("profit_role") == role and row.get("value_status") == "可计算"
        and (scope is None or scope_family(row.get("reporting_scope")) == scope)
    ]
    if not matches:
        return None
    signatures = {
        (
            row.get("current_yuan"), row.get("previous_yuan"), row.get("reporting_scope"),
            row.get("previous_reporting_scope"), row.get("currency"), row.get("previous_currency"),
            row.get("current_period"), row.get("previous_period"),
        )
        for row in matches
    }
    if len(signatures) == 1:
        return matches[0]

    # A primary statement line is authoritative for calculation when the same
    # concept also appears in a note with a narrower or differently grouped
    # amount (for example, operating revenue vs. main-business revenue).
    # If no explicit statement row exists, preserve the conflict as unresolved.
    priorities = {id(row): _statement_label_priority(row, role) for row in matches}
    best_priority = max(priorities.values(), default=0)
    statement_rows = [row for row in matches if priorities[id(row)] == best_priority and best_priority > 0]
    statement_signatures = {
        (
            row.get("current_yuan"), row.get("previous_yuan"), row.get("reporting_scope"),
            row.get("previous_reporting_scope"), row.get("currency"), row.get("previous_currency"),
            row.get("current_period"), row.get("previous_period"),
        )
        for row in statement_rows
    }
    return statement_rows[0] if len(statement_signatures) == 1 else None
