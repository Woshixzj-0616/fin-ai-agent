"""从文字层年报提取财务证据：兼容门面。

实现已迁移到 :mod:`extraction`。本模块只保留历史导入路径，保证旧调用方、
测试和脚本无需同步改名。
"""
# ruff: noqa: I001, PLC0414
from __future__ import annotations

from extraction import (
    ADJUDICABLE as ADJUDICABLE,
    ALIASES as ALIASES,
    CONSOLIDATED_METRICS as CONSOLIDATED_METRICS,
    METRICS as METRICS,
    PARENT_METRICS as PARENT_METRICS,
    REQUIRED as REQUIRED,
    TOTAL_REV_LABEL_RE as TOTAL_REV_LABEL_RE,
    UNIT_IN_LABEL_RE as UNIT_IN_LABEL_RE,
    _adjustment_cell_for as _adjustment_cell_for,
    _apply_scope as _apply_scope,
    _decimal_cell as _decimal_cell,
    _dedupe as _dedupe,
    _emit_words_fact as _emit_words_fact,
    _reported_yoy_for as _reported_yoy_for,
    _scanned_pdf_hint as _scanned_pdf_hint,
    _statement_title as _statement_title,
    amount_cell as amount_cell,
    box as box,
    cell_bbox as cell_bbox,
    corroborate_scope as corroborate_scope,
    default_scope as default_scope,
    document_currency as document_currency,
    extract_material as extract_material,
    extract_total_revenue as extract_total_revenue,
    extract_via_words as extract_via_words,
    extract_words_document as extract_words_document,
    header_for as header_for,
    label_for as label_for,
    metric_for as metric_for,
    parse_table as parse_table,
    statement_sections as statement_sections,
    table_cells as table_cells,
)
