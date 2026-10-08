"""本地薄页面：丢入一份财报 → 复用 CLI 流水线 → 出指标与核查报告。

不重写分析引擎：登记/抽取/分析/核查全部走 agent 里与 CLI 相同的函数。
用法：python webui.py [--port 8765]，浏览器打开提示的地址。
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from finance import analyze
from extract import extract_material
from main import report_markdown
from materials import ROOT, Run, register, sha256, write_json
from qa import answer as qa_answer

MAX_UPLOAD = 40 * 1024 * 1024
# 临时分析的工作区：不进正式 70 份年报台账，避免污染 data/agent 与测试夹具
WORKSPACE = ROOT / "results" / "webui_work"
# 页图文件名：{code}_{year}_{sha8}_p{page}.png，落在 results/ 根下（Run.output 不允许子目录）
PAGE_PNG = re.compile(r"^\d{6}_\d{4}_[0-9a-f]{8}_p\d+\.png$")
# 最近一次分析的证据：受限问答只认这份，不另建库。
# ThreadingHTTPServer 多线程下必须持锁读写，否则 A 的分析会喂给 B 的提问。
LAST_FACTS: list[dict] = []
_LAST_LOCK = threading.Lock()

# ── Render 防护：令牌桶限流 + 分析并发锁 + 工作区 TTL ──
_RATE_CAPACITY = 10        # 每 IP 突发上限
_RATE_REFILL_PER_SEC = 0.2  # 每秒补 0.2 个（即 1 个/5 秒）
_rate_buckets: dict[str, tuple[float, float]] = {}  # ip → (tokens, last_ts)
_rate_lock = threading.Lock()
_ANALYZE_LOCK = threading.Lock()  # 同时刻只允许一个分析，防 free tier 被打穿
_WORK_TTL_SEC = 24 * 3600         # webui_work 里超 24h 的上传自动清理


def _rate_limit(ip: str, cost: float = 1.0) -> bool:
    """令牌桶限流；返回 True=放行，False=应拒绝。"""
    now = time.monotonic()
    with _rate_lock:
        tokens, last = _rate_buckets.get(ip, (_RATE_CAPACITY, now))
        tokens = min(_RATE_CAPACITY, tokens + (now - last) * _RATE_REFILL_PER_SEC)
        if tokens < cost:
            _rate_buckets[ip] = (tokens, now)
            return False
        _rate_buckets[ip] = (tokens - cost, now)
        return True


def _cleanup_work_dir() -> None:
    """清理 webui_work 里超时的上传 PDF 与页图，防磁盘被吃满。"""
    try:
        cutoff = time.time() - _WORK_TTL_SEC
        work = WORKSPACE
        if not work.is_dir():
            return
        for p in work.iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass  # 清理失败不阻断分析


# ---------- 流水线（与 CLI live 同一条路） ----------

def guess_identity(blob: bytes) -> dict:
    """从 PDF 前几页猜 代码/简称/年度，降低临时丢材料的填写成本。猜不中就留空让人填。"""
    try:
        import pymupdf
    except ImportError:
        return {}
    try:
        with pymupdf.open(stream=blob, filetype="pdf") as doc:
            sample = "".join(page.get_text() for page in list(doc)[:6])
    except Exception:
        return {}
    flat = re.sub(r"\s+", "", sample)
    guess: dict = {}
    from periods import report_period
    identity = report_period(flat)
    if identity["report_kind"] != "unknown":
        guess.update(year=identity["report_year"], report_kind=identity["report_kind"],
                     period_end=identity["report_period_end"])
    # 兼容「2024年年度报告」「2024年度报告」「2024年报」等常见写法
    year = re.search(r"(20\d{2})年?(?:年度报告|度报告|度報告|年报)", flat)
    if year:
        guess["year"] = int(year.group(1))
    # 代码要在仍保留空白的文本上找：全空白压掉后「600519 2024」会粘成「6005192024」
    code = re.search(r"(?<!\d)([036]\d{5})(?!\d)", re.sub(r"[\r\n]+", " ", sample))
    if code:
        guess["code"] = code.group(1)
    name = re.search(r"([\u4e00-\u9fa5A-Za-z()（）·]{2,24}股份有限公司)", flat)
    if name:
        full = name.group(1)
        guess["name"] = re.sub(r"(股份有限公司|集团|控股)$", "", full) or full
    return guess


def run_pipeline(blob: bytes, code: str, name: str, year: int,
                 draft: str | None = None, *, workspace: Path | None = None,
                 root: Path | None = None, comparison_blobs: list[bytes] | None = None) -> dict:
    """丢一份财报走完整流水线。页图默认渲染，供页面「点击溯源」。"""
    work = (workspace or WORKSPACE).resolve()
    base = (root or ROOT).resolve()
    if len(comparison_blobs or []) > 4:
        raise ValueError("比较材料最多 4 份，请先选择相邻期间报告")
    work.relative_to(base)  # 所有证据文件须在本次受控根目录中可定位。
    work.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256(blob)
    # 上传材料没有公告号：用指纹派生稳定 ID，同一文件重复上传不产生新身份
    announcement_id = str(int(fingerprint[:8], 16))
    run = Run(base, "webui-live", {"command": "webui-live", "company_code": code,
                                   "company_name": name, "report_year": year,
                                   "draft": bool(draft), "render": True,
                                   "sha256": fingerprint, "workspace": str(work)})
    record = register(work, blob, {
        "company_code": code, "company_name": name, "report_year": int(year),
        "announcement_id": announcement_id,
        "title": f"{name}{year}年财务报告（网页登记）",
        "disclosed_at": datetime.now().date().isoformat(),
        "disclosure_date_status": "onsite_unverified",
        "source_url": f"webui://{fingerprint[:12]}",
        "version_policy": "first", "license_status": "uploaded_rights_unverified",
    }, run)
    # 只抽本次上传的那一份：同公司同年的旧材料不掺进来
    materials = [record]
    try:
        facts = extract_material(work, record, run)
        failures = []
    except Exception as exc:
        facts = []
        failures = [{"document_id": record["document_id"],
                     "error_type": type(exc).__name__, "message": str(exc)}]
        run.event("extraction_failed", **failures[0])
    for extra in comparison_blobs or []:
        identity = guess_identity(extra)
        if identity.get("code") != code or not identity.get("year"):
            raise ValueError("比较材料须明确为同一公司，并具有可识别的报告年度")
        extra_hash = sha256(extra)
        extra_record = register(work, extra, {
            "company_code": code, "company_name": name, "report_year": identity["year"],
            "announcement_id": str(int(extra_hash[:8], 16)),
            "title": f"{name}{identity['year']}年比较报告", "disclosed_at": None,
            "disclosure_date_status": "onsite_unverified", "source_url": f"webui://{extra_hash[:12]}",
            "version_policy": "first", "license_status": "uploaded_rights_unverified"}, run)
        if extra_record["document_id"] in {m["document_id"] for m in materials}:
            continue
        materials.append(extra_record)
        try:
            facts.extend(extract_material(work, extra_record, run))
        except ValueError as exc:
            failures.append({"document_id": extra_record["document_id"], "message": str(exc)})
            run.event("extraction_failed", **failures[-1])
    page_images: dict[tuple, str] = {}
    if facts:
        import pymupdf
        for material in materials:
            with pymupdf.open(work / material["local_file"]) as doc:
                for page in sorted({f["page"] for f in facts if f.get("page") and f["document_id"] == material["document_id"]}):
                    if not (1 <= page <= doc.page_count):
                        continue
                    name_png = f"{material['company_code']}_{material['report_year']}_{material['sha256'][:8]}_p{page}.png"
                    doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(run.output(name_png))
                    page_images[(material["document_id"], page)] = name_png
    for fact in facts:
        fact["page_image"] = page_images.get((fact["document_id"], fact.get("page")))
        fact["source_file"] = str((work / fact["source_file"]).relative_to(base))
    for material in materials:
        material["local_file"] = str((work / material["local_file"]).relative_to(base))
    # 不用 evidence.json：那是审计台/CLI 的正式产物名，网页临时分析不覆盖
    write_json(run.output("webui_evidence.json"), facts)
    write_json(run.output("webui_failures.json"), failures)
    analysis = analyze(facts, run)
    report = report_markdown(analysis, materials, None)
    issues = [f for f in facts if f.get("issues")]
    checks = _check_draft(draft, facts, run) if draft and draft.strip() else None
    counts = {"match": 0, "mismatch": 0, "unverified": 0, "issue": len(issues)}
    for row in analysis["rows"]:
        check = row.get("reported_yoy_check")
        if check and check.get("status") == "match":
            counts["match"] += 1
        elif check and check.get("status") == "mismatch":
            counts["mismatch"] += 1
        else:
            counts["unverified"] += 1
    payload: dict = {
        "ok": not failures,
        "run_id": run.id,
        "document_id": record["document_id"],
        "materials": [{
            "company_code": m["company_code"], "company_name": m["company_name"],
            "report_year": m["report_year"], "title": m.get("title", ""),
            "page_count": m.get("page_count"), "sha256": m.get("sha256", "")[:12],
        } for m in materials],
        "rows": [{
            **row,
            "page_image": page_images.get((row["document_id"], row.get("page"))),
        } for row in analysis["rows"]],
        "signals": analysis["signals"],
        "qoq_rows": analysis.get("qoq_rows", []),
        "basis": analysis["basis"],
        "limits": analysis["limits"],
        "counts": counts,
        "evidence": [{
            "evidence_id": f["evidence_id"], "metric": f.get("metric"),
            "metric_name": f.get("metric_name"), "period_year": f.get("period_year"),
            "value": f.get("value"), "unit": f.get("unit"),
            "period_start": f.get("period_start"), "period_end": f.get("period_end"),
            "normalized_value": f.get("normalized_value"),
            "page": f.get("page"), "page_image": f.get("page_image"),
            "value_bbox": f.get("value_bbox"), "label_bbox": f.get("label_bbox"),
            "scope": f.get("scope"), "adjustment": f.get("adjustment"),
            "issues": f.get("issues") or [],
        } for f in facts],
        "issues": [{
            "evidence_id": f["evidence_id"], "metric_name": f.get("metric_name"),
            "issues": f.get("issues"),
        } for f in issues],
        "report_md": report,
        "failures": failures,
    }
    if checks is not None:
        payload["checks"] = checks
    with _LAST_LOCK:
        LAST_FACTS.clear()
        LAST_FACTS.extend(facts)
    run.finish(status="ok" if not failures else "partial_failure",
               materials=len(materials), evidence_count=len(facts),
               failures=len(failures), draft_checked=bool(checks))
    from trace import build_trace
    payload["trace"] = build_trace(run.events, facts)
    return payload


def _check_draft(draft: str, facts: list[dict], run) -> dict:
    """有草稿就走核查；没配模型时明确说清，不假装判过。"""
    from llm_check import SECRET_PATTERN
    if SECRET_PATTERN.search(draft):
        return {"status": "failed", "reason": "草稿疑似包含凭证，拒绝处理与保存", "checks": []}
    try:
        from llm_check import LLMClient, LLMError, check_text
        client = LLMClient.from_environment()
    except Exception as exc:
        from audit_checks import check_draft_supplements
        from llm_check import SECRET_PATTERN
        if SECRET_PATTERN.search(draft):
            return {"status": "failed", "reason": "草稿疑似包含凭证，拒绝处理与保存", "checks": []}
        checks = check_draft_supplements(draft, facts, root=run.root, run=run)
        from collections import Counter
        return {"status": "completed" if checks else "skipped", "mode": "local_supplemental",
                "reason": "未配置模型；仅检查明确表达的倍数与引用，其他句子未核查",
                "counts": dict(Counter(c["status"] for c in checks)), "checks": checks,
                "tools_used": 0, "tool_names": []}
    try:
        if client.key in draft:
            return {"status": "failed", "reason": "草稿含模型凭证，拒绝处理与保存", "checks": []}
        tmp = run.output("webui_draft.txt")
        tmp.write_text(draft, encoding="utf-8")
        bundle = check_text(tmp, facts, run, client, use_loop=True)
        tools_used = bundle.get("tools_used") or []
        return {"status": "completed", "mode": bundle.get("mode"),
                "counts": bundle.get("counts"), "checks": bundle.get("checks") or [],
                "tools_used": len(tools_used),
                "tool_names": [t.get("name") for t in tools_used],
                "model": getattr(client, "model", None),
                "provider_host": getattr(client, "host", None)}
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "checks": []}


def run_announcement_pipeline(blob: bytes, event_type: str, *, root: Path | None = None, ocr: bool = False) -> dict:
    from announcements import extract_announcement
    from trace import build_trace
    import pymupdf
    base = root or ROOT
    run = Run(base, "webui-announcement", {"event_type": event_type, "sha256": sha256(blob), "ocr": ocr})
    path = run.output("webui_announcement.pdf")
    path.write_bytes(blob)
    try:
        output = extract_announcement(run.read(path), event_type, run=run,
                                      source_file="results/webui_announcement.pdf", ocr=ocr)
    except Exception as exc:
        run.event("extraction_failed", message=str(exc), error_type=type(exc).__name__)
        run.finish(status="failed", reason=str(exc))
        raise
    evidence, images = [], {}
    with pymupdf.open(stream=blob, filetype="pdf") as doc:
        for event in output["events"]:
            for field, value in event["fields"].items():
                for i, loc in enumerate(value["evidence"]):
                    page = loc["page"]
                    if page not in images:
                        name = f"000000_2026_{output['source_sha256'][:8]}_p{page}.png"
                        doc[page-1].get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6)).save(run.output(name))
                        images[page] = name
                    evidence.append({"evidence_id": f"{event['event_id']}_{field}_{i}", "metric": field,
                                     "metric_name": field, "event_name": event["event_name"], "value": loc["value"],
                                     "normalized_value": loc["normalized_value"], "unit": loc.get("unit"),
                                     "period_year": "—", "page": page, "page_image": images[page],
                                     "value_bbox": loc.get("bbox"), "issues": list(dict.fromkeys(loc.get("issues", []) + value.get("issues", [])
                                     + (["field_conflict"] if value["status"] == "conflict" else [])
                                     + (["OCR 识别需人工复核" if ocr else "字段需人工复核"] if value["status"] == "needs_review" else []))),
                                     "source_file": "results/webui_announcement.pdf", "source_sha256": output["source_sha256"]})
    write_json(run.output("webui_announcement_events.json"), output)
    with _LAST_LOCK:
        LAST_FACTS.clear()
    report = ["# 公告字段抽取", "", output["limits"]]
    for event in output["events"]:
        report.extend(["", f"## {event['event_name']} · {event['status']}", "",
                       f"必填缺失：{','.join(event['missing_required']) or '无'}；冲突：{','.join(event['conflicts']) or '无'}。", "",
                       "| 字段 | 原值 | 规范值 | 状态 | PDF 页序 |", "|---|---|---|---|---|"])
        for field, value in event["fields"].items():
            cells = [field, value.get("value") or "缺失", f"{value.get('normalized_value') or '—'} {value.get('normalized_unit') or ''}",
                     value["status"], ",".join(str(loc["page"]) for loc in value["evidence"])]
            report.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    report_md = "\n".join(report) + "\n"
    write_json(run.output("webui_announcement_evidence.json"), evidence)
    run.output("webui_announcement_report.md").write_text(report_md, encoding="utf-8")
    # 报告和字段清单纳入同次归档。
    run.finish(status=output["status"], event_count=len(output["events"]))
    return {"ok": bool(output["events"]), "run_id": run.id, "announcement": output,
            "evidence": evidence, "trace": build_trace(run.events, evidence), "rows": [],
            "issues": [e for e in evidence if e["issues"]], "counts": {
                "events": len(output["events"]), "required_missing": sum(len(e["missing_required"]) for e in output["events"]),
                "conflicts": sum(len(e["conflicts"]) for e in output["events"])}, "signals": [],
            "report_md": report_md, "failures": []}


# ---------- 页面 ----------

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>金融投研智能体 · 现场分析</title>
<style>
:root {
  --bg: #f6f7f9; --card: #fff; --ink: #1c2430; --muted: #667085;
  --line: #e4e7ec; --accent: #1f5eff; --ok: #067647; --bad: #b42318;
  --warn: #b54708; --info: #175cd3;
}
* { box-sizing: border-box; }
body {
  margin: 0; font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg); color: var(--ink); line-height: 1.55;
}
header {
  background: var(--card); border-bottom: 1px solid var(--line);
  padding: 18px 28px 14px;
}
header h1 { margin: 0 0 4px; font-size: 22px; font-weight: 650; }
header .sub { margin: 0; color: var(--muted); font-size: 13px; }
main { max-width: 1100px; margin: 0 auto; padding: 22px 20px 60px; }
.card {
  background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 18px 20px; margin-bottom: 16px;
}
.card h2 {
  margin: 0 0 12px; font-size: 15px; font-weight: 650;
  display: flex; align-items: center; gap: 8px;
}
.card h2 .actions { margin-left: auto; display: flex; gap: 8px; }
.card h2 button { padding: 5px 12px; font-size: 12.5px; font-weight: 600; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
input[type=text], input[type=number], textarea {
  width: 100%; border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 10px; font-size: 14px; background: #fff; color: var(--ink);
}
input[type=file] { width: 100%; font-size: 13px; padding: 6px 0; }
textarea { min-height: 110px; resize: vertical; font-family: inherit; }
.row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 14px; }
button {
  border: 0; border-radius: 8px; padding: 10px 18px; font-size: 14px;
  cursor: pointer; background: var(--accent); color: #fff; font-weight: 600;
}
button.secondary { background: #eef2ff; color: var(--accent); }
button:disabled { opacity: .55; cursor: not-allowed; }
.hint { font-size: 12px; color: var(--muted); }
.file-meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
.badge {
  display: inline-block; padding: 1px 8px; border-radius: 999px;
  font-size: 12px; font-weight: 600; white-space: nowrap;
}
.badge.ok { background: #dcfae6; color: var(--ok); }
.badge.bad { background: #fee4e2; color: var(--bad); }
.badge.warn { background: #fef0c7; color: var(--warn); }
.badge.info { background: #e0eaff; color: var(--info); }
.badge.muted { background: #f2f4f7; color: var(--muted); }
.stats { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.stat {
  border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px;
  background: #fff; min-width: 96px;
}
.stat .n { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat .k { font-size: 12px; color: var(--muted); }
.stat.ok .n { color: var(--ok); }
.stat.bad .n { color: var(--bad); }
.stat.info .n { color: var(--info); }
table.grid {
  width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; background: #fff;
}
table.grid th, table.grid td {
  border-bottom: 1px solid var(--line); padding: 8px 10px; text-align: left;
}
table.grid th {
  background: #f9fafb; font-weight: 600; color: var(--muted); font-size: 12px;
  position: sticky; top: 0; z-index: 1; white-space: nowrap;
}
table.grid td.num { text-align: right; font-variant-numeric: tabular-nums; }
.table-wrap { overflow: auto; max-height: 420px; border: 1px solid var(--line); border-radius: 8px; }
a.page-link {
  color: var(--accent); cursor: pointer; text-decoration: underline;
  text-underline-offset: 2px; font-variant-numeric: tabular-nums;
}
.report-md {
  font-size: 13px; line-height: 1.7; background: #f9fafb;
  border: 1px solid var(--line); border-radius: 8px;
  padding: 16px; max-height: 520px; overflow: auto;
}
.report-md h1 { font-size: 17px; margin: 14px 0 8px; }
.report-md h2 { font-size: 15px; margin: 12px 0 6px; color: var(--accent); }
.report-md h3 { font-size: 13.5px; margin: 10px 0 4px; }
.report-md table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 12px; }
.report-md th, .report-md td { border: 1px solid var(--line); padding: 5px 8px; text-align: left; }
.report-md th { background: #eef1f5; font-weight: 600; }
.report-md code { background: #eef1f5; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.report-md pre { background: #eef1f5; padding: 10px; border-radius: 6px; overflow: auto; }
.report-md ul, .report-md ol { padding-left: 20px; margin: 6px 0; }
.report-md li { margin: 3px 0; }
.report-md strong { font-weight: 600; }
.report-md hr { border: 0; border-top: 1px solid var(--line); margin: 12px 0; }
.status-line { font-size: 13px; color: var(--muted); min-height: 20px; }
.status-line.busy { color: var(--accent); }
.status-line.err { color: var(--bad); }
#results[hidden], #checks[hidden], #analysis-card[hidden], #report-card[hidden], #qa-card[hidden], #manual-fallback[hidden], #auto-info[hidden] { display: none; }
.qa-item {
  border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px;
  margin-top: 10px; background: #fff;
}
.qa-item .q { font-weight: 600; font-size: 13.5px; margin-bottom: 6px; }
.qa-item .a { font-size: 13.5px; white-space: pre-wrap; word-break: break-word; }
.qa-item .meta { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.evidence-chip {
  display: inline-flex; align-items: center; gap: 4px;
  border: 1px solid var(--line); border-radius: 999px; padding: 2px 10px;
  font-size: 12px; background: #f9fafb; color: var(--info);
  cursor: default; text-decoration: none;
}
a.evidence-chip { cursor: pointer; color: var(--accent); border-color: #c7d7fe; }
.empty {
  padding: 28px; text-align: center; color: var(--muted); font-size: 14px;
}
.upload-area {
  border: 2px dashed var(--line); border-radius: 12px; padding: 24px;
  text-align: center; transition: border-color .15s;
}
.upload-area:hover, .upload-area.dragover { border-color: var(--accent); background: #f0f5ff; }
.auto-info {
  margin-top: 12px; padding: 10px 14px; border-radius: 10px;
  background: #f0fdf4; border: 1px solid #bbf7d0; font-size: 13.5px;
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.auto-info .tag {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  background: #dcfae6; color: var(--ok); font-weight: 600; font-size: 12.5px;
}
.auto-info .tag.warn { background: #fef0c7; color: var(--warn); }
.dir-tag {
  font-size: 11px; font-weight: 500; color: var(--accent);
  background: #e0eaff; padding: 2px 10px; border-radius: 999px;
  margin-left: 8px; vertical-align: middle;
}
#lightbox {
  position: fixed; inset: 0; background: rgba(16, 24, 40, .62); z-index: 50;
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
#lightbox[hidden] { display: none; }
#lightbox .box {
  background: #fff; border-radius: 12px; max-width: min(920px, 96vw);
  max-height: 92vh; display: flex; flex-direction: column; overflow: hidden;
}
#lightbox .bar {
  display: flex; align-items: center; gap: 10px; padding: 10px 14px;
  border-bottom: 1px solid var(--line); font-size: 13px;
}
#lightbox .bar button { padding: 6px 12px; font-size: 12.5px; }
#lightbox img, #lightbox canvas {
  max-width: 100%; max-height: calc(92vh - 52px); object-fit: contain;
  background: #f2f4f7; display: block; margin: 0 auto;
}
.lb-canvas-wrap { text-align: center; }
@media (max-width: 640px) {
  main { margin: 12px; }
  header { padding: 20px 16px; }
  h1 { font-size: 20px; }
  .stats { flex-wrap: wrap; }
  .stat { min-width: 44%; }
  .card { padding: 16px; }
  table.grid th, table.grid td { padding: 6px 8px; font-size: 12px; }
  .qa-item .row, .row { flex-wrap: wrap; }
  .grid { grid-template-columns: 1fr; }
  pre.report { font-size: 12px; }
}
@media print {
  header .sub, main > .card:first-child, .status-line,
  #report-card h2 .actions, #results h2 .dir-tag, #analysis-card h2 .dir-tag, #checks h2 .dir-tag { display: none !important; }
  body { background: #fff; }
  .card {
    border: 0; padding: 0 0 12px; margin: 0 0 8px; page-break-inside: avoid;
    box-shadow: none;
  }
  .table-wrap, pre.report { max-height: none; overflow: visible; border: 0; padding: 0; }
  table.grid th { position: static; }
  #lightbox { display: none !important; }
}
</style>
</head>
<body>
<header>
  <h1>金融投研智能体</h1>
  <p class="sub">选择财报或公告 → 提取字段 → 分析与核查。每个数字带页码与 evidence_id，点页码可回看原文页图。</p>
</header>
<main>
  <section class="card">
    <h2>上传材料</h2>
    <label for="task-type">任务</label>
    <select id="task-type" style="padding:8px;margin-bottom:12px">
      <option value="financial">财报分析 · 年报 / 半年报 / 季报</option>
      <option value="pledge">公告字段 · 股份质押</option>
      <option value="winning_bid">公告字段 · 中标</option>
      <option value="equity_change">公告字段 · 股权变动</option>
    </select>
    <label style="margin:8px 0"><input id="ocr" type="checkbox"> 启用扫描公告 OCR（需先配置语言包；识别字段需人工复核）</label>
    <div class="upload-area" id="upload-area">
      <input type="file" id="pdf" accept="application/pdf">
      <div class="file-meta" id="file-meta"></div>
    </div>
    <div id="auto-info" class="auto-info" hidden></div>
    <div style="margin-top:12px">
      <label for="comparison-pdfs">比较财报（可选，最多 4 份同公司报告；环比需要相邻季度累计数或单季值）</label>
      <input type="file" id="comparison-pdfs" accept="application/pdf" multiple>
    </div>
    <div id="manual-fallback" hidden>
      <div class="grid" style="margin-top:10px">
        <div>
          <label>证券代码</label>
          <input type="text" id="code" placeholder="6 位数字" maxlength="6" inputmode="numeric">
        </div>
        <div>
          <label>公司简称</label>
          <input type="text" id="name" placeholder="如 贵州茅台">
        </div>
        <div>
          <label>报告年度</label>
          <input type="number" id="year" placeholder="如 2024" min="2000" max="2100">
        </div>
      </div>
    </div>
    <div style="margin-top:12px">
      <label>研报草稿（可选 — 粘贴后自动做纠错核查）</label>
      <textarea id="draft" placeholder="粘贴一段投研草稿；不填则只做提取与分析"></textarea>
    </div>
    <div class="row">
      <button id="run">开始分析</button>
      <span class="hint">只需一份 PDF，公司 / 代码 / 年度自动识别。</span>
    </div>
    <div class="status-line" id="status"></div>
  </section>

  <section class="card" id="results" hidden>
    <h2>① 结构化提取 <span class="dir-tag">财务证据 / 方向一公告字段</span></h2>
    <div class="stats" id="stats"></div>
    <div class="table-wrap">
      <table class="grid" id="evidence">
        <thead>
          <tr>
            <th>证据 ID</th><th>指标</th><th>年度</th><th>数值</th><th>单位</th>
            <th>口径</th><th>调整</th><th>PDF 页</th><th>问题</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="card" id="analysis-card" hidden>
    <h2>② 财报分析 <span class="dir-tag">方向二 · 财报指标与同比</span></h2>
    <div class="table-wrap">
      <table class="grid" id="metrics">
        <thead>
          <tr>
            <th>指标</th><th>本年</th><th>上年</th><th>复算同比</th>
            <th>披露同比</th><th>核对</th><th>PDF 页</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
    <div id="signals" style="margin-top:12px"></div>
  </section>

  <section class="card" id="checks" hidden>
    <h2>③ 纠错核查 <span class="dir-tag">方向五 · 研报纠错</span></h2>
    <div id="checks-body"></div>
  </section>
  <section class="card" id="announcement-card" hidden>
    <h2>公告事件字段 <span class="dir-tag">方向一</span></h2>
    <div id="announcement-body"></div>
  </section>
  <section class="card" id="trace-card" hidden>
    <h2>执行轨迹</h2>
    <p class="hint">展示本次文件访问、规则、工具、计算与实际拆句 JSON；步骤可展开，证据可定位。</p>
    <div id="trace-body"></div>
  </section>

  <section class="card" id="qa-card" hidden>
    <h2>④ 受限问答</h2>
    <p class="hint" style="margin:0 0 10px">只基于本次已抽取的证据回答；每个数字挂 evidence_id，点页码可溯源。</p>
    <div class="row" style="margin-top:0">
      <input type="text" id="qa-input" placeholder="如：2024年营业收入是多少 / 营收同比 / 有什么问题"
             style="flex:1;min-width:220px" maxlength="500">
      <button id="qa-ask" type="button">提问</button>
    </div>
    <div class="status-line" id="qa-status"></div>
    <div id="qa-history"></div>
  </section>

  <section class="card" id="report-card" hidden>
    <h2>
      ⑤ 完整报告
      <span class="actions">
        <button class="secondary" id="export-md" type="button">导出 Markdown</button>
        <button class="secondary" id="export-pdf" type="button">导出 PDF</button>
      </span>
    </h2>
    <div class="report-md" id="report"></div>
  </section>
</main>

<div id="lightbox" hidden>
  <div class="box">
    <div class="bar">
      <strong id="lb-title">原文页图</strong>
      <span class="hint" id="lb-hint"></span>
      <button class="secondary" id="lb-close" type="button" style="margin-left:auto">关闭</button>
    </div>
    <div class="lb-canvas-wrap">
      <canvas id="lb-canvas"></canvas>
    </div>
  </div>
</div>

<script src="/trace.js"></script>
<script>
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmt = (v) => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 4 });
};
const fmtBig = (v) => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  const abs = Math.abs(n);
  if (abs >= 1e8) return (n / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 亿';
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
};
const scopeZh = (s) => ({
  consolidated: '合并', parent_shareholders: '归母', parent_company: '母公司',
  unknown: '未知',
}[s] || s || '—');
const adjZh = (a) => ({
  as_reported: '披露值', before: '调整前', after: '调整后',
}[a] || a || '—');

function renderMd(md) {
  // 极简 Markdown 渲染：标题/表格/加粗/代码/列表/分隔线；正文经 esc 后再注入标签
  const lines = String(md).split('\n');
  let html = '', inTable = false, inList = false, inCode = false;
  const closeList = () => { if (inList) { html += '</ul>'; inList = false; } };
  const closeTable = () => { if (inTable) { html += '</table>'; inTable = false; } };
  for (const line of lines) {
    if (line.startsWith('```')) { closeList(); closeTable(); html += inCode ? '</pre>' : '<pre>'; inCode = !inCode; continue; }
    if (inCode) { html += esc(line) + '\n'; continue; }
    if (/^\|/.test(line)) {
      closeList();
      const cells = line.split('|').slice(1, -1).map((c) => c.trim());
      if (/^[-: |]+$/.test(line)) continue;
      if (!inTable) { html += '<table>'; inTable = true; html += '<tr>' + cells.map((c) => `<th>${esc(c)}</th>`).join('') + '</tr>'; }
      else html += '<tr>' + cells.map((c) => `<td>${esc(c)}</td>`).join('') + '</tr>';
      continue;
    }
    closeTable();
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { closeList(); html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; continue; }
    if (/^---+$/.test(line.trim())) { closeList(); html += '<hr>'; continue; }
    const li = line.match(/^\s*[-*+]\s+(.*)/);
    if (li) { if (!inList) { html += '<ul>'; inList = true; } html += `<li>${inline(li[1])}</li>`; continue; }
    const oli = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (oli) { if (!inList) { html += '<ul>'; inList = true; } html += `<li>${inline(oli[1])}</li>`; continue; }
    closeList();
    if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  if (inCode) html += '</pre>';
  closeList(); closeTable();
  return html;
}
function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}
const badge = (s) => {
  const map = {
    'match': 'ok', '一致': 'ok', '证据支持': 'ok', 'evidence_supported': 'ok',
    'mismatch': 'bad', '不一致，需复核': 'bad', '确认错误': 'bad', 'confirmed_error': 'bad',
    '证据不足': 'info', '模型判断': 'info',
    '口径冲突／需人工复核': 'warn', 'needs_review': 'warn',
  };
  const cls = map[s] || 'info';
  return `<span class="badge ${cls}">${esc(s || '—')}</span>`;
};
const trackBadge = (track) => {
  const map = {
    deterministic: ['A 确定', 'ok'],
    model: ['B 模型', 'info'],
    review: ['C 人工', 'warn'],
  };
  const [text, cls] = map[track] || ['C 人工', 'muted'];
  return `<span class="badge ${cls}">${text}</span>`;
};
const pageCell = (row) => {
  const page = row.page ?? '—';
  const img = row.page_image;
  if (!img) return esc(String(page));
  const boxes = [];
  if (row.value_bbox && row.value_bbox.length >= 4) {
    boxes.push({ x0: row.value_bbox[0], y0: row.value_bbox[1], x1: row.value_bbox[2], y1: row.value_bbox[3], kind: 'value' });
  }
  if (row.label_bbox && row.label_bbox.length >= 4) {
    boxes.push({ x0: row.label_bbox[0], y0: row.label_bbox[1], x1: row.label_bbox[2], y1: row.label_bbox[3], kind: 'label' });
  }
  const bboxAttr = boxes.length ? ` data-bbox='${esc(JSON.stringify(boxes))}'` : '';
  return `<a class="page-link" href="/img/${esc(img)}" data-img="${esc(img)}" data-page="${esc(String(page))}"${bboxAttr}>${esc(String(page))}</a>`;
};
function setStatus(text, cls) {
  const el = $('#status');
  el.textContent = text || '';
  el.className = 'status-line' + (cls ? ' ' + cls : '');
}
function openLightbox(img, page, bboxes) {
  $('#lb-title').textContent = '原文页图 · 第 ' + page + ' 页';
  $('#lb-hint').textContent = bboxes && bboxes.length ? '蓝=数值 · 绿=标签' : '点页码可回看材料原页';
  const canvas = $('#lb-canvas');
  const ctx = canvas.getContext('2d');
  const image = new Image();
  image.onload = () => {
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    ctx.drawImage(image, 0, 0);
    if (bboxes && bboxes.length) {
      for (const b of bboxes) {
        if (!b || b.length < 4) continue;
        ctx.strokeStyle = b.kind === 'label' ? '#22c55e' : '#3b82f6';
        ctx.lineWidth = Math.max(2, image.naturalWidth / 400);
        if (b.kind === 'label') ctx.setLineDash([6, 4]);
        else ctx.setLineDash([]);
        // bbox 是 PDF pt 坐标，页图 2x 渲染，按 2 倍映射
        ctx.strokeRect(b.x0 * 1.6, b.y0 * 1.6, (b.x1 - b.x0) * 1.6, (b.y1 - b.y0) * 1.6);
      }
      ctx.setLineDash([]);
    }
  };
  image.src = '/img/' + img;
  $('#lightbox').hidden = false;
}
function closeLightbox() {
  $('#lightbox').hidden = true;
  $('#lb-img').removeAttribute('src');
}

let autoInfo = {};  // 从 PDF 自动识别到的字段

async function prefill() {
  const file = $('#pdf').files[0];
  if (!file) return;
  const form = new FormData();
  form.append('pdf', file);
  setStatus('正在从 PDF 识别公司信息…', 'busy');
  try {
    const res = await fetch('/api/prefill', { method: 'POST', body: form });
    const data = await res.json();
    autoInfo = data;
    const tags = [];
    if (data.name) tags.push(`<span class="tag">${esc(data.name)}</span>`);
    if (data.code) tags.push(`<span class="tag">${esc(data.code)}</span>`);
    if (data.year) tags.push(`<span class="tag">${esc(data.year)} ${esc(({annual:'年报',half:'半年报',quarter:'季报'})[data.report_kind] || '财务报告')}</span>`);
    const missing = [];
    if (!data.code) missing.push('代码');
    if (!data.name) missing.push('公司');
    if (!data.year) missing.push('年度');
    if (missing.length) {
      tags.push(`<span class="tag warn">未识别：${esc(missing.join(' / '))}</span>`);
      // 识别不全 → 露出手填那几格
      $('#manual-fallback').hidden = false;
      if (data.code) $('#code').value = data.code;
      if (data.name) $('#name').value = data.name;
      if (data.year) $('#year').value = data.year;
    } else {
      $('#manual-fallback').hidden = true;
    }
    $('#auto-info').innerHTML = '已识别：' + tags.join(' ');
    $('#auto-info').hidden = false;
    setStatus(missing.length ? '部分字段未识别，请手动补充' : '已自动识别，点「开始分析」即可', '');
  } catch (e) {
    $('#manual-fallback').hidden = false;
    setStatus('自动识别失败，请手动填写', 'err');
  }
}

function onFilePicked() {
  autoInfo = {};
  for (const id of ['results', 'analysis-card', 'checks', 'report-card', 'qa-card', 'announcement-card', 'trace-card', 'auto-info']) $('#' + id).hidden = true;
  const file = $('#pdf').files[0];
  if (!file) {
    $('#file-meta').textContent = '';
    $('#auto-info').hidden = true;
    $('#manual-fallback').hidden = true;
    return;
  }
  const kb = file.size / 1024;
  const size = kb > 1024 ? (kb / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(kb)) + ' KB';
  $('#file-meta').textContent = file.name + ' · ' + size;
  if ($('#task-type').value === 'financial') prefill();
  else setStatus('已选择公告，点「开始分析」提取事件字段', '');
}

async function run() {
  const file = $('#pdf').files[0];
  const draft = $('#draft').value;
  if (!file) { setStatus('请选择财报或公告 PDF', 'err'); return; }

  // 自动识别到的（或手填的）字段
  const code = ($('#manual-fallback').hidden ? (autoInfo.code || '') : ($('#code').value.trim() || autoInfo.code || '')).trim();
  const name = ($('#manual-fallback').hidden ? (autoInfo.name || '') : ($('#name').value.trim() || autoInfo.name || '')).trim();
  const year = ($('#manual-fallback').hidden ? (autoInfo.year || '') : ($('#year').value.trim() || autoInfo.year || '')).toString().trim();

  const form = new FormData();
  form.append('pdf', file);
  if (code) form.append('code', code);
  if (name) form.append('name', name);
  if (year) form.append('year', year);
  form.append('draft', draft || '');
  form.append('event_type', $('#task-type').value);
  form.append('ocr', $('#ocr').checked ? 'true' : 'false');
  for (const comparison of $('#comparison-pdfs').files) form.append('comparison_pdf', comparison);

  $('#run').disabled = true;
  // 分段进度提示，避免 7 秒只有单调「请稍候」
  const stages = ['正在识别 PDF 身份…', '正在抽取指标…', '正在渲染页图…', '正在核算同比与核查…'];
  let stageIdx = 0;
  setStatus(stages[0], 'busy');
  const stageTimer = setInterval(() => {
    stageIdx = Math.min(stageIdx + 1, stages.length - 1);
    setStatus(stages[stageIdx] + '（已耗时 ' + (stageIdx * 2) + 's+）', 'busy');
  }, 2500);
  for (const id of ['results', 'analysis-card', 'checks', 'report-card', 'qa-card', 'announcement-card', 'trace-card']) {
    $('#' + id).hidden = true;
  }
  $('#qa-history').innerHTML = '';
  try {
    const endpoint = $('#task-type').value === 'financial' ? '/api/analyze' : '/api/announcement';
    const res = await fetch(endpoint, { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok || data.error) {
      setStatus(data.error || ('分析失败 HTTP ' + res.status), 'err');
      return;
    }
    render(data);
    const c = data.counts || {};
    setStatus(data.announcement ? `完成：公告事件 ${c.events || 0} 项 · 字段证据 ${data.evidence.length} 条 · 待复核 ${data.issues.length} 条` : `完成：提取证据 ${data.evidence.length} 条 · 同比一致 ${c.match || 0} · 不一致 ${c.mismatch || 0} · 待复核 ${data.issues.length} 条`, '');
  } catch (e) {
    setStatus('请求失败：' + e, 'err');
  } finally {
    clearInterval(stageTimer);
    $('#run').disabled = false;
  }
}

function render(data) {
  window.__last = data;
  const c = data.counts || {};

  // ── ① 结构化提取：证据表 + 统计 ──
  $('#stats').innerHTML = (data.announcement ? [
    ['字段证据', (data.evidence || []).length, 'info'],
    ['公告事件', c.events || 0, 'info'],
    ['必填缺失', c.required_missing || 0, 'warn'],
    ['冲突字段', c.conflicts || 0, 'bad'],
    ['待复核', (data.issues || []).length, (data.issues || []).length ? 'warn' : 'ok'],
  ] : [
    ['提取证据', (data.evidence || []).length, 'info'],
    ['同比一致', c.match || 0, 'ok'],
    ['同比不一致', c.mismatch || 0, 'bad'],
    ['缺披露值', c.unverified || 0, ''],
    ['待复核', (data.issues || []).length, (data.issues || []).length ? 'bad' : 'ok'],
  ]).map(([k, n, cls]) => `<div class="stat ${cls}"><div class="n">${n}</div><div class="k">${k}</div></div>`).join('');

  const evBody = (data.evidence || []).map((e) => `<tr>
    <td>${esc(e.evidence_id)}</td>
    <td>${esc(window.announcementFieldLabels[e.metric_name] || e.metric_name || e.metric)}</td>
    <td>${esc(e.period_start && e.period_end ? e.period_start + ' 至 ' + e.period_end : e.period_year)}</td>
    <td class="num">${fmt(e.value)}</td>
    <td>${esc(e.unit || '—')}</td>
    <td>${esc(scopeZh(e.scope))}</td>
    <td>${esc(adjZh(e.adjustment))}</td>
    <td class="num">${pageCell(e)}</td>
    <td>${(e.issues && e.issues.length) ? badge(String(e.issues.join('；'))) : '<span class="badge ok">干净</span>'}</td>
  </tr>`).join('');
  $('#evidence tbody').innerHTML = evBody ||
    `<tr><td colspan="9" class="empty">没有提取到证据</td></tr>`;
  $('#results').hidden = false;

  // ── ② 财报分析：指标与同比 ──
  const tbody = $('#metrics tbody');
  tbody.innerHTML = (data.rows || []).map((r) => {
    const yoy = r.yoy && r.yoy.status === 'ok' ? fmt(r.yoy.value) + '%' : (r.yoy ? r.yoy.status : '—');
    const check = r.reported_yoy_check;
    const outcome = check
      ? (check.status === 'match' ? '一致' : '不一致，需复核')
      : '未核对（缺披露值）';
    return `<tr>
      <td>${esc(r.metric_name || r.metric)}<br><small>${esc(r.period_start || '')} 至 ${esc(r.period_end || '')}</small></td>
      <td class="num">${fmtBig(r.current)}</td>
      <td class="num">${fmtBig(r.previous)}</td>
      <td class="num">${esc(yoy)}</td>
      <td class="num">${check ? fmt(check.reported) + '%' : '—'}</td>
      <td>${badge(outcome)}</td>
      <td class="num">${pageCell(r)}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="7" class="empty">没有抽出指标</td></tr>`;

  const sigs = data.signals || [];
  $('#signals').innerHTML = sigs.length
    ? sigs.map(s => `<div class="hint"><strong>【${esc(s.epistemic_type || '事实')}】</strong>${esc(s.description)} ${s.value == null ? '' : fmt(s.value)}${esc(s.unit || '')}<br>${esc(s.interpretation || '')}</div>`).join('')
    : '';
  const qoq = data.qoq_rows || [];
  if (qoq.length) $('#signals').innerHTML += '<h3>单季环比</h3>' + qoq.map(r => `<p>${esc(r.metric)} · ${esc(r.period_start)} 至 ${esc(r.period_end)}：${r.qoq.status === 'ok' ? fmt(r.qoq.value) + '%' : esc(r.qoq.reason ?? r.qoq.status)}</p>`).join('');
  $('#analysis-card').hidden = false;

  // ── ③ 纠错核查 ──
  if (data.checks && data.checks.status) {
    const box = $('#checks-body');
    if (data.checks.status === 'completed') {
      const counts = data.checks.counts || {};
      const head = Object.entries(counts).map(([k, v]) => `${esc(k)} ${v}`).join(' · ');
      const modeMap = {
        native_tool_calls: ['原生工具循环', 'ok'],
        json_multi_step: ['JSON 多步协议', 'ok'],
        fallback_single_shot_tools_preserved: ['单次拆解 · 工具裁决已保留', 'warn'],
        fallback_single_shot: ['单次拆解（回退）', 'warn'],
        local_supplemental: ['本地补充核查', 'info'],
      };
      const [modeText, modeCls] = modeMap[data.checks.mode] || [data.checks.mode || '—', 'muted'];
      const nTools = data.checks.tools_used || 0;
      const toolNames = (data.checks.tool_names || []).slice(0, 8).join('、');
      const agentLine = `<div class="hint" style="margin-bottom:6px">
        <span class="badge ${modeCls}">${esc(modeText)}</span>
        <span class="badge info">工具调用 ${nTools} 次</span>
        ${data.checks.model ? `<span class="badge muted">${esc(data.checks.model)}</span>` : ''}
        ${toolNames ? `<br><span class="muted">工具：${esc(toolNames)}</span>` : ''}
      </div>`;
      const rows = (data.checks.checks || []).map((c) => `<tr>
        <td>${esc(c.claim_id)}</td>
        <td>${trackBadge(c.track)}</td>
        <td>${esc((c.original_sentence || '').slice(0, 80))}</td>
        <td>${badge(c.status)}</td>
        <td>${esc(c.reason || c.reason_code || '—')}${c.verification_scope ? '<br>'+esc(c.verification_scope) : ''}${c.correction ? '<details><summary>修改片段</summary>'+esc(c.correction.before)+' → '+esc(c.correction.after)+'<p>'+esc(c.correction.revised_sentence || '需人工调整句子')+'</p></details>' : ''}</td>
      </tr>`).join('');
      box.innerHTML = `<div class="hint" style="margin-bottom:8px">${esc(data.checks.reason || '')}<br>双轨：A 确定（本地裁决）· B 模型（只解释）· C 人工（需复核）。${head || '已出结果'}</div>
        ${agentLine}
        <div class="table-wrap"><table class="grid">
          <thead><tr><th>编号</th><th>轨道</th><th>原句</th><th>结论</th><th>说明</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>`;
    } else {
      box.innerHTML = `<div class="empty">${esc(data.checks.reason || data.checks.status)}</div>`;
    }
    $('#checks').hidden = false;
  } else {
    $('#checks-body').innerHTML = '<div class="empty">未提供研报草稿 — 粘贴草稿后可做纠错核查</div>';
    $('#checks').hidden = false;
  }

  // ── ④⑤ 报告与问答 ──
  $('#report').innerHTML = renderMd(data.report_md || '(无报告)');
  $('#report-card').hidden = false;
  $('#qa-history').innerHTML = '';
  $('#qa-input').value = '';
  $('#qa-card').hidden = false;
  if (data.checks && data.checks.reason) $('#checks-body').insertAdjacentHTML('afterbegin', `<p class="hint">${esc(data.checks.reason)}</p>`);
  if (data.announcement) {
    $('#announcement-body').innerHTML = (data.announcement.events || []).map(e => `<h3>${esc(e.event_name)} · ${esc(window.announcementStatusLabels[e.status] || e.status)}</h3><p>必填缺失：${esc(e.missing_required.join('、') || '无')}；冲突：${esc(e.conflicts.join('、') || '无')}</p><table class="grid"><thead><tr><th>字段</th><th>原值</th><th>规范值</th><th>状态</th><th>原页位置</th></tr></thead><tbody>${Object.entries(e.fields).map(([k,v]) => `<tr><td>${esc(window.announcementFieldLabels[k] || k)}</td><td>${esc(v.value ?? '缺失')}</td><td>${esc(v.normalized_value ?? '—')} ${esc(v.normalized_unit || '')}</td><td>${esc(window.announcementStatusLabels[v.status] || v.status)}</td><td>${(data.evidence || []).filter(f => f.evidence_id.startsWith(e.event_id + '_' + k + '_')).map(pageCell).join(' ')}</td></tr>`).join('')}</tbody></table>`).join('') || '<p>未找到可明确提取的字段。</p>';
    $('#announcement-card').hidden = false;
    $('#analysis-card').hidden = true;
    $('#qa-card').hidden = true;
  }
  window.renderExecutionTrace($('#trace-body'), data.trace || [], id => {
    const e = (data.evidence || []).find(e => e.evidence_id === id);
    if (e && e.page_image) {
      const boxes = e.value_bbox ? [{x0:e.value_bbox[0], y0:e.value_bbox[1], x1:e.value_bbox[2], y1:e.value_bbox[3], kind:'value'}] : [];
      openLightbox(e.page_image, e.page, boxes);
    }
  });
  $('#trace-card').hidden = false;
}

function qaBadge(status) {
  const map = {
    ok: ['ok', '已答'],
    insufficient_evidence: ['info', '证据不足'],
    out_of_scope: ['warn', '超范围'],
  };
  const [cls, text] = map[status] || ['muted', status || '—'];
  return `<span class="badge ${cls}">${esc(text)}</span>`;
}

function renderQa(item) {
  const cites = (item.citations || []).map((c) => {
    const label = `${esc(c.evidence_id)} · ${esc(c.metric_name || c.metric || '')}${c.page ? ' · p' + esc(String(c.page)) : ''}`;
    if (c.page_image) {
      return `<a class="evidence-chip page-link" href="/img/${esc(c.page_image)}" data-img="${esc(c.page_image)}" data-page="${esc(String(c.page ?? ''))}">${label}</a>`;
    }
    return `<span class="evidence-chip">${label}</span>`;
  }).join('');
  const ids = (item.evidence_ids || []).map((id) => `<code style="font-size:11px">${esc(id)}</code>`).join(' ');
  return `<div class="qa-item">
    <div class="q">问：${esc(item.question)}</div>
    <div class="a">${esc(item.answer)}</div>
    <div class="meta">${qaBadge(item.status)}${cites || (ids ? '<span class="hint">证据：' + ids + '</span>' : '')}</div>
  </div>`;
}

async function qaAsk() {
  const q = $('#qa-input').value.trim();
  if (!q) { $('#qa-status').textContent = '先输入问题'; $('#qa-status').className = 'status-line err'; return; }
  if (!window.__last) { $('#qa-status').textContent = '请先完成一次分析'; $('#qa-status').className = 'status-line err'; return; }
  $('#qa-ask').disabled = true;
  $('#qa-status').textContent = '正在基于证据作答…';
  $('#qa-status').className = 'status-line busy';
  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      $('#qa-status').textContent = data.error || ('提问失败 HTTP ' + res.status);
      $('#qa-status').className = 'status-line err';
      return;
    }
    const item = { question: q, ...data };
    $('#qa-history').insertAdjacentHTML('afterbegin', renderQa(item));
    $('#qa-input').value = '';
    const n = (data.evidence_ids || []).length;
    $('#qa-status').textContent = n ? `已答，引用 ${n} 条证据` : '已答（无引用证据）';
    $('#qa-status').className = 'status-line';
  } catch (e) {
    $('#qa-status').textContent = '请求失败：' + e;
    $('#qa-status').className = 'status-line err';
  } finally {
    $('#qa-ask').disabled = false;
  }
}

function exportMarkdown() {
  const data = window.__last;
  if (!data) { setStatus('还没有分析结果可导出', 'err'); return; }
  const lines = [];
  const mat = (data.materials || [])[0] || {};
  lines.push('# 金融投研智能体 · 分析报告');
  lines.push('');
  lines.push(`- 公司：${mat.company_name || ''}（${mat.company_code || ''}）`);
  lines.push(`- 报告年度：${mat.report_year || ''}`);
  lines.push(`- 运行号：${data.run_id || ''}`);
  lines.push(`- 生成时间：${new Date().toLocaleString('zh-CN')}`);
  lines.push('');
  lines.push('## 指标与同比核对');
  lines.push('');
  lines.push('| 指标 | 本年 | 上年 | 复算同比 | 披露同比 | 核对 | PDF页 |');
  lines.push('| --- | ---: | ---: | ---: | ---: | --- | ---: |');
  (data.rows || []).forEach((r) => {
    const yoy = r.yoy && r.yoy.status === 'ok' ? fmt(r.yoy.value) + '%' : (r.yoy ? r.yoy.status : '—');
    const check = r.reported_yoy_check;
    const outcome = check
      ? (check.status === 'match' ? '一致' : '不一致，需复核')
      : '未核对（缺披露值）';
    lines.push(`| ${r.metric_name || r.metric} | ${fmtBig(r.current)} | ${fmtBig(r.previous)} | ${yoy} | ${check ? fmt(check.reported) + '%' : '—'} | ${outcome} | ${r.page ?? '—'} |`);
  });
  const sigs = data.signals || [];
  if (sigs.length) {
    lines.push('', '## 辅助观察', '');
    sigs.forEach((s) => lines.push(`- ${s.description}${s.value ? '：' + s.value + (s.unit || '') : ''}`));
  }
  if (data.checks && data.checks.status === 'completed') {
    lines.push('', '## 草稿核查', '');
    const counts = data.checks.counts || {};
    if (Object.keys(counts).length) {
      lines.push('结论分布：' + Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(' · '), '');
    }
    lines.push('| 编号 | 轨道 | 原句 | 结论 | 说明 |');
    lines.push('| --- | --- | --- | --- | --- |');
    (data.checks.checks || []).forEach((c) => {
      const sentence = String(c.original_sentence || '').replace(/\|/g, '\\|').slice(0, 120);
      const reason = String(c.reason || c.reason_code || '—').replace(/\|/g, '\\|');
      const track = { deterministic: 'A 确定', model: 'B 模型', review: 'C 人工' }[c.track] || 'C 人工';
      lines.push(`| ${c.claim_id} | ${track} | ${sentence} | ${c.status} | ${reason} |`);
    });
  }
  if ((data.evidence || []).length) {
    lines.push('', '## 证据明细', '');
    lines.push('| 证据ID | 指标 | 年度 | 数值 | 单位 | 口径 | 页 |');
    lines.push('| --- | --- | ---: | ---: | --- | --- | ---: |');
    data.evidence.forEach((e) => {
      lines.push(`| ${e.evidence_id} | ${e.metric_name || e.metric} | ${e.period_year ?? '—'} | ${e.value ?? '—'} | ${e.unit || '—'} | ${scopeZh(e.scope)} | ${e.page ?? '—'} |`);
    });
  }
  lines.push('', '## 核查报告', '');
  lines.push(data.report_md || '');
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `分析报告_${mat.company_code || 'report'}_${mat.report_year || ''}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
  setStatus('Markdown 已下载', '');
}

function exportPdf() {
  if (!window.__last) { setStatus('还没有分析结果可导出', 'err'); return; }
  setStatus('已打开打印窗口，请在目标里选「另存为 PDF」', '');
  window.print();
}

$('#run').addEventListener('click', run);
$('#pdf').addEventListener('change', onFilePicked);
$('#export-md').addEventListener('click', exportMarkdown);
$('#export-pdf').addEventListener('click', exportPdf);
$('#qa-ask').addEventListener('click', qaAsk);
$('#qa-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); qaAsk(); }
});
$('#lb-close').addEventListener('click', closeLightbox);
$('#lightbox').addEventListener('click', (e) => {
  if (e.target.id === 'lightbox') closeLightbox();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeLightbox();
});
document.addEventListener('click', (e) => {
  const a = e.target.closest('a.page-link');
  if (!a) return;
  e.preventDefault();
  let boxes = [];
  try { boxes = JSON.parse(a.dataset.bbox || '[]'); } catch (_) {}
  openLightbox(a.dataset.img, a.dataset.page || '', boxes);
});

// 拖放上传
const upArea = $('#upload-area');
upArea.addEventListener('dragover', (e) => { e.preventDefault(); upArea.classList.add('dragover'); });
upArea.addEventListener('dragleave', () => upArea.classList.remove('dragover'));
upArea.addEventListener('drop', (e) => {
  e.preventDefault();
  upArea.classList.remove('dragover');
  const f = e.dataTransfer.files && e.dataTransfer.files[0];
  if (!f) return;
  if (!f.name.toLowerCase().endsWith('.pdf')) { setStatus('请拖入 PDF 文件', 'err'); return; }
  const input = $('#pdf');
  const dt = new DataTransfer();
  dt.items.add(f);
  input.files = dt.files;
  onFilePicked();
});
</script>
</body>
</html>
"""


