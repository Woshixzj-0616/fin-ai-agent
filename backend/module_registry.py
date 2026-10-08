"""Professional module entry points for the modular local workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.core.context import ReportContext


@dataclass(frozen=True)
class ModuleRegistration:
    module_id: str
    version: str
    run: Callable[[ReportContext], dict[str, Any]]


def registrations() -> dict[str, ModuleRegistration]:
    from modules.part1_business.entry import MODULE_ID as business_id
    from modules.part1_business.entry import MODULE_VERSION as business_version
    from modules.part1_business.entry import run as business_run
    from modules.part2_profit.entry import MODULE_ID as profit_id
    from modules.part2_profit.entry import MODULE_VERSION as profit_version
    from modules.part2_profit.entry import run as profit_run
    from modules.part3_assets.entry import MODULE_ID as assets_id
    from modules.part3_assets.entry import MODULE_VERSION as assets_version
    from modules.part3_assets.entry import run as assets_run
    from modules.part4_cashflow.entry import MODULE_ID as cashflow_id
    from modules.part4_cashflow.entry import MODULE_VERSION as cashflow_version
    from modules.part4_cashflow.entry import run as cashflow_run
    from modules.part5_solvency.entry import MODULE_ID as solvency_id
    from modules.part5_solvency.entry import MODULE_VERSION as solvency_version
    from modules.part5_solvency.entry import run as solvency_run
    from modules.part6_disclosure.entry import MODULE_ID as disclosure_id
    from modules.part6_disclosure.entry import MODULE_VERSION as disclosure_version
    from modules.part6_disclosure.entry import run as disclosure_run

    return {
        business_id: ModuleRegistration(business_id, business_version, business_run),
        profit_id: ModuleRegistration(profit_id, profit_version, profit_run),
        assets_id: ModuleRegistration(assets_id, assets_version, assets_run),
        cashflow_id: ModuleRegistration(cashflow_id, cashflow_version, cashflow_run),
        solvency_id: ModuleRegistration(solvency_id, solvency_version, solvency_run),
        disclosure_id: ModuleRegistration(disclosure_id, disclosure_version, disclosure_run),
    }
