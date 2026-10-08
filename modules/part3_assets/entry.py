"""Registered-compatible module three entry point; the registry change belongs to task 00."""

from __future__ import annotations

from typing import Any

from backend.core.context import ReportContext
from modules.part3_assets.agent import MODULE_ID, MODULE_VERSION, analyze_asset_report


def run(context: ReportContext) -> dict[str, Any]:
    return analyze_asset_report(context)