# ---------- HTTP ----------

def _json(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(blob)))
    handler.end_headers()
    handler.wfile.write(blob)


def _parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict, bytes]:
    """只支持浏览器标准 multipart/form-data，够薄页面用。"""
    ctype = handler.headers.get("Content-Type", "")
    match = re.search(r'boundary="?([^";]+)"?', ctype)
    if not match:
        raise ValueError("缺少 multipart boundary")
    boundary = b"--" + match.group(1).encode()
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0 or length > MAX_UPLOAD:
        raise ValueError("请求体为空或超过 40MiB")
    body = handler.rfile.read(length)
    fields: dict = {}
    pdf = b""
    for part in body.split(boundary):
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, content = part.split(b"\r\n\r\n", 1)
        head_text = head.decode("utf-8", errors="replace")
        name_m = re.search(r'name="([^"]+)"', head_text)
        if not name_m:
            continue
        name = name_m.group(1)
        if name == "pdf" or 'filename="' in head_text:
            if name == "pdf":
                pdf = content
            elif name == "comparison_pdf":
                fields.setdefault("_comparison_pdfs", []).append(content)
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields, pdf


class Handler(BaseHTTPRequestHandler):
    server_version = "FinAgentWebUI/1.0"

    def log_message(self, fmt, *args):
        print(f"[webui] {self.address_string()} {fmt % args}")

    def do_GET(self):
        if self.path == "/trace.js":
            blob = (ROOT / "docs" / "trace.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        if self.path in ("/", "/index.html"):
            blob = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        if self.path.startswith("/img/"):
            self._serve_image(self.path[len("/img/"):])
            return
        _json(self, 404, {"error": "not found"})

    def _serve_image(self, name: str) -> None:
        # 只回 results/ 根下的页图 PNG，文件名白名单，杜绝路径穿越
        if not PAGE_PNG.fullmatch(name):
            _json(self, 404, {"error": "not found"})
            return
        path = ROOT / "results" / name
        if not path.is_file():
            _json(self, 404, {"error": "页图不存在"})
            return
        blob = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    def _ask_json(self) -> None:
        """受限问答：只认上次分析的 facts，答案必挂 evidence_id。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64 * 1024:
            _json(self, 400, {"error": "请求体为空或过大"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            _json(self, 400, {"error": "请求体不是合法 JSON"})
            return
        question = (body.get("question") or "").strip() if isinstance(body, dict) else ""
        if not question:
            _json(self, 400, {"error": "缺少 question"})
            return
        client, run = None, None
        try:
            from llm_check import LLMClient
            client = LLMClient.from_environment()
            run = Run(ROOT, "webui-ask", {"command": "webui-ask", "question_len": len(question)})
        except Exception:  # noqa: BLE001 — 未配模型时降级纯确定性问答
            client, run = None, None
        try:
            with _LAST_LOCK:
                facts_snapshot = list(LAST_FACTS)
            out = qa_answer(question, facts_snapshot, client=client, run=run,
                            use_llm=client is not None)
            if run is not None:
                run.finish(status="ok", question_len=len(question),
                           answer_status=out.get("status"),
                           evidence_count=len(out.get("evidence_ids") or []))
            _json(self, 200, out)
        except Exception as exc:
            traceback.print_exc()
            _json(self, 400, {"error": f"{type(exc).__name__}: {exc}"})

    def _client_ip(self) -> str:
        fwd = self.headers.get("X-Forwarded-For") or ""
        return fwd.split(",")[0].strip() or self.client_address[0]

    def do_POST(self):
        try:
            ip = self._client_ip()
            if not _rate_limit(ip):
                _json(self, 429, {"error": "请求过于频繁，请稍后再试"})
                return
            ctype = self.headers.get("Content-Type") or ""
            if self.path == "/api/ask" and ctype.startswith("application/json"):
                self._ask_json()
                return
            fields, pdf = _parse_multipart(self)
            if self.path == "/api/prefill":
                if not pdf:
                    _json(self, 400, {"error": "缺少 PDF"})
                    return
                _json(self, 200, guess_identity(pdf))
                return
            if self.path == "/api/announcement":
                if not pdf:
                    _json(self, 400, {"error": "缺少 PDF"})
                    return
                if not _ANALYZE_LOCK.acquire(blocking=False):
                    _json(self, 429, {"error": "已有分析任务在进行中，请稍后再试"})
                    return
                try:
                    _json(self, 200, run_announcement_pipeline(pdf, fields.get("event_type", "auto"), ocr=fields.get("ocr") == "true"))
                finally:
                    _ANALYZE_LOCK.release()
                return
            if self.path != "/api/analyze":
                _json(self, 404, {"error": "not found"})
                return
            if not pdf:
                _json(self, 400, {"error": "缺少 PDF"})
                return
            # 字段全可选：没带就从 PDF 自动提取（用户只需丢文件）
            code = (fields.get("code") or "").strip()
            name = (fields.get("name") or "").strip()
            year_raw = (fields.get("year") or "").strip()
            draft = fields.get("draft") or ""
            auto = guess_identity(pdf)
            if not code:
                code = auto.get("code") or ""
            if not name:
                name = auto.get("name") or ""
            if not year_raw:
                year_raw = str(auto.get("year") or "")
            if not re.fullmatch(r"\d{6}", code):
                _json(self, 400, {"error": "未能从 PDF 识别证券代码，请检查文件或手动补充财报身份"})
                return
            if not name:
                _json(self, 400, {"error": "未能从 PDF 识别公司名称，请检查文件或手动补充财报身份"})
                return
            if not re.fullmatch(r"\d{4}", year_raw):
                _json(self, 400, {"error": "未能从 PDF 识别报告年度，请检查文件或手动补充财报身份"})
                return
            if not _ANALYZE_LOCK.acquire(blocking=False):
                _json(self, 429, {"error": "已有分析任务在进行中，请稍后再试"})
                return
            try:
                _cleanup_work_dir()
                payload = run_pipeline(pdf, code, name, int(year_raw),
                                       draft=draft or None, comparison_blobs=fields.get("_comparison_pdfs", []))
                payload["auto_detected"] = auto
                _json(self, 200, payload)
            finally:
                _ANALYZE_LOCK.release()
        except Exception as exc:
            traceback.print_exc()
            _json(self, 400, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    import os
    parser = argparse.ArgumentParser(description="金融投研智能体 · 本地薄页面")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT") or 8765))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"薄页面已启动：http://{args.host}:{args.port}/")
    print("Ctrl+C 停止。分析流水线与 CLI live 相同，结果落 results/。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
