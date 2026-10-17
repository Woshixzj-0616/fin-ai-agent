"""Module-two pipeline boundary consumed by the V3.0.1 registry adapter."""

from __future__ import annotations

from typing import Any, Callable

from modules.part2_profit.engine import analyze_profit_report as _analyze
from modules.part2_profit.version import MODULE_VERSION


def analyze_profit_report(**kwargs: Any) -> dict[str, Any]:
    workflow = _analyze(**kwargs)
    result = workflow.get("result")
    if isinstance(result, dict):
        result.setdefault("schema_version", "profit_analysis_v2")
        result.setdefault("module_version", MODULE_VERSION)
    return workflow


def answer_profit_question(**kwargs: Any) -> dict[str, Any]:
    from modules.part2_profit.engine import answer_profit_question as _answer

    return _answer(**kwargs)
