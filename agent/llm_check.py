"""旧导入兼容门面：实现源位于 ``audit`` 包。"""
from audit.schema import (
    COMPANY_ALIASES,
    COMPANIES,
    CLAIM_TYPES,
    DEFAULT_TOLERANCE_PCT,
    MAX_ITEMS,
    METRIC_NOTE,
    METRICS,
    OPERATORS,
    SECRET_PATTERN,
    UNITS,
    LLMError,
    companies_from_facts,
    load_companies,
    schema,
    validate_schema,
)
from audit.prompt import FUZZY, FORECAST, MAX_DRAFT_CHARS, SYSTEM_PROMPT
from audit.client import LLMClient, NoRedirect
from audit.grounding import resolve_company, split_draft
from audit.claim_check import _tool_verdict_rows, check_one_claim, check_payload
from audit.report import render_report
from audit.service import check_text
