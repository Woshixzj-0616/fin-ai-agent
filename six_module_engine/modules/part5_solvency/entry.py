"""V3.0.1 registry-compatible entry point for solvency analysis."""

from __future__ import annotations

from typing import Any

from backend.core.context import ReportContext
from modules.part5_solvency.agent import MODULE_VERSION, run as run_agent
from modules.part5_solvency.retrieval import select_initial_pages


MODULE_ID = "solvency"


def run(context: ReportContext) -> dict[str, Any]:
    """Run the independent debt and funding-pressure workflow."""
    if not context.initial_pages:
        context.initial_pages = select_initial_pages(context.pages)
    result = run_agent(context)
    result["result"]["prompt_version"] = MODULE_VERSION
    return result
