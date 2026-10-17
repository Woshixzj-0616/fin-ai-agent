"""V3.0.1 shared-module adapter for cashflow and profit-realization analysis."""

from __future__ import annotations

from typing import Any

from backend.core.context import ReportContext

from .agent import MODULE_ID, MODULE_VERSION, _initial_pages, analyze_cashflow


def run(context: ReportContext) -> dict[str, Any]:
    """Analyze one report while retaining the V3.0.1 run(context) contract."""
    if not context.initial_pages:
        context.initial_pages = _initial_pages(context)
    return analyze_cashflow(context)
