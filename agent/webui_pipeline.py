"""旧 Web 页面和兼容流水线实现源。"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

try:
    from audit.tools.search import document_key
    from extract import extract_material
    from finance import analyze, evidence_issues
    from main import report_markdown
    from materials import ROOT, Run, register, sha256, write_json
except ModuleNotFoundError:
    from agent.audit.tools.search import document_key
    from agent.extract import extract_material
    from agent.finance import analyze, evidence_issues
    from agent.main import report_markdown
    from agent.materials import ROOT, Run, register, sha256, write_json


WORKSPACE = ROOT / "results" / "webui_work"
_WORK_TTL_SEC = 24 * 3600
_ANALYZE_LOCK = threading.Lock()
_LAST_LOCK = threading.Lock()
LAST_FACTS: list[dict] = []
LAST_DOCUMENT_TEXTS: dict[str, list[str]] = {}


def guess_identity(blob: bytes) -> dict:
    """从 PDF 前几页猜代码、简称和年度，猜不中就留空给调用方。"""
    try:
        import pymupdf
    except ImportError:
        return {}
    try:
        with pymupdf.open(stream=blob, filetype="pdf") as doc:
            pages = [page.get_text() for page in list(doc)[:6]]
            sample = "".join(pages)
    except Exception:
        return {}
    flat = re.sub(r"\s+", "", sample)
    guess: dict = {}
    from periods import report_period

    identity = report_period(flat)
    if identity["report_kind"] != "unknown":
        guess.update(
            year=identity["report_year"],
            report_kind=identity["report_kind"],
            period_end=identity["report_period_end"],
        )
    year = re.search(r"(20\d{2})年?(?:年度报告|度报告|度報告|年报)", flat)
    if year:
        guess["year"] = int(year.group(1))
    code = re.search(r"(?<!\d)([036]\d{5})(?!\d)", re.sub(r"[\r\n]+", " ", sample))
    if code:
        guess["code"] = code.group(1)
    name = re.search(r"([\u4e00-\u9fa5A-Za-z()（）·]{2,24}股份有限公司)", flat)
    if name:
        full = name.group(1)
        guess["name"] = re.sub(r"(股份有限公司|集团|控股)$", "", full) or full
    title = re.sub(r"\s+", "", "".join(pages[0].splitlines()[:30])) if pages else ""
    if identity["report_kind"] != "unknown":
        guess["material_type"] = "financial"
    elif "公告" in title:
        kinds = [kind for kind, pattern in (("pledge", "股份质押|股份.*质押"), ("winning_bid", "中标"),
                  ("equity_change", "股权变动|权益变动|股份转让|股权转让")) if re.search(pattern, title)]
        guess.update(material_type="announcement", suggested_task=kinds[0] if len(kinds) == 1 else None)
        dated = re.search(r"本公告日期[（(](?:即)?(20\d{2})年(\d{1,2})月(\d{1,2})日", flat)
        if dated:
            try:
                guess["announcement_date"] = datetime(*map(int, dated.groups())).date().isoformat()
                guess["announcement_date_basis"] = dated[0]
            except ValueError:
                pass
    else:
        guess["material_type"] = "unknown"
    return guess


def run_pipeline(
    blob: bytes,
    code: str,
    name: str,
    year: int,
    draft: str | None = None,
    *,
    workspace: Path | None = None,
    root: Path | None = None,
    comparison_blobs: list[bytes] | None = None,
    run=None,
    publish_latest: bool = True,
    store_page_texts: bool = False,
) -> dict:
    """丢一份财报走完整流水线。页图默认渲染，供页面点击溯源。"""
    if len(comparison_blobs or []) > 4:
        raise ValueError("比较材料最多 4 份，请先选择相邻期间报告")
    identity = guess_identity(blob)
    if identity.get("material_type") == "announcement":
        names = {"pledge": "股份质押", "winning_bid": "中标", "equity_change": "股权变动"}
        raise ValueError("该PDF为公告，请切换到“公告字段 · " + names.get(identity.get("suggested_task"), "对应类型") + "”，无需填写财报年度")
    base = (root or ROOT).resolve()
    if run is None:
        work = (workspace or WORKSPACE).resolve()
        fingerprint = sha256(blob)
        run = Run(
            base,
            "webui-live",
            {
                "command": "webui-live",
                "company_code": code,
                "company_name": name,
                "report_year": year,
                "draft": bool(draft),
                "render": True,
                "sha256": fingerprint,
                "workspace": str(work),
            },
        )
    else:
        base = Path(run.root).resolve()
        work = (workspace or (Path(run.folder) / "work")).resolve()
        fingerprint = sha256(blob)
    work.relative_to(base)
    work.mkdir(parents=True, exist_ok=True)
    announcement_id = str(int(fingerprint[:8], 16))
    record = register(
        work,
        blob,
        {
            "company_code": code,
            "company_name": name,
            "report_year": int(year),
            "announcement_id": announcement_id,
            "title": f"{name}{year}年财务报告（网页登记）",
            "disclosed_at": datetime.now().date().isoformat(),
            "disclosure_date_status": "onsite_unverified",
            "source_url": f"webui://{fingerprint[:12]}",
            "version_policy": "first",
            "license_status": "uploaded_rights_unverified",
        },
        run,
    )
    materials = [record]
    try:
        facts = extract_material(work, record, run)
        failures = []
    except Exception as exc:
        facts = []
        failures = [
            {
                "document_id": record["document_id"],
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        ]
        run.event("extraction_failed", **failures[0])
    for extra in comparison_blobs or []:
        identity = guess_identity(extra)
        if identity.get("code") != code or not identity.get("year"):
            raise ValueError("比较材料须明确为同一公司，并具有可识别的报告年度")
        extra_hash = sha256(extra)
        extra_record = register(
            work,
            extra,
            {
                "company_code": code,
                "company_name": name,
                "report_year": identity["year"],
                "announcement_id": str(int(extra_hash[:8], 16)),
                "title": f"{name}{identity['year']}年比较报告",
                "disclosed_at": None,
                "disclosure_date_status": "onsite_unverified",
                "source_url": f"webui://{extra_hash[:12]}",
                "version_policy": "first",
                "license_status": "uploaded_rights_unverified",
            },
            run,
        )
        if extra_record["document_id"] in {m["document_id"] for m in materials}:
            continue
        materials.append(extra_record)
        try:
            facts.extend(extract_material(work, extra_record, run))
        except ValueError as exc:
            failures.append(
                {"document_id": extra_record["document_id"], "message": str(exc)}
            )
            run.event("extraction_failed", **failures[-1])
    page_images: dict[tuple, str] = {}
    if facts:
        import pymupdf

        for material in materials:
            with pymupdf.open(work / material["local_file"]) as doc:
                pages = sorted(
                    {
                        fact["page"]
                        for fact in facts
                        if fact.get("page")
                        and fact["document_id"] == material["document_id"]
                    }
                )
                for page in pages:
                    if not (1 <= page <= doc.page_count):
                        continue
                    name_png = (
                        f"{material['company_code']}_{material['report_year']}_"
                        f"{material['sha256'][:8]}_p{page}.png"
                    )
                    doc[page - 1].get_pixmap(
                        matrix=pymupdf.Matrix(1.6, 1.6)
                    ).save(run.output(name_png))
                    page_images[(material["document_id"], page)] = name_png
    for fact in facts:
        fact["page_image"] = page_images.get((fact["document_id"], fact.get("page")))
        fact["source_file"] = str((work / fact["source_file"]).relative_to(base))
    doc_texts: dict[str, list[str]] = {}
    for material in materials:
        # 正文按材料身份索引：同年季报/半年报/年报各自独立，不再互相覆盖。
        key = document_key(material["company_code"], material["report_year"],
                           material["document_id"])
        try:
            import pymupdf as _pm

            with _pm.open(work / material["local_file"]) as _doc:
                doc_texts[key] = [page.get_text() for page in _doc]
        except Exception:
            doc_texts.setdefault(key, [])
    for material in materials:
        material["local_file"] = str(
            (work / material["local_file"]).relative_to(base)
        )
    if store_page_texts:
        write_json(run.output("webui_document_texts.json"), doc_texts)
    write_json(run.output("webui_evidence.json"), facts)
    write_json(run.output("webui_failures.json"), failures)
    analysis = analyze(facts, run)
    source_links = {material["document_id"]: Path(os.path.relpath(base / material["local_file"], run.folder)).as_posix()
                    for material in materials}
    report = report_markdown(analysis, materials, None, source_links=source_links)
    issues = [fact for fact in facts if evidence_issues(fact)]
    checks = _check_draft(draft, facts, run) if draft and draft.strip() else None
    if checks is not None:
        report += "\n\n" + draft_report_markdown(checks)
    run.output("webui_report.md").write_text(report, encoding="utf-8")
    counts = {"match": 0, "mismatch": 0, "unverified": 0, "issue": len(issues)}
    for row in analysis["rows"]:
        check = row.get("reported_change_check") or row.get("reported_yoy_check")
        if check and check.get("status") == "match":
            counts["match"] += 1
        elif check and check.get("status") == "mismatch":
            counts["mismatch"] += 1
        else:
            counts["unverified"] += 1
    payload: dict = {
        "ok": not failures and not (checks and checks.get("status") == "failed"),
        "run_id": run.id,
        "document_id": record["document_id"],
        "materials": [
            {
                "company_code": material["company_code"],
                "document_id": material["document_id"],
                "company_name": material["company_name"],
                "report_year": material["report_year"],
                "report_kind": material.get("report_kind"),
                "title": material.get("title", ""),
                "page_count": material.get("page_count"),
                "sha256": material.get("sha256", ""),
                "local_file": material.get("local_file"),
            }
            for material in materials
        ],
        "rows": [
            {
                **row,
                "page_image": page_images.get((row["document_id"], row.get("page"))),
            }
            for row in analysis["rows"]
        ],
        "signals": analysis["signals"],
        "qoq_rows": analysis.get("qoq_rows", []),
        "qoq_blocked": analysis.get("blocked_derivations", []),
        "basis": analysis["basis"],
        "limits": analysis["limits"],
        "counts": counts,
        "evidence": [
            {
                "evidence_id": fact["evidence_id"],
                "document_id": fact["document_id"],
                "metric": fact.get("metric"),
                "metric_name": fact.get("metric_name"),
                "period_year": fact.get("period_year"),
                "value": fact.get("value"),
                "unit": fact.get("unit"),
                "period_start": fact.get("period_start"),
                "period_end": fact.get("period_end"),
                "period_kind": fact.get("period_kind"),
                "normalized_value": fact.get("normalized_value"),
                "normalized_unit": fact.get("normalized_unit"),
                "currency": fact.get("currency"),
                "source_file": fact.get("source_file"),
                "source_sha256": fact.get("source_sha256"),
                "page": fact.get("page"),
                "page_image": fact.get("page_image"),
                "value_bbox": fact.get("value_bbox"),
                "label_bbox": fact.get("label_bbox"),
                "scope": fact.get("scope"),
                "adjustment": fact.get("adjustment"),
                "issues": evidence_issues(fact),
                "source_origin": fact.get("source_origin"),
            }
            for fact in facts
        ],
        "issues": [
            {
                "evidence_id": fact["evidence_id"],
                "metric_name": fact.get("metric_name"),
                "issues": evidence_issues(fact),
            }
            for fact in issues
        ],
        "report_md": report,
        "failures": failures,
    }
    if checks is not None:
        payload["checks"] = checks
    if publish_latest:
        with _LAST_LOCK:
            LAST_FACTS.clear()
            LAST_FACTS.extend(facts)
            LAST_DOCUMENT_TEXTS.clear()
            LAST_DOCUMENT_TEXTS.update(doc_texts)
    run.finish(
        status=("partial_failure" if failures or
                (checks and (checks.get("execution_status") == "partial" or checks.get("status") == "failed")) else "ok"),
        materials=len(materials),
        evidence_count=len(facts),
        failures=len(failures),
        draft_checked=bool(checks),
    )
    from trace import build_trace

    payload["trace"] = build_trace(run.events, facts)
    return payload


def draft_report_markdown(bundle: dict) -> str:
    """将实际核查结论加入可交付报告，保留失败范围与修改意见。"""
    def cell(value):
        return str(value or "—").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ")
    lines = ["## 研报草稿核查", "", f"核查状态：{cell(bundle.get('status'))}；模式：{cell(bundle.get('mode'))}。", "",
             cell(bundle.get("reason")), "",
             "确认错误与证据支持由程序核查；模型判断属于待复核解释。未核查句子不能视为通过。", "",
             "| 主张 | 原句 | 结果 | 原因 | 修改建议 | 证据 ID |", "|---|---|---|---|---|---|"]
    for check in bundle.get("checks") or []:
        lines.append("| " + " | ".join(cell(value) for value in (
            check.get("claim_id"), check.get("quote") or check.get("original_sentence"),
            check.get("status"), check.get("reason"), check.get("suggestion"),
            ", ".join(check.get("evidence_ids") or []))) + " |")
    if bundle.get("unfinished_sentence_ids"):
        lines += ["", "未完成句子：" + ", ".join(map(str, bundle["unfinished_sentence_ids"]))]
    return "\n".join(lines) + "\n"


def _check_draft(draft: str, facts: list[dict], run) -> dict:
    """有草稿就走核查；没配模型时明确说清，不假装判过。"""
    from llm_check import SECRET_PATTERN

    if SECRET_PATTERN.search(draft):
        return {
            "status": "failed",
            "reason": "草稿疑似包含凭证，拒绝处理与保存",
            "checks": [],
        }
    try:
        from llm_check import LLMClient, LLMError, check_text

        client = LLMClient.from_environment()
    except Exception:
        from audit_checks import check_draft_supplements
        from llm_check import SECRET_PATTERN

        if SECRET_PATTERN.search(draft):
            return {
                "status": "failed",
                "reason": "草稿疑似包含凭证，拒绝处理与保存",
                "checks": [],
            }
        checks = check_draft_supplements(draft, facts, root=run.root, run=run)
        from collections import Counter

        return {
            "status": "completed" if checks else "skipped",
            "mode": "local_supplemental",
            "reason": "未配置模型；仅检查明确表达的倍数与引用，其他句子未核查",
            "counts": dict(Counter(check["status"] for check in checks)),
            "checks": checks,
            "tools_used": 0,
            "tool_names": [],
        }
    try:
        if client.key in draft:
            return {
                "status": "failed",
                "reason": "草稿含模型凭证，拒绝处理与保存",
                "checks": [],
            }
        temporary = run.output("webui_draft.txt")
        temporary.write_text(draft, encoding="utf-8")
        if os.environ.get("LLM_STREAM", "").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }:
            client.stream = True
        bundle = check_text(temporary, facts, run, client, use_loop=True)
        tools_used = bundle.get("tools_used") or []
        return {
            "status": "completed" if bundle.get("status") == "completed" else "failed",
            "execution_status": bundle.get("status"),
            "reason": bundle.get("reason"),
            "unfinished_sentence_ids": bundle.get("unfinished_sentence_ids", []),
            "checkpoint_file": bundle.get("checkpoint_file"),
            "mode": bundle.get("mode"),
            "counts": bundle.get("counts"),
            "checks": bundle.get("checks") or [],
            "tools_used": len(tools_used),
            "tool_names": [tool.get("name") for tool in tools_used],
            "model": getattr(client, "model", None),
            "provider_host": getattr(client, "host", None),
        }
    except Exception as exc:
        salvaged = _salvage_checks(run)
        payload = {"status": "failed", "reason": str(exc),
                   "checks": salvaged.get("checks") or []}
        if salvaged:
            payload.update(
                execution_status=salvaged.get("status") or "partial",
                unfinished_sentence_ids=salvaged.get("unfinished_sentence_ids", []),
                checkpoint_file=salvaged.get("checkpoint_file"),
                mode=salvaged.get("mode"),
                counts=salvaged.get("counts"),
            )
        return payload


def _salvage_checks(run) -> dict:
    """核查中途失败时读回本次运行已写出的检查点结果，不把已完成分块丢掉。"""
    try:
        path = run.output("text_checks.json")
        if not path.is_file():
            return {}
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def run_announcement_pipeline(
    blob: bytes,
    event_type: str,
    *,
    root: Path | None = None,
    ocr: bool = False,
    run=None,
    publish_latest: bool = True,
) -> dict:
    from announcements import SCHEMAS, extract_announcement
    from trace import build_trace

    import pymupdf

    base = Path(root or ROOT).resolve()
    if run is None:
        run = Run(
            base,
            "webui-announcement",
            {"event_type": event_type, "sha256": sha256(blob), "ocr": ocr},
        )
    else:
        base = Path(run.root).resolve()
    path = run.output("webui_announcement.pdf")
    path.write_bytes(blob)
    try:
        identity = guess_identity(blob)
        output = extract_announcement(
            run.read(path),
            event_type,
            run=run,
            source_file=str(path.relative_to(base)),
            ocr=ocr,
            company_code=identity.get("code"), company_name=identity.get("name"),
        )
        output["identity"] = identity
    except Exception as exc:
        run.event("extraction_failed", message=str(exc), error_type=type(exc).__name__)
        run.finish(status="failed", reason=str(exc))
        raise
    evidence, images = [], {}
    with pymupdf.open(stream=blob, filetype="pdf") as doc:
        for event in output["events"]:
            for field, value in event["fields"].items():
                for index, location in enumerate(value["evidence"]):
                    page = location["page"]
                    if page not in images:
                        name = f"000000_2026_{output['source_sha256'][:8]}_p{page}.png"
                        doc[page - 1].get_pixmap(
                            matrix=pymupdf.Matrix(1.6, 1.6)
                        ).save(run.output(name))
                        images[page] = name
                    evidence.append(
                        {
                            "evidence_id": f"{event['event_id']}_{field}_{index}",
                            "metric": field,
                            "metric_name": SCHEMAS[event["event_type"]]["fields"][field][0],
                            "event_name": event["event_name"],
                            "value": location["value"],
                            "normalized_value": location["normalized_value"],
                            "unit": location.get("unit"),
                            "period_year": "—",
                            "page": page,
                            "page_image": images[page],
                            "value_bbox": location.get("bbox"),
                            "issues": list(
                                dict.fromkeys(
                                    location.get("issues", [])
                                    + value.get("issues", [])
                                    + (
                                        ["field_conflict"]
                                        if value["status"] == "conflict"
                                        else []
                                    )
                                    + (
                                        ["OCR 识别需人工复核" if ocr else "字段需人工复核"]
                                        if value["status"] == "needs_review"
                                        else []
                                    )
                                )
                            ),
                            "source_file": str(path.relative_to(base)),
                            "source_sha256": output["source_sha256"],
                        }
                    )
    write_json(run.output("webui_announcement_events.json"), output)
    if publish_latest:
        with _LAST_LOCK:
            LAST_FACTS.clear()
    report = ["# 公告字段抽取", "", output["limits"]]
    for event in output["events"]:
        report.extend(
            [
                "",
                f"## {event['event_name']} · {event['status']}",
                "",
                f"必填缺失：{','.join(event['missing_required']) or '无'}；"
                f"冲突：{','.join(event['conflicts']) or '无'}。",
                "",
                "| 字段 | 原值 | 规范值 | 状态 | PDF 页序 |",
                "|---|---|---|---|---|",
            ]
        )
        for field, value in event["fields"].items():
            cells = [
                field,
                value.get("value") or "缺失",
                f"{value.get('normalized_value') or '—'} "
                f"{value.get('normalized_unit') or ''}",
                value["status"],
                ",".join(str(location["page"]) for location in value["evidence"]),
            ]
            report.append(
                "| "
                + " | ".join(
                    str(cell).replace("|", "\\|").replace("\n", " ") for cell in cells
                )
                + " |"
            )
    report_md = "\n".join(report) + "\n"
    write_json(run.output("webui_announcement_evidence.json"), evidence)
    run.output("webui_announcement_report.md").write_text(report_md, encoding="utf-8")
    run.finish(status=output["status"], event_count=len(output["events"]))
    return {
        "ok": bool(output["events"]),
        "run_id": run.id,
        "announcement": output,
        "evidence": evidence,
        "trace": build_trace(run.events, evidence),
        "rows": [],
        "issues": [event for event in evidence if event["issues"]],
        "counts": {
            "events": len(output["events"]),
            "required_missing": sum(
                len(event["missing_required"]) for event in output["events"]
            ),
            "conflicts": sum(len(event["conflicts"]) for event in output["events"]),
        },
        "signals": [],
        "report_md": report_md,
        "failures": [],
    }


__all__ = [
    "LAST_DOCUMENT_TEXTS",
    "LAST_FACTS",
    "WORKSPACE",
    "_ANALYZE_LOCK",
    "_LAST_LOCK",
    "_WORK_TTL_SEC",
    "_check_draft",
    "guess_identity",
    "run_announcement_pipeline",
    "run_pipeline",
]
