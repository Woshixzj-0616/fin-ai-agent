"""P2 独立抽取留出集评分：逐字段核对，不把缺失折成正确。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import re
import pymupdf
from decimal import Decimal, InvalidOperation

from extraction.catalog import ALIASES
from extraction.orchestrator import extract_material
from periods import STOCK_METRICS
from holdout_eval import verify_dataset
from materials import Run, sha256


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
    ocr_review = ocr_candidates = ocr_value_correct = ocr_complete = 0
    ocr_missing = ocr_policy_violations = 0
    automatic_total = sum(not target.get("requires_review") for target in targets)
    ocr_total = len(targets) - automatic_total
    failures = material_failures or []
    material_stats = {
        "failed": len(failures),
        "automatic_failures": sum(item.get("mode", "automatic") != "ocr" for item in failures),
        "ocr_failures": sum(item.get("mode") == "ocr" for item in failures),
        "ocr_materials": sum(item.get("mode") == "ocr" for item in failures),
    }
    review_counts = {key: 0 for key in counts}

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
            if target.get("requires_review"):
                ocr_missing += 1
                rows.append({"evidence_id": target.get("evidence_id"),
                             "status": "ocr_review_missing"})
            else:
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
        failed = [field for field, passed in checks.items() if not passed]
        actual_ocr = bool(actual.get("ocr_used")
                         or "ocr_requires_manual_review" in actual.get("issues", []))
        if target.get("requires_review") or actual_ocr:
            for field in failed:
                review_counts[field] += 1
            policy_ok = bool(target.get("requires_review")) and actual_ocr
            if not policy_ok:
                ocr_policy_violations += 1
                status = "ocr_policy_violation"
            else:
                ocr_candidates += 1
                ocr_review += 1
                ocr_value_correct += int(checks["value"])
                ocr_complete += int(not failed)
                status = "ocr_review"
            rows.append({
                "evidence_id": target.get("evidence_id"),
                "status": status,
                "failed_fields": failed,
                "actual_evidence_id": actual.get("evidence_id"),
                "actual_page": actual.get("page"),
                "requires_manual_review": True,
            })
            continue

        for field, passed in checks.items():
            if passed:
                if field == "location":
                    located += 1
                elif field == "value":
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
        "automatic_total": automatic_total,
        "automatic_correct": correct,
        "correct": correct,
        "ocr_total": ocr_total,
        "ocr_review": ocr_review,
        "ocr_candidates": ocr_candidates,
        "ocr_value_correct": ocr_value_correct,
        "ocr_complete": ocr_complete,
        "ocr_missing": ocr_missing,
        "ocr_policy_violations": ocr_policy_violations,
        "value_correct": value_correct,
        "period_correct": period_correct,
        "unit_correct": unit_correct,
        "page_correct": page_correct,
        "label_correct": label_correct,
        "located": located,
        "missing": missing,
        "material_failures": len(material_failures or []),
        "material_stats": material_stats,
        "explicit_uncertain": explicit_uncertain,
        "silent_errors": silent_errors,
        "mismatch_counts": counts,
        "review_mismatch_counts": review_counts,
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
    automatic_target_count = protocol.get("automatic_target_fact_count", len(targets))
    if len(targets) != automatic_target_count:
        raise ValueError(
            f"P2 自动目标事实数不符：{len(targets)} != {automatic_target_count}")

    derived_specs = protocol.get("derived_materials", [])
    for spec in derived_specs:
        source_id = spec["source_document_id"]
        if source_id not in selected_ids:
            raise ValueError(f"派生 OCR 材料引用了清单外文档：{source_id}")
        source_targets = [
            fact for fact in facts_bundle["facts"]
            if fact.get("document_id") == source_id
            and fact.get("page") == spec["source_page"]
            and fact.get("metric") in spec["target_metrics"]]
        if len(source_targets) != spec["target_fact_count"]:
            raise ValueError(
                f"派生 OCR 目标事实数不符：{len(source_targets)} != {spec['target_fact_count']}")
        for fact in source_targets:
            targets.append({**fact, "document_id": spec["document_id"], "page": 1,
                            "evidence_id": str(fact.get("evidence_id")) + "-scan",
                            "requires_review": True, "review_reason": "ocr"})
    if len(targets) != protocol["target_fact_count"]:
        raise ValueError(f"P2 目标事实总数不符：{len(targets)} != {protocol['target_fact_count']}")

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
                           "mode": "automatic",
                           "error": str(exc)}
                failures.append(failure)
                material_runs.append({**failure, "status": "review"})
                continue
            extracted.extend(facts)
            material_runs.append({"document_id": document_id, "status": "ok",
                                  "mode": "automatic", "ocr_used": False,
                                  "fact_count": len(facts),
                                  "needs_review": sum(bool(fact.get("issues")) for fact in facts)})

        for spec in derived_specs:
            document_id = spec["document_id"]
            source_material = materials[spec["source_document_id"]]
            source_path = root / source["path"] / source_material["local_file"]
            relative = Path("data") / "derived" / f"{document_id}.pdf"
            scanned_path = Path(temp).resolve() / relative
            fingerprint = _render_scanned_page(
                source_path, scanned_path, spec["source_page"], spec["render_scale"])
            material = {**source_material,
                        "document_id": document_id,
                        "local_file": relative.as_posix(),
                        "sha256": fingerprint,
                        "document_type": "annual_report_summary_scanned"}
            try:
                facts = extract_material(
                    Path(temp).resolve(), material, run, ocr=True)
            except Exception as exc:
                failure = {"document_id": document_id, "mode": "ocr",
                           "error_type": type(exc).__name__, "error": str(exc)}
                failures.append(failure)
                material_runs.append({**failure, "status": "review"})
                continue
            extracted.extend(facts)
            material_runs.append({
                "document_id": document_id, "mode": "ocr", "status": "ok",
                "fact_count": len(facts), "ocr_used": True,
                "needs_review": sum(bool(fact.get("issues")) for fact in facts),
                "ocr_review_facts": sum(bool(fact.get("ocr_used")) for fact in facts),
            })

    comparison = compare_extraction(targets, extracted, material_failures=failures)
    material_stats = {
        "total": len(material_runs),
        "ok": sum(item["status"] == "ok" for item in material_runs),
        "failed": len(failures),
        "automatic_materials": sum(item.get("mode") == "automatic" for item in material_runs),
        "ocr_materials": sum(item.get("mode") == "ocr" for item in material_runs),
        "automatic_failures": sum(item.get("mode") == "automatic" for item in failures),
        "ocr_failures": sum(item.get("mode") == "ocr" for item in failures),
    }
    comparison.update(
        dataset_verification=verification,
        material_stats=material_stats,
        material_runs=material_runs,
        target_document_ids=protocol["material_ids"] + [spec["document_id"] for spec in derived_specs],
        target_metrics=metric_order,
        material_failures_detail=failures,
    )
    return {"report": comparison, "facts": extracted, "targets": targets}
def _render_scanned_page(source: Path, destination: Path, page_number: int,
                         scale: float) -> str:
    """把指定原页确定性栅格化为单页扫描 PDF，保留原页版式用于 OCR 挑战。"""
    with pymupdf.open(source) as document:
        page = document[page_number - 1]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        scanned = pymupdf.open()
        target = scanned.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, stream=pixmap.tobytes("png"))
        blob = scanned.tobytes()
        scanned.close()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(blob)
    return sha256(blob)
