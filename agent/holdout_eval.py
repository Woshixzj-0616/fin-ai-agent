"""P0 留出集评测协议实现。

本模块只负责数据冻结、泄漏检查、统一指标和一致性汇总，不读取也不修改
Agent 的公司解析、抽取器或核查规则。参考答案只在评分阶段使用。
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

JUDGED_STATUSES = {"证据支持", "确认错误"}
ABSTAIN_STATUSES = {"证据不足", "口径冲突／需人工复核"}
FAILURE_OUTCOMES = {"timeout", "failed", "other"}
NONVERDICT_OUTCOMES = {"abstain", "model_track"}
DATASET_HASH_FILE = "dataset.sha256"
DATASET_FILES = ("manifest.json", "gold.json", "facts.json")
ANNUAL_SUMMARY = "annual_report_summary"
REQUIRED_GOLD_FIELDS = (
    "id",
    "text",
    "claim",
    "expect",
    "reason_category",
    "allowed_document_ids",
    "allowed_evidence",
    "abstention_allowed",
)
REQUIRED_MATERIAL_FIELDS = (
    "document_id",
    "company_code",
    "company_name",
    "sha256",
    "announcement_id",
    "document_type",
    "local_file",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _expected_status(expect: dict) -> str | None:
    if expect.get("kind") == "judge":
        return "确认错误" if expect.get("error") else "证据支持"
    if expect.get("kind") == "status":
        return expect.get("status")
    return None


def is_correct(row: dict) -> bool:
    expect = row.get("expect") or {}
    outcome = row.get("outcome")
    status = row.get("status")
    if expect.get("kind") == "nonverdict":
        return outcome in NONVERDICT_OUTCOMES
    if expect.get("kind") == "reason_code":
        return row.get("reason_code") == expect.get("code")
    wanted = _expected_status(expect)
    return wanted is not None and status == wanted


def _expected_abstain(row: dict) -> bool:
    expect = row.get("expect") or {}
    return (_expected_status(expect) in ABSTAIN_STATUSES
            or bool(row.get("abstention_allowed"))
            or row.get("reason_category") in {"scope_conflict", "missing_evidence",
                                               "material_not_loaded"})


def _citation_valid(row: dict) -> bool:
    evidence_ids = row.get("evidence_ids") or []
    locations = row.get("evidence_locations") or []
    if not evidence_ids or not locations:
        return False
    for location in locations:
        page = location.get("page")
        if (location.get("document_id")
                and isinstance(page, int) and page > 0
                and location.get("locator")):
            return True
    return False


def unified_metrics(rows: list[dict]) -> dict:
    """按 P0 协议计算统一指标；未运行、弃答和失败绝不折成正确。"""
    total = len(rows)
    correct_rows = [row for row in rows if is_correct(row)]
    judged_rows = [row for row in rows
                   if row.get("outcome") == "judged" and row.get("status") in JUDGED_STATUSES]
    explicit_correct = [row for row in correct_rows
                        if row.get("outcome") == "judged" and row.get("status") in JUDGED_STATUSES]
    expected_errors = [row for row in rows if _expected_status(row.get("expect") or {}) == "确认错误"]
    expected_support = [row for row in rows if _expected_status(row.get("expect") or {}) == "证据支持"]
    found_errors = [row for row in expected_errors
                    if row.get("outcome") == "judged" and row.get("status") == "确认错误"]
    false_errors = [row for row in expected_support
                    if row.get("outcome") == "judged" and row.get("status") == "确认错误"]
    abstain_eligible = [row for row in rows if _expected_abstain(row)]
    reasonable_abstentions = [row for row in abstain_eligible
                              if row.get("outcome") == "abstain" and is_correct(row)]
    cited = [row for row in judged_rows if _citation_valid(row)]
    failures = [row for row in rows if row.get("outcome") in FAILURE_OUTCOMES]
    omitted = [row for row in rows if row.get("outcome") == "omitted"]
    resolved_correct = [row for row in correct_rows
                        if row.get("outcome") in {"judged", "abstain"}]
    model_times = [float(row["model_ms"]) for row in rows
                   if isinstance(row.get("model_ms"), (int, float))]
    local_times = [float(row["local_ms"]) for row in rows
                   if isinstance(row.get("local_ms"), (int, float))]
    total_times = [float(row["model_ms"]) + float(row["local_ms"]) for row in rows
                   if isinstance(row.get("model_ms"), (int, float))
                   and isinstance(row.get("local_ms"), (int, float))]
    return {
        "total": total,
        "correct": len(correct_rows),
        "correct_judgments": len(explicit_correct),
        "judged": len(judged_rows),
        "abstain": sum(row.get("outcome") == "abstain" for row in rows),
        "omitted": len(omitted),
        "failed": len(failures),
        "model_track": sum(row.get("outcome") == "model_track" for row in rows),
        "unrun": sum(row.get("outcome") in {"omitted", "timeout", "failed"}
                     for row in rows),
        "judgment_accuracy": _ratio(len(correct_rows), total),
        "error_recall": _ratio(len(found_errors), len(expected_errors)),
        "false_positive_rate": _ratio(len(false_errors), len(expected_support)),
        "correct_coverage": _ratio(len(resolved_correct), total),
        "citation_supported": len(cited),
        "citation_support_rate": _ratio(len(cited), len(judged_rows)),
        "reasonable_abstentions": len(reasonable_abstentions),
        "abstain_eligible": len(abstain_eligible),
        "reasonable_abstention_rate": _ratio(len(reasonable_abstentions),
                                               len(abstain_eligible)),
        "failure_rate": _ratio(len(failures), total),
        "omitted_rate": _ratio(len(omitted), total),
        "model_mean_ms": round(sum(model_times) / len(model_times), 3) if model_times else None,
        "local_mean_ms": round(sum(local_times) / len(local_times), 3) if local_times else None,
        "total_mean_ms": round(sum(total_times) / len(total_times), 3) if total_times else None,
        "total_p95_ms": _percentile(total_times, 0.95),
    }


def _signature(row: dict) -> str:
    payload = {
        "outcome": row.get("outcome"),
        "status": row.get("status"),
        "reason_code": row.get("reason_code"),
        "evidence_ids": sorted(row.get("evidence_ids") or []),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def compare_runs(runs: list[list[dict]]) -> dict:
    """比较同配置多次运行；状态、原因和引用任一变化都算不一致。"""
    if not runs:
        return {"statement_count": 0, "fully_consistent": 0,
                "run_count": 0, "consistency_rate": None, "differences": []}
    run_count = len(runs)
    if run_count < 2:
        identifiers = sorted({row.get("id") for run in runs for row in run if row.get("id")})
        return {"statement_count": len(identifiers), "fully_consistent": 0,
                "run_count": run_count, "consistency_rate": None, "differences": []}
    identifiers = sorted({row.get("id") for run in runs for row in run if row.get("id")})
    differences = []
    consistent = 0
    for ident in identifiers:
        signatures = []
        for index, run in enumerate(runs, 1):
            match = next((row for row in run if row.get("id") == ident), None)
            signatures.append({"run": index,
                               "signature": _signature(match) if match else None})
        unique = {item["signature"] for item in signatures}
        if len(unique) == 1:
            consistent += 1
        else:
            differences.append({"id": ident, "run_count": len(runs), "runs": signatures})
    return {
        "statement_count": len(identifiers),
        "fully_consistent": consistent,
        "run_count": run_count,
        "consistency_rate": _ratio(consistent, len(identifiers)),
        "differences": differences,
    }


def validate_manifest(manifest: dict) -> list[str]:
    issues = []
    if not manifest.get("dataset_id"):
        issues.append("manifest 缺少 dataset_id")
    materials = manifest.get("materials") or []
    if len(materials) < 4:
        issues.append("留出集至少 4 份材料")
    if len({item.get("company_code") for item in materials if item.get("company_code")}) < 2:
        issues.append("留出集至少 2 家公司")
    if not any(item.get("document_type") == ANNUAL_SUMMARY for item in materials):
        issues.append("留出集缺少 annual_report_summary")
    if not any(item.get("document_type") not in {None, ANNUAL_SUMMARY} for item in materials):
        issues.append("留出集缺少年报摘要之外的其他材料")
    seen = set()
    for item in materials:
        missing = [field for field in REQUIRED_MATERIAL_FIELDS if not item.get(field)]
        if missing:
            issues.append(f"材料缺少字段：{','.join(missing)}")
        ident = item.get("document_id")
        if ident in seen:
            issues.append(f"duplicate document_id: {ident}")
        seen.add(ident)
    return issues


def check_leakage(dev_scope: list[dict], holdout_manifest: dict,
                  dev_manifest: list[dict]) -> dict:
    """公司代码、文件 SHA-256、公告 ID 三重去重，并检查留出集内部 ID 唯一。"""
    dev_codes = {str(row.get("code") or row.get("company_code") or "") for row in dev_scope}
    dev_codes.discard("")
    dev_hashes = {str(row.get("sha256") or "") for row in dev_manifest}
    dev_hashes.discard("")
    dev_announcements = {str(row.get("announcement_id") or "") for row in dev_manifest}
    dev_announcements.discard("")
    holdout_codes = set()
    holdout_hashes = set()
    holdout_announcements = set()
    holdout_document_ids = set()
    issues = []
    for item in holdout_manifest.get("materials") or []:
        code = str(item.get("company_code") or "")
        sha = str(item.get("sha256") or "")
        announcement = str(item.get("announcement_id") or "")
        document_id = str(item.get("document_id") or "")
        if code and code in dev_codes:
            issues.append(f"company_code overlap: {code}")
        if sha and sha in dev_hashes:
            issues.append(f"sha256 overlap: {document_id}")
        if announcement and announcement in dev_announcements:
            issues.append(f"announcement_id overlap: {announcement}")
        if document_id and document_id in holdout_document_ids:
            issues.append(f"duplicate document_id: {document_id}")
        holdout_codes.add(code)
        holdout_hashes.add(sha)
        holdout_announcements.add(announcement)
        holdout_document_ids.add(document_id)
    return {
        "ok": not issues,
        "issues": issues,
        "companies": sorted(holdout_codes - {""}),
        "sha256": sorted(holdout_hashes - {""}),
        "document_ids": sorted(holdout_document_ids - {""}),
        "announcement_ids": sorted(holdout_announcements - {""}),
    }


def validate_gold(gold: dict, *, document_ids: set[str]) -> list[str]:
    issues = []
    if not gold.get("dataset_id"):
        issues.append("gold 缺少 dataset_id")
    statements = gold.get("statements") or []
    if len(statements) < 30:
        issues.append("金标至少 30 条原子陈述")
    seen = set()
    for index, statement in enumerate(statements, 1):
        ident = statement.get("id")
        if not ident:
            issues.append(f"第 {index} 条缺少 id")
        elif ident in seen:
            issues.append(f"duplicate statement id: {ident}")
        seen.add(ident)
        missing = [field for field in REQUIRED_GOLD_FIELDS if field not in statement]
        if missing:
            issues.append(f"{ident or index} 缺少字段：{','.join(missing)}")
        allowed = statement.get("allowed_document_ids") or []
        unknown = [document_id for document_id in allowed if document_id not in document_ids]
        if unknown:
            issues.append(f"{ident or index} 引用未登记文档：{','.join(unknown)}")
        evidence = statement.get("allowed_evidence") or {}
        evidence_docs = evidence.get("document_ids") or []
        unknown = [document_id for document_id in evidence_docs if document_id not in document_ids]
        if unknown:
            issues.append(f"{ident or index} 允许证据引用未登记文档：{','.join(unknown)}")
    return issues


def rows_from_scored(scored: dict, statements: list[dict], facts: list[dict], *,
                       model_ms: float | None = None,
                       local_ms: float | None = None) -> list[dict]:
    """把现有原子评分行补齐 P0 指标需要的金标口径与可回查位置。"""
    statement_index = {item.get("id"): item for item in statements}
    fact_index = {item.get("evidence_id"): item for item in facts}
    rows = []
    for original in scored.get("rows") or []:
        row = copy.deepcopy(original)
        statement = statement_index.get(row.get("id")) or {}
        row["abstention_allowed"] = bool(statement.get("abstention_allowed"))
        row["reason_category"] = statement.get("reason_category")
        locations = []
        for evidence_id in row.get("evidence_ids") or []:
            fact = fact_index.get(evidence_id)
            if not fact or not fact.get("document_id") or not fact.get("page"):
                continue
            locator = f"{fact.get('source_file') or fact.get('document_id')}#page={fact['page']}"
            locations.append({"document_id": fact["document_id"], "page": fact["page"],
                              "locator": locator})
        row["evidence_locations"] = locations
        row["model_ms"] = model_ms
        row["local_ms"] = local_ms
        rows.append(row)
    return rows

def runtime_payload(gold: dict) -> tuple[str, list[dict]]:
    """只生成 Agent 可见的草稿与解析项，不带任何评分侧字段。"""
    statements = gold.get("statements") or []
    draft = "\n".join(statement.get("text") or "" for statement in statements)
    items = []
    for index, statement in enumerate(statements, 1):
        item = copy.deepcopy(statement.get("claim") or {})
        item["claim_id"] = statement.get("id")
        item["sentence_id"] = index
        item.setdefault("quote", statement.get("text"))
        items.append(item)
    return draft, items

def validate_facts(facts_bundle: dict, *, document_hashes: dict[str, str]) -> list[str]:
    """运行事实必须来自已登记材料，且同一 evidence_id 只能出现一次。"""
    facts = facts_bundle.get("facts")
    if not isinstance(facts, list) or not facts:
        return ["facts.json 缺少非空 facts 数组"]
    issues = []
    seen = set()
    for index, fact in enumerate(facts, 1):
        evidence_id = fact.get("evidence_id")
        if not evidence_id:
            issues.append(f"facts 第 {index} 条缺少 evidence_id")
        elif evidence_id in seen:
            issues.append(f"duplicate evidence_id: {evidence_id}")
        seen.add(evidence_id)
        document_id = fact.get("document_id")
        if document_id not in document_hashes:
            issues.append(f"{evidence_id or index} 引用未登记文档：{document_id}")
        if not isinstance(fact.get("page"), int) or fact.get("page", 0) < 1:
            issues.append(f"{evidence_id or index} 缺少有效页码")
        if fact.get("source_sha256") != document_hashes.get(document_id):
            issues.append(f"{evidence_id or index} source_sha256 与 manifest 不一致")
        if not fact.get("source_file"):
            issues.append(f"{evidence_id or index} 缺少 source_file")
    return issues

def verify_dataset(dataset_root: Path, *, dev_scope_path: Path | None = None,
                   dev_manifest_path: Path | None = None) -> dict:
    root = Path(dataset_root).resolve()
    manifest_path = root / "manifest.json"
    gold_path = root / "gold.json"
    facts_path = root / "facts.json"
    digest_path = root / DATASET_HASH_FILE
    issues = []
    required_paths = [manifest_path, gold_path, facts_path, digest_path]
    missing = [path.name for path in required_paths if not path.is_file()]
    if missing:
        return {"ok": False, "issues": [f"缺少文件：{','.join(missing)}"]}
    actual_hashes = {name: sha256_file(root / name) for name in DATASET_FILES}
    expected_hashes = {}
    for line_number, line in enumerate(
            digest_path.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.strip().split()
        if not parts:
            continue
        if len(parts) != 2 or len(parts[0]) != 64:
            issues.append(f"dataset.sha256 第 {line_number} 行格式错误")
            continue
        expected_hashes[parts[1]] = parts[0]
    if set(expected_hashes) != set(DATASET_FILES):
        issues.append("dataset.sha256 必须且只能登记 manifest.json、gold.json、facts.json")
    for name in DATASET_FILES:
        if expected_hashes.get(name) != actual_hashes[name]:
            issues.append(f"dataset.sha256 与 {name} 不一致")
    dataset_version = hashlib.sha256("".join(
        f"{name}:{actual_hashes[name]}\n" for name in DATASET_FILES
    ).encode("utf-8")).hexdigest()
    manifest = _load_json(manifest_path)
    gold = _load_json(gold_path)
    facts_bundle = _load_json(facts_path)
    if manifest.get("dataset_id") != gold.get("dataset_id"):
        issues.append("manifest.json 与 gold.json 的 dataset_id 不一致")
    if manifest.get("dataset_id") != facts_bundle.get("dataset_id"):
        issues.append("manifest.json 与 facts.json 的 dataset_id 不一致")
    for item in manifest.get("materials") or []:
        material_path = root / str(item.get("local_file") or "")
        if not material_path.is_file():
            issues.append(f"材料缺失：{item.get('local_file')}")
            continue
        actual = sha256_file(material_path)
        if actual != item.get("sha256"):
            issues.append(f"材料哈希不一致：{item.get('document_id')}")
    issues.extend(validate_manifest(manifest))
    document_ids = {item.get("document_id") for item in manifest.get("materials") or []}
    issues.extend(validate_gold(gold, document_ids=document_ids))
    document_hashes = {item.get("document_id"): item.get("sha256")
                       for item in manifest.get("materials") or []}
    issues.extend(validate_facts(facts_bundle, document_hashes=document_hashes))
    scope_path = dev_scope_path or (root.parent / "scope.csv")
    dev_path = dev_manifest_path or (root.parent / "manifest.csv")
    leakage = {"ok": False, "issues": ["缺少开发集快照"]}
    if scope_path.is_file() and dev_path.is_file():
        leakage = check_leakage(_load_csv(scope_path), manifest, _load_csv(dev_path))
        issues.extend(leakage["issues"])
    return {
        "ok": not issues,
        "issues": issues,
        "dataset_id": manifest.get("dataset_id"),
        "gold_sha256": actual_hashes["gold.json"],
        "manifest_sha256": actual_hashes["manifest.json"],
        "facts_sha256": actual_hashes["facts.json"],
        "dataset_version": dataset_version,
        "material_count": len(manifest.get("materials") or []),
        "company_count": len({item.get("company_code") for item in manifest.get("materials") or []}),
        "statement_count": len(gold.get("statements") or []),
        "leakage": leakage,
    }


def _display(value) -> str:
    if value is None:
        return "未定义"
    if isinstance(value, float):
        return f"{value:.2%}"
    return str(value)


def render_markdown(metrics: dict, consistency: dict, meta: dict) -> str:
    """报告模板只允许称“留出集回归”；无独立复核时明确写适用范围。"""
    independent = bool(meta.get("independent_review"))
    run_count = int(meta.get("run_count") or 0)
    metric_scope = f"{run_count} 轮累计" if run_count else "本次"
    lines = [
        "# P0 留出集回归基线",
        "",
        f"- 数据集：`{meta.get('dataset_id') or '未记录'}`",
        f"- 数据集版本：`{meta.get('dataset_version') or '未记录'}`",
        f"- 运行方式：`{meta.get('run_mode') or '未记录'}`",
        f"- 实际运行轮数：{run_count}",
        f"- 独立人工复核：{'有' if independent else '无'}",
        "- 适用范围：留出集回归，用于检查工程链路和结果稳定性。",
    ]
    if not independent:
        lines.append("- 本轮未进行独立人工复核，不能表述为独立盲测成绩。")
    lines += [
        "",
        f"## 统一指标（{metric_scope}）",
        "",
        "| 指标 | 值 | 口径 |",
        "|---|---:|---|",
        f"| 判定准确率 | {_display(metrics.get('judgment_accuracy'))} | 正确结论 / 全部金标陈述 |",
        f"| 正确判定数（{metric_scope}） | {metrics.get('correct_judgments', 0)} | 仅统计明确给出支持或确认错误的句子 |",
        f"| 错误召回率 | {_display(metrics.get('error_recall'))} | 确认错误命中 / 金标确认错误 |",
        f"| 误报率 | {_display(metrics.get('false_positive_rate'))} | 正确句被误判为确认错误 / 金标正确句 |",
        f"| 正确覆盖率 | {_display(metrics.get('correct_coverage'))} | 正确明确判定或合理弃答 / 全部金标陈述 |",
        f"| 引用支持率 | {_display(metrics.get('citation_support_rate'))} | 有 evidence_id、文档、页码和可回查位置 / 明确判定 |",
        f"| 合理弃答率 | {_display(metrics.get('reasonable_abstention_rate'))} | 应弃答且正确弃答 / 应弃答陈述 |",
        f"| 失败率 | {_display(metrics.get('failure_rate'))} | 超时、协议、工具或程序失败 / 全部陈述 |",
        f"| 漏拆率 | {_display(metrics.get('omitted_rate'))} | 未产出原子 / 全部陈述 |",
        f"| 模型平均耗时 | {metrics.get('model_mean_ms', '未定义')} ms | 模型请求时间 |",
        f"| 本地平均耗时 | {metrics.get('local_mean_ms', '未定义')} ms | 本地计算与编排时间 |",
        f"| 总耗时 P95 | {metrics.get('total_p95_ms', '未定义')} ms | 单条陈述 |",
        "",
        "## 结果边界",
        "",
        "- 未运行、漏拆、超时和失败都进入分母，不按正确计算。",
        "- 证据不足和口径冲突只有金标允许时才计合理弃答。",
        "- 本报告不把固定样例或重复运行替代独立盲测。",
        "",
    ]
    lines += ["", f"## 运行一致性（实际 {run_count} 轮）", ""]
    if run_count < 2:
        lines.append("- 本次不足两轮，不计算重复运行一致性。")
    else:
        lines += [
            f"- 完全一致：{consistency.get('fully_consistent', 0)} / {consistency.get('statement_count', 0)}",
            f"- 一致率：{_display(consistency.get('consistency_rate'))}",
            f"- 差异条数：{len(consistency.get('differences') or [])}",
        ]
    lines.append("")
    diagnostics = meta.get("diagnostics") or []
    lines += ["## 错误与未达标明细", ""]
    if not diagnostics:
        lines.append("- 无。")
    else:
        lines += [
            "| 陈述 | 金标预期 | 实际结果 | 原因码 | 原因类别 |",
            "|---|---|---|---|---|",
        ]
        for detail in diagnostics:
            values = [detail.get("id"), detail.get("expected"), detail.get("actual"),
                      detail.get("reason_code"), detail.get("reason_category")]
            lines.append("| " + " | ".join(
                str(value or "-").replace("|", "\\|") for value in values
            ) + " |")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "check_leakage",
    "compare_runs",
    "is_correct",
    "render_markdown",
    "runtime_payload",
    "rows_from_scored",
    "sha256_file",
    "unified_metrics",
    "validate_facts",
    "validate_gold",
    "validate_manifest",
    "verify_dataset",
]
