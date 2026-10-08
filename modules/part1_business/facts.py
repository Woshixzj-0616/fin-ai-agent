"""Stable identities for business-module revenue tables and evidence."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().casefold()


def _stable_id(prefix: str, parts: list[Any]) -> str:
    identity = json.dumps(
        [_normalized(part) for part in parts], ensure_ascii=False, separators=(",", ":")
    )
    return f"{prefix}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def _attach_record_evidence_ids(record: dict[str, Any], report_id: str, fact_type: str) -> None:
    fact_id = str(record.get("fact_id") or "")
    quote = str(record.get("evidence_quote") or "").strip()
    if quote:
        record["evidence_id"] = _stable_id(
            "bizevidence",
            [report_id, fact_id, ",".join(str(page) for page in record.get("source_pages", [])), quote],
        )
    evidence_items = record.get("evidence")
    if isinstance(evidence_items, list):
        for evidence in evidence_items:
            if not isinstance(evidence, dict) or not evidence.get("evidence_quote"):
                continue
            evidence["evidence_id"] = _stable_id(
                "bizevidence",
                [
                    report_id,
                    fact_id,
                    fact_type,
                    evidence.get("role", ""),
                    ",".join(str(page) for page in evidence.get("source_pages", [])),
                    evidence["evidence_quote"],
                ],
            )
    components = record.get("components")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            component["fact_id"] = _stable_id(
                "bizfact",
                [
                    report_id,
                    fact_id,
                    "component",
                    component.get("name", ""),
                    component.get("current_value", ""),
                    ",".join(str(page) for page in component.get("source_pages", [])),
                ],
            )
            for evidence in component.get("evidence", []):
                if not isinstance(evidence, dict) or not evidence.get("evidence_quote"):
                    continue
                evidence["evidence_id"] = _stable_id(
                    "bizevidence",
                    [
                        report_id,
                        component["fact_id"],
                        fact_type,
                        evidence.get("role", ""),
                        ",".join(str(page) for page in evidence.get("source_pages", [])),
                        evidence["evidence_quote"],
                    ],
                )


def attach_table_identities(result: dict[str, Any], report_id: str) -> None:
    """Add stable table/evidence IDs without changing legacy result fields.

    A named table is grouped by report, model-reported title, verified header,
    split dimension, original revenue metric, scope, currency, and period. This ID supports separation and lookup;
    it does not prove the model transcribed the table title correctly. When no
    title is returned, a weaker inferred identity is explicitly marked.
    """
    records: list[tuple[str, dict[str, Any]]] = []
    primary = result.get("revenue_total")
    if isinstance(primary, dict):
        records.append(("revenue_total", primary))
    for key in ("revenue_totals", "revenue_segments"):
        value = result.get(key)
        if isinstance(value, list):
            records.extend((key, item) for item in value if isinstance(item, dict))

    for record_type, item in records:
        table_name = str(item.get("table_name") or "").strip()
        table_dimension = str(item.get("table_dimension") or item.get("basis") or "").strip()
        metric = str(item.get("metric_name") or "").strip()
        scope = str(item.get("reporting_scope") or "").strip()
        currency = str(item.get("currency") or "").strip()
        period = str(item.get("current_period") or "").strip()
        verified_headers = sorted(
            {
                _normalized(evidence.get("evidence_quote", ""))
                for evidence in item.get("evidence", [])
                if isinstance(evidence, dict)
                and str(evidence.get("role") or "").casefold() == "table_header"
                and evidence.get("quote_verified")
                and evidence.get("evidence_quote")
            }
        )
        if table_name:
            identity_parts = [
                report_id, table_name, "|".join(verified_headers), table_dimension,
                metric, scope, currency, period,
                item.get("current_unit", ""),
            ]
            if not verified_headers:
                identity_parts.extend(
                    [
                        item.get("basis", ""),
                        table_dimension,
                        ",".join(str(page) for page in item.get("source_pages", [])),
                    ]
                )
            title_evidence_verified = any(
                str(evidence.get("role") or "").casefold() == "table_title"
                and evidence.get("quote_verified")
                and _normalized(table_name) in _normalized(evidence.get("evidence_quote", ""))
                for evidence in item.get("evidence", [])
                if isinstance(evidence, dict)
            )
            item["table_identity_status"] = (
                "report_title_text_verified" if title_evidence_verified else "model_reported_title_unverified"
            )
        else:
            identity_parts = [
                report_id,
                metric,
                item.get("basis", ""),
                scope,
                currency,
                period,
                ",".join(str(page) for page in item.get("source_pages", [])),
            ]
            item["table_identity_status"] = "inferred_title_missing"
        item["table_id"] = _stable_id("biztable", identity_parts)
        item["fact_id"] = _stable_id(
            "bizfact",
            [
                report_id,
                item["table_id"],
                record_type,
                item.get("name", ""),
                table_dimension,
                item.get("metric_name", ""),
                item.get("current_value", ""),
                item.get("current_period", ""),
            ],
        )
        _attach_record_evidence_ids(item, report_id, record_type)
        calculation_ids: dict[str, str] = {}
        if item.get("calculated_change") is not None:
            calculation_ids["period_change"] = _stable_id(
                "bizcalc", [report_id, item["fact_id"], "period_change"]
            )
        if item.get("total_origin") == "derived_from_reported_complete_segments":
            calculation_ids["component_sum"] = _stable_id(
                "bizcalc", [report_id, item["fact_id"], "component_sum"]
            )
        for period in ("current", "previous"):
            if item.get(f"{period}_share_percent") is not None:
                calculation_ids[f"{period}_share"] = _stable_id(
                    "bizcalc", [report_id, item["fact_id"], f"{period}_share"]
                )
        if item.get("share_change_percentage_points") is not None:
            calculation_ids["share_change"] = _stable_id(
                "bizcalc", [report_id, item["fact_id"], "share_change"]
            )
        if calculation_ids:
            item["calculation_ids"] = calculation_ids

    metric_bridges = result.get("revenue_metric_bridges")
    if isinstance(metric_bridges, list):
        for bridge in metric_bridges:
            if not isinstance(bridge, dict):
                continue
            bridge["calculation_id"] = _stable_id(
                "bizcalc",
                [
                    report_id,
                    bridge.get("table_name", ""),
                    bridge.get("table_dimension", ""),
                    bridge.get("higher_metric", ""),
                    bridge.get("higher_value", ""),
                    bridge.get("lower_metric", ""),
                    bridge.get("lower_value", ""),
                    bridge.get("current_period", ""),
                    "metric_bridge",
                ],
            )

    fact_groups = (
        "business_flow",
        "growth_drivers",
        "industry_context",
        "strategy_competitiveness",
        "topics",
        "dependencies",
        "risk_factors",
        "major_changes",
        "industry_metrics",
        "follow_up_checks",
    )
    identity_fields = (
        "stage", "driver", "topic", "aspect", "title", "name", "risk", "change", "question"
    )
    for group in fact_groups:
        records_in_group = result.get(group)
        if not isinstance(records_in_group, list):
            continue
        for item in records_in_group:
            if not isinstance(item, dict):
                continue
            identity = [item.get(field, "") for field in identity_fields if item.get(field)]
            item["fact_id"] = _stable_id(
                "bizfact",
                [report_id, group, *identity, ",".join(str(page) for page in item.get("source_pages", []))],
            )
            _attach_record_evidence_ids(item, report_id, group)

    summary_evidence = result.get("business_summary_evidence")
    if isinstance(summary_evidence, dict):
        summary_evidence["fact_id"] = _stable_id(
            "bizfact", [report_id, "business_summary", result.get("business_summary", "")]
        )
        _attach_record_evidence_ids(summary_evidence, report_id, "business_summary")
