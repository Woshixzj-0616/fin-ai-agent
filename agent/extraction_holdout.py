"""P2 独立抽取留出集评分：逐字段核对，不把缺失折成正确。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import re
from decimal import Decimal, InvalidOperation

from extraction.catalog import ALIASES
from extraction.orchestrator import extract_material
from periods import STOCK_METRICS
from holdout_eval import verify_dataset
from materials import Run


def _compact(value: str | None) -> str:
    return re.sub(r"[\s\u3000]", "", value or "")


def _number(value) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("，", ""))
    except (InvalidOperation, ValueError):
        return None


def _expected_period(target: dict) -> dict:
    metric = target.get("metric")
    end = target.get("period_end")
    if metric in STOCK_METRICS and target.get("period_kind") in {"quarter", "half"} and end:
        return {"period_start": end, "period_end": end, "period_kind": "instant"}
    return {
        "period_start": target.get("period_start"),
        "period_end": end,
        "period_kind": target.get("period_kind"),
    }


def _label_matches(target: dict, fact: dict) -> bool:
    label = _compact(fact.get("original_label"))
    if not label or not fact.get("label_bbox"):
        return False
    aliases = [alias for alias, metric, _ in ALIASES if metric == target.get("metric")]
    return any(_compact(alias) in label for alias in aliases)


def _located(fact: dict) -> bool:
    page = fact.get("page")
    return bool(isinstance(page, int) and page > 0
                and fact.get("label_bbox") and fact.get("value_bbox"))


def _same_period(expected: dict, fact: dict) -> bool:
    return all(fact.get(key) == expected.get(key)
               for key in ("period_start", "period_end", "period_kind"))


def _select(target: dict, candidates: list[dict], expected_period: dict) -> dict | None:
    if not candidates:
        return None
    expected_value = _number(target.get("normalized_value"))
    return sorted(
        candidates,
        key=lambda fact: (
            not _same_period(expected_period, fact),
            _number(fact.get("normalized_value")) != expected_value,
            fact.get("page") != target.get("page"),
        ),
    )[0]


def compare_extraction(targets: list[dict], facts: list[dict],
                       *, material_failures: list[dict] | None = None) -> dict:
    """按文档、指标和期间匹配，分别统计值/期间/单位/页码/标签。"""
    rows = []
    counts = {"value": 0, "period": 0, "unit": 0, "page": 0, "label": 0, "location": 0}
    silent_errors = 0
    explicit_uncertain = 0
    correct = value_correct = period_correct = unit_correct = 0
    page_correct = label_correct = located = missing = 0

    by_metric: dict[tuple[str, str], list[dict]] = {}
    for fact in facts:
        by_metric.setdefault((fact.get("document_id"), fact.get("metric")), []).append(fact)

    for target in targets:
        expected_period = _expected_period(target)
        actual = _select(
            target,
            by_metric.get((target.get("document_id"), target.get("metric")), []),
            expected_period,
        )
        if actual is None:
            missing += 1
            rows.append({"evidence_id": target.get("evidence_id"), "status": "missing"})
            continue

        checks = {
            "value": _number(actual.get("normalized_value")) == _number(target.get("normalized_value")),
            "period": _same_period(expected_period, actual),
            "unit": actual.get("normalized_unit") == target.get("normalized_unit"),
            "page": actual.get("page") == target.get("page"),
            "label": _label_matches(target, actual),
            "location": _located(actual),
        }
        for field, passed in checks.items():
            if passed:
                if field == "location":
                    located += 1
                else:
                    if field == "value":
                        value_correct += 1
                    elif field == "period":
                        period_correct += 1
                    elif field == "unit":
                        unit_correct += 1
                    elif field == "page":
                        page_correct += 1
                    elif field == "label":
                        label_correct += 1
            else:
                counts[field] += 1

        failed = [field for field, passed in checks.items() if not passed]
        if not failed:
            correct += 1
            status = "correct"
        elif actual.get("issues"):
            explicit_uncertain += 1
            status = "explicit_uncertain"
        else:
            silent_errors += 1
            status = f"wrong_{failed[0]}"
        rows.append({
            "evidence_id": target.get("evidence_id"),
            "status": status,
            "failed_fields": failed,
            "actual_evidence_id": actual.get("evidence_id"),
            "actual_page": actual.get("page"),
        })

    return {
        "total": len(targets),
        "correct": correct,
        "value_correct": value_correct,
        "period_correct": period_correct,
        "unit_correct": unit_correct,
        "page_correct": page_correct,
        "label_correct": label_correct,
        "located": located,
        "missing": missing,
        "material_failures": len(material_failures or []),
        "explicit_uncertain": explicit_uncertain,
        "silent_errors": silent_errors,
        "mismatch_counts": counts,
        "rows": rows,
    }


def run_holdout(root: Path, protocol_path: Path) -> dict:
    """运行 P2 目标指标抽取；材料失败保留为显式状态，不补猜值。"""
    root = Path(root).resolve()
    protocol = json.loads(Path(protocol_path).read_text(encoding="utf-8"))
    source = protocol["source_dataset"]
    dataset_root = root / source["path"]
    verification = verify_dataset(dataset_root)
    if not verification["ok"]:
        raise ValueError("P2 源留出集校验失败：" + "；".join(verification["issues"]))
    if verification["dataset_version"] != source["dataset_version"]:
        raise ValueError("P2 源留出集版本与协议不一致")

    manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    facts_bundle = json.loads((dataset_root / "facts.json").read_text(encoding="utf-8"))
    materials = {item["document_id"]: item for item in manifest["materials"]}
    selected_ids = set(protocol["material_ids"])
    if selected_ids != set(materials):
        raise ValueError("P2 材料清单与冻结 manifest 不一致")
    metric_order = protocol["target_metrics"]
    targets = [fact for fact in facts_bundle["facts"]
               if fact.get("document_id") in selected_ids and fact.get("metric") in metric_order]
    if len(targets) != protocol["target_fact_count"]:
        raise ValueError(f"P2 目标事实数不符：{len(targets)} != {protocol['target_fact_count']}")

    extracted = []
    material_runs = []
    failures = []
    with tempfile.TemporaryDirectory(prefix="p2_extraction_holdout_") as temp:
        run = Run(Path(temp).resolve(), "p2-extraction-holdout", {})
        for document_id in protocol["material_ids"]:
            material = dict(materials[document_id])
            material["local_file"] = str(Path(source["path"]) / material["local_file"])
            try:
                facts = extract_material(root, material, run)
            except Exception as exc:
                failure = {"document_id": document_id, "error_type": type(exc).__name__,
                           "error": str(exc)}
                failures.append(failure)
                material_runs.append({**failure, "status": "review"})
                continue
            extracted.extend(facts)
            material_runs.append({"document_id": document_id, "status": "ok",
                                  "fact_count": len(facts),
                                  "needs_review": sum(bool(fact.get("issues")) for fact in facts)})

    comparison = compare_extraction(targets, extracted, material_failures=failures)
    comparison.update(
        dataset_verification=verification,
        material_runs=material_runs,
        target_document_ids=protocol["material_ids"],
        target_metrics=metric_order,
        material_failures_detail=failures,
    )
    return {"report": comparison, "facts": extracted, "targets": targets}
