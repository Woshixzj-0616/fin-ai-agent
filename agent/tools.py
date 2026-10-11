"""旧导入兼容门面：实现源位于 ``audit.tools`` 包。"""
from audit.tools.specs import TOOL_NAMES, tool_specs
from audit.tools.evidence import (
    _err,
    _fact_identity,
    _fact_quality,
    _pick_fact,
    find_evidence,
    list_catalog,
)
from audit.tools.compute import _norm_value, _one_fact, compute_trend, compute_yoy
from audit.tools.compare import compare_claim, compare_companies
from audit.tools.search import search_text
from audit.tools.dispatch import dispatch
