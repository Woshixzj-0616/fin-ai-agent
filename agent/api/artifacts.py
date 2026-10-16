"""运行源码快照与复现包；只收集明确的源码和当前运行文件。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from urllib.parse import urlsplit

try:
    from backend_runtime import load_config
    from runtime.redaction import redact_result
except ModuleNotFoundError:
    from agent.backend_runtime import load_config
    from agent.runtime.redaction import redact_result


SOURCE_ROOT = Path(__file__).resolve().parents[2]
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
SOURCE_PATTERNS = (
    "agent/**/*.py", "scripts/**/*.py", "web/**/*.js", "web/**/*.css",
    "web/**/*.html", "web/**/*.svg", "skills/**/*.md", "skills/**/*.json",
    "tests/**/*.mjs", "docs/**/*.md", ".github/workflows/*.yml",
)
SOURCE_FILES = (
    "requirements.txt", "README.md", "render.yaml", "agent/backend_config.json", "docs/运行说明.md",
    "docs/第三方与来源清单.md", "docs/app.js", "docs/trace.js", "docs/index.html", "docs/style.css",
)


def _json(value) -> bytes:
    return (json.dumps(redact_result(value), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def capture_sources(context, *, source_root: Path = SOURCE_ROOT) -> dict:
    """保存执行时的源码字节及指纹，Git 提交号仅作为辅助身份。"""
    root = Path(source_root).resolve()
    paths = {root / name for name in SOURCE_FILES}
    for pattern in SOURCE_PATTERNS:
        paths.update(root.glob(pattern))
    sources = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(paths)
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)
    }
    hashes = {name: hashlib.sha256(blob).hexdigest() for name, blob in sources.items()}
    versions = {"python": platform.python_version()}
    for distribution in ("PyMuPDF", "httpx"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "unavailable"
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=3, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    manifest = {
        "schema_version": 1, "run_id": context.id, "git_commit": commit,
        "versions": versions, "code_sha256": hashes,
        "effective_runtime_config": load_config(),
        "model_configuration": {
            "configured": all(bool(os.environ.get(name)) for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")),
            "requested_model": os.environ.get("LLM_MODEL") or None,
            "provider_host": urlsplit(os.environ.get("LLM_BASE_URL", "")).hostname,
            "response_format": os.environ.get("LLM_FORMAT", "json_schema"),
            "stream": os.environ.get("LLM_STREAM", "").lower() not in {"0", "false", "no", "off"},
        },
        "source_fingerprint": hashlib.sha256(_json(hashes)).hexdigest(),
        "identity_basis": "执行时归档的源码字节；提交号不包含未提交修改，以文件指纹为准",
    }
    destination = context.output("source_code.zip")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".zip", delete=False) as stream:
            temporary = Path(stream.name)
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            for name, blob in sources.items():
                archive.writestr(name, blob)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    context.output("provenance.json").write_bytes(_json(manifest))
    context.event("source_snapshot", actor="program", title="保存复现源码",
                  summary=f"保存 {len(sources)} 份源码与依赖版本",
                  payload={"source_fingerprint": manifest["source_fingerprint"],
                           "file_count": len(sources), "git_commit": commit, "versions": versions})
    return manifest


def build_reproduction_bundle(context, events: list) -> bytes:
    """导出完整结构化产物、原始输入、源码和日志，移除会话访问凭证。"""
    folder = context.folder.resolve()
    entries: dict[str, bytes] = {}
    total = 0
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(folder):
            continue
        relative = path.relative_to(folder)
        if path.name in {"run.json", "events.jsonl"} or path.suffix in {".tmp", ".zip"}:
            continue
        if path.suffix.lower() not in {".pdf", ".png", ".json", ".jsonl", ".md", ".txt", ".csv"}:
            continue
        total += path.stat().st_size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("复现包超过 256MiB，请从本地运行目录复制产物")
        blob = path.read_bytes()
        if path.suffix == ".json":
            blob = _json(json.loads(blob))
        entries[f"run/{relative.as_posix()}"] = blob
    source = context.output("source_code.zip")
    if source.is_file() and not source.is_symlink():
        total += source.stat().st_size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("复现包超过 256MiB，请从本地运行目录复制产物")
        entries["source_code.zip"] = source.read_bytes()
    state = context.snapshot(result_transform=lambda _: None)
    state.pop("session_id", None)
    state.pop("events_path", None)
    state.pop("result", None)
    # snapshot 的参数面向状态展示，草稿可能较长，交付需保留完整输入。
    state["parameters"] = redact_result(dict(context.parameters))
    entries["run/run.json"] = _json(state)
    entries["run/events.jsonl"] = ("".join(event.to_json() + "\n" for event in events)).encode("utf-8")
    entries["run/explanation.json"] = _json(build_explanation(context, events))
    result = context.snapshot()["result"]
    if isinstance(result, dict) and result.get("report_md"):
        entries["run/report.md"] = str(result["report_md"]).encode("utf-8")
    entries["README.md"] = (
        "# 本次运行复现包\n\n"
        "source_code.zip 为执行时源码；run/provenance.json 记录各源码 SHA-256 和依赖版本。\n"
        "run/ 包含原始 PDF、完整结果、日志与输入参数；manifest.json 记录本包文件指纹。\n\n"
        "解压源码后，按 docs/运行说明.md 安装依赖并启动工作台。用 run/ 中的原始 PDF、"
        "run/run.json 的任务与完整参数重新提交；草稿位于 parameters.draft。\n"
        "不要把此包直接作为运行目录恢复：会话访问凭证未导出。\n\n"
        "模型凭证须自行通过环境提供。确定性计算可以复核；云模型版本和非确定输出不承诺逐字一致。\n"
    ).encode("utf-8")
    entries["manifest.json"] = _json({
        "schema_version": 1, "run_id": context.id,
        "sha256": {name: hashlib.sha256(blob).hexdigest() for name, blob in entries.items()},
    })
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)
    return stream.getvalue()


def build_explanation(context, events: list) -> dict:
    """从实际调用生成可审计分析记录，不生成或读取模型私有思维字段。"""
    try:
        from trace import LABELS
    except ModuleNotFoundError:
        from agent.trace import LABELS
    details = {}
    path = context.output("execution_details.jsonl")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("run_id") == context.id and isinstance(record.get("seq"), int):
                details[record["seq"]] = record
    steps = []
    for event in events:
        record = details.get(event.seq)
        detail = ({key: value for key, value in record.items() if key not in {"event", "seq", "time", "run_id", "title", "summary"}}
                  if record else dict(event.payload))
        if event.event_type == "run_finished":
            detail.pop("result", None)
        steps.append({"seq": event.seq, "timestamp": event.timestamp, "event_type": event.event_type,
                      "phase": event.phase, "actor": event.actor, "status": event.status,
                      "title": event.title, "summary": event.summary,
                      "rule": LABELS.get(event.event_type, ("", "", "按实际记录展示本步输入、输出与依据"))[2],
                      "explanation": explain_step(event.event_type, detail, event.summary),
                      "evidence_ids": (record or {}).get("evidence_ids", event.evidence_ids),
                      "payload": redact_result(detail), "detail_level": "complete" if record else "event_summary"})
    responses = any(event.event_type in {"llm_response", "llm_response_tools"} for event in events)
    requests = any(event.event_type.startswith(("llm_", "model_", "agent_loop")) for event in events)
    return {"schema_version": 1, "run_id": context.id,
            "description": "可核验分析记录：结构化任务、工具输入输出、公式、证据和裁决依据",
            "model_call_status": "response_recorded" if responses else "attempted" if requests else "not_used",
            "steps": steps}


def explain_step(kind: str, detail: dict, fallback: str) -> str:
    """只把已记录的业务输入与裁决转成便于答辩的说明，不补写模型心理活动。"""
    if kind == "model_parse":
        return f"模型把草稿整理成 {detail.get('claim_count', 0)} 条结构化主张，记录公司、期间、指标、数值与拟核查动作；各主张仍须由原始证据和程序校验。"
    if kind in {"agent_tool_start", "agent_tool"}:
        arguments = detail.get("arguments") or {}
        target = "、".join(f"{label} {arguments[key]}" for key, label in (
            ("company_name_or_code", "公司"), ("period_year", "年度"), ("metric", "指标")) if key in arguments)
        result = detail.get("result") or {}
        status = result.get("verdict") or result.get("status") if isinstance(result, dict) else None
        return f"{'调用' if kind.endswith('start') else '完成'}工具 {detail.get('tool', '')}。{target}。" + (f"实际返回：{status}。" if status else "具体参数与返回内容见本步记录。")
    if kind == "calculation":
        calc = next((detail[key] for key in ("change", "yoy", "qoq", "calculation", "result") if isinstance(detail.get(key), dict)), {})
        prefix = " ".join(str(detail.get(key) or "") for key in ("company_name", "metric_name", "period_start", "period_end")).strip()
        if calc.get("status") == "ok":
            check = detail.get("reported_change_check") or detail.get("reported_yoy_check")
            value = check.get("calculated_rounded") if check else calc.get("value")
            unit = (check or {}).get("unit") or calc.get("unit") or ""
            operands = calc.get("operands") or {}
            inputs = "、".join(f"{key}={value}" for key, value in operands.items())
            text = f"{prefix} {detail.get('comparison_label') or ''}：以 {inputs or '已引用的证据数值'}，按 {calc.get('formula') or detail.get('formula') or '本地规则'} 计算得到 {value}{unit}。"
            if check:
                text += f"按原文精度与披露 {check.get('reported')}{unit} 核对，{'一致' if check.get('status') == 'match' else '不一致，需复核'}。"
            return text
        if calc:
            reason = calc.get("reason") or "、".join(calc.get("reasons") or []) or calc.get("status")
            attempt = f"尝试值 {calc['attempted_value']} 元仅供复核操作数，未获可比性确认。" if calc.get("attempted_value") is not None else ""
            return f"{prefix} 计算未确认：{reason}。{attempt}"
    if kind in {"claim_gate", "text_claim_checked", "supplemental_check"}:
        return f"主张 {detail.get('claim_id', '')}：{detail.get('status', '')}。依据：{detail.get('reason') or detail.get('reason_code') or fallback}。"
    return fallback
