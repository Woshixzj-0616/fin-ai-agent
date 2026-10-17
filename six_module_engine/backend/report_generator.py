"""Create a source-linked narrative report from a completed six-module run."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.core.model_io import model_name, new_client, recorded_completion
from backend.core.recorder import RunRecorder
from backend.integrated_store import get_report
from backend.financial_brief import (
    audit_narrative, build_financial_brief, quarantine_inconsistent_paragraphs,
    render_financial_brief,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "integrated_workbench"
OUTPUT_DIR = DATA_DIR / "report_pipeline"
PROMPT_DIR = ROOT / "report_pipeline" / "prompts"
MODULE_FIELDS = {
    "business": ("summary", "business_summary", "business_flow", "revenue_total", "revenue_totals", "revenue_segments", "industry_context", "strategy_competitiveness", "growth_drivers", "dependencies", "risk_factors", "major_changes", "follow_up_checks", "uncertainties", "coverage_checks"),
    "profit": ("summary", "profit_metrics", "profit_trajectory", "profit_bridge", "findings", "follow_up_checks", "coverage_checks", "uncertainties"),
    "assets": ("summary", "analysis_text", "analysis_error", "analysis_missing_fields", "facts", "calculations", "evidence_notes", "quality_issues"),
    "cashflow": ("sections", "limitations", "follow_up_questions", "review_flags", "analysis_completeness"),
    "solvency": ("headline", "findings", "debt_structure", "maturity_profile", "funding_sources", "cash_availability", "support_metrics", "obligations", "data_coverage", "limitations", "handoff_questions", "evidence_review"),
    "disclosure": ("executive_summary", "audit_profile", "coverage", "limitations", "findings", "impacts", "partial_reason", "run_quality"),
}
LABELS = {
    "business": "业务与经营", "profit": "盈利分析", "assets": "资产质量",
    "cashflow": "现金流", "solvency": "偿债能力", "disclosure": "信息披露",
}


def _read_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _scope_label(value: str) -> str:
    scope = value.strip()
    if "合并" in scope or scope.lower() == "consolidated":
        return "合并报表"
    if "母公司" in scope or scope.lower() == "parent":
        return "母公司报表"
    return scope


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _call_json(recorder: RunRecorder, stage: str, system_prompt: str, payload: Any, *, max_tokens: int = 8000) -> dict[str, Any]:
    last_issue = "没有返回有效 JSON"
    for attempt in range(2):
        compact = attempt > 0
        response = recorded_completion(
            recorder=recorder,
            client=new_client(),
            model=model_name(),
            messages=[
                {"role": "system", "content": system_prompt + (
                    "\n\n上次输出没有形成完整 JSON。本次只返回一个闭合的 JSON 对象；"
                    "优先写完整的短段落，必要时缩至 500 字，不使用代码围栏。" if compact else ""
                )},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            stream=False,
        )
        if not response.choices or response.choices[0].finish_reason == "length":
            last_issue = "输出达到模型长度上限"
            continue
        content = (response.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"\A```(?:json)?\s*|\s*```\Z", "", content, flags=re.I).strip()
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            last_issue = "没有返回有效 JSON"
            continue
        if isinstance(result, dict):
            return result
        last_issue = "返回的数据结构无效"
    raise ValueError(f"{stage} 阶段{last_issue}；已保留此前阶段底稿。")


def _module_inputs(report: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, set[int]], set[str]]:
    inputs: list[dict[str, Any]] = []
    allowed_pages: dict[str, set[int]] = {}
    allowed_refs: set[str] = set()
    for task in report.get("modules", []):
        module_id = str(task.get("module_id") or "")
        envelope = task.get("result") if isinstance(task.get("result"), dict) else {}
        result = envelope.get("result") if isinstance(envelope.get("result"), dict) else {}
        facts = result.get("facts") if isinstance(result.get("facts"), list) else []
        fact_scopes = {str(fact.get("reporting_scope")).strip() for fact in facts if isinstance(fact, dict) and fact.get("reporting_scope")}
        scope = _scope_label(str(result.get("reporting_scope") or "").strip() or (next(iter(fact_scopes)) if len(fact_scopes) == 1 else ""))
        pages = {
            int(page) for page in envelope.get("read_pages", [])
            if str(page).isdigit() and int(page) > 0
        }
        allowed_pages[module_id] = pages
        blocks = []
        for field in MODULE_FIELDS.get(module_id, ()):
            if field not in result or result[field] is None:
                continue
            value = result[field]
            values = value if isinstance(value, list) else [value]
            for index, item in enumerate(values[:35]):
                source_ref = f"{module_id}.{field}[{index}]" if isinstance(value, list) else f"{module_id}.{field}"
                allowed_refs.add(source_ref)
                blocks.append({"source_ref": source_ref, "data": item})
        inputs.append({
            "module_id": module_id,
            "module_label": LABELS.get(module_id, module_id),
            "reporting_scope": scope,
            "source_status": task.get("status"),
            "analysis_status": task.get("analysis_status") or result.get("analysis_status") or result.get("analysis_completion_status"),
            "review_status": task.get("review_status") or result.get("analysis_review_status"),
            "module_version": task.get("module_version"),
            "analysis_note": task.get("error") or envelope.get("analysis_note") or "",
            "read_pages": sorted(pages),
            "source_blocks": blocks,
        })
    return inputs, allowed_pages, allowed_refs


def _validate_findings(raw: Any, allowed_pages: dict[str, set[int]], allowed_refs: set[str]) -> list[dict[str, Any]]:
    rows = raw.get("findings", []) if isinstance(raw, dict) else []
    validated = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        module_id = str(item.get("module_id") or "")
        refs = list(dict.fromkeys(
            ref for ref in (item.get("source_refs") or [])
            if isinstance(ref, str) and ref.startswith(module_id + ".") and ref in allowed_refs
        ))
        pages = sorted({int(page) for page in (item.get("source_pages") or []) if str(page).isdigit() and int(page) in allowed_pages.get(module_id, set())})
        if not refs or not item.get("claim"):
            continue
        finding = dict(item)
        finding.update({"finding_id": f"F{len(validated) + 1:04d}", "module_id": module_id, "source_refs": refs, "source_pages": pages})
        validated.append(finding)
    return validated


def _validate_themes(raw: Any, finding_ids: set[str]) -> list[dict[str, Any]]:
    rows = raw.get("themes", []) if isinstance(raw, dict) else []
    themes = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        support = [value for value in item.get("supporting_finding_ids", []) if value in finding_ids]
        counter = [value for value in item.get("counterevidence_finding_ids", []) if value in finding_ids]
        if not support:
            continue
        theme = dict(item)
        theme.update({
            "theme_id": f"T{len(themes) + 1:02d}",
            "supporting_finding_ids": support,
            "counterevidence_finding_ids": counter,
        })
        themes.append(theme)
    return themes


def _observation_section(themes: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> str:
    titles = {item["theme_id"]: str(item.get("title") or "相关主题") for item in themes}
    points: list[str] = []
    seen: set[str] = set()
    for review in reviews:
        for question in (review.get("follow_up_checks") or []):
            question = str(question or "").strip()
            if not question or question in seen:
                continue
            seen.add(question)
            points.append(f"- **{titles.get(review['theme_id'], '相关主题')}：**{question}")
            if len(points) == 3:
                return "## 最可能改变判断的后续观察\n\n" + "\n".join(points)
    return "## 最可能改变判断的后续观察\n\n" + "\n".join(points) if points else ""


def generate_report_package(report_id: str) -> dict[str, Any]:
    report = get_report(report_id)
    if not report:
        raise ValueError("找不到本次六模块运行。")
    module_inputs, allowed_pages, allowed_refs = _module_inputs(report)
    complete = [item for item in module_inputs if item["source_status"] == "completed"]
    if not complete:
        raise ValueError("没有模块返回可用于成稿的分析结果。")
    scopes = {item["reporting_scope"] for item in complete if item.get("reporting_scope")}
    report_scope = next(iter(scopes)) if len(scopes) == 1 else ""

    stamp = datetime.now(timezone.utc).strftime("v5_%Y%m%d_%H%M%S_%f")
    output = OUTPUT_DIR / report_id / stamp
    output.mkdir(parents=True, exist_ok=True)
    financial_brief = build_financial_brief(report)
    _write_json(output / "financial_brief.json", financial_brief)
    recorder = RunRecorder(output / "generation", run_id=report_id, module_id="editorial_report")
    recorder.record("report_generation_started", module_count=len(complete), source_run_id=report_id)

    discovery_rows: list[dict[str, Any]] = []
    for module in complete:
        module_id = module["module_id"]
        findings_result = _call_json(recorder, f"{LABELS.get(module_id, module_id)}发现整理", _read_prompt("01_findings.md"), {
            "company": report.get("company_name") or "",
            "period": report.get("report_year") or "",
            "reporting_scope": report_scope,
            "modules": [module],
        })
        _write_json(output / f"discovery_{module_id}.json", findings_result)
        discovery_rows.extend(
            {**item, "module_id": module_id}
            for item in findings_result.get("findings", []) if isinstance(item, dict)
        )
    findings = _validate_findings({"findings": discovery_rows}, allowed_pages, allowed_refs)
    _write_json(output / "findings_stage.json", {"findings": findings})
    for index, item in enumerate(findings, 1):
        item.setdefault("module_label", LABELS.get(item.get("module_id"), item.get("module_id")))
    finding_ids = {item["finding_id"] for item in findings}
    themes_result = _call_json(recorder, "跨模块综合", _read_prompt("02_synthesis.md"), {
        "company": report.get("company_name") or "",
        "period": report.get("report_year") or "",
        "reporting_scope": report_scope,
        "modules": [{key: value for key, value in item.items() if key != "source_blocks"} for item in module_inputs],
        "findings": findings,
    })
    _write_json(output / "synthesis_stage.json", themes_result)
    themes = _validate_themes(themes_result, finding_ids)
    for index, item in enumerate(themes, 1):
        item.setdefault("missing_evidence", [])
    review_result = _call_json(recorder, "反方复核", _read_prompt("03_challenge.md"), {
        "themes": themes,
        "findings": findings,
    })
    _write_json(output / "challenge_stage.json", review_result)
    valid_theme_ids = {item["theme_id"] for item in themes}
    reviews = [
        {**item, "theme_id": item.get("theme_id"),
         "supporting_finding_ids": [value for value in item.get("supporting_finding_ids", []) if value in finding_ids],
         "counterevidence_finding_ids": [value for value in item.get("counterevidence_finding_ids", []) if value in finding_ids]}
        for item in review_result.get("reviews", [])
        if isinstance(item, dict) and item.get("theme_id") in valid_theme_ids
    ] if isinstance(review_result.get("reviews"), list) else []
    editor_rules = _read_prompt("04_editor.md").split("输出有效 JSON：", 1)[0]
    editor_context = {
        "company": report.get("company_name") or "",
        "period": report.get("report_year") or "",
        "reporting_scope": report_scope,
        "modules": [{key: value for key, value in item.items() if key != "source_blocks"} for item in module_inputs],
        "overall_review": review_result.get("overall_review") or "",
        "financial_brief": financial_brief,
    }
    intro_result = _call_json(
        recorder, "核心判断编辑",
        editor_rules + "\n\n本次只写报告开篇，不写后续章节。输出 JSON："
        '{"intro_markdown":"含公司、年度、报表口径、核心判断及三至四条重要发现；财务数据底表由程序紧接本段插入，此处不要重复列表；'
        '缺失利润或收入等支柱时明确标为待补证稿"}。公司、期间、口径只从输入读取；缺失则明确标注。控制在 1200 个汉字以内。',
        {**editor_context, "findings": [
            {key: item.get(key) for key in ("finding_id", "title", "claim", "source_pages", "counterevidence_or_boundary")}
            for item in findings
        ], "themes": [
            {key: item.get(key) for key in ("theme_id", "title", "provisional_view", "supporting_finding_ids")}
            for item in themes
        ]}, max_tokens=4500,
    )
    _write_json(output / "editor_intro.json", intro_result)
    intro = str(intro_result.get("intro_markdown") or "").strip()
    if not intro:
        raise ValueError("报告开篇没有返回正文。")
    review_by_theme = {item["theme_id"]: item for item in reviews}
    chapters: list[str] = []
    outline: list[dict[str, Any]] = []
    chapter_inputs = themes or [
        {"theme_id": module_id, "title": LABELS.get(module_id, module_id),
         "supporting_finding_ids": [item["finding_id"] for item in findings if item["module_id"] == module_id]}
        for module_id in LABELS
        if any(item["module_id"] == module_id for item in findings)
    ]
    for index, theme in enumerate(chapter_inputs, 1):
        theme_id = str(theme.get("theme_id") or f"T{index:02d}")
        related_ids = set(theme.get("supporting_finding_ids", [])) | set(theme.get("counterevidence_finding_ids", []))
        related = [item for item in findings if item["finding_id"] in related_ids]
        if not related:
            continue
        title = str(theme.get("title") or f"专题 {index}")
        chapter_result = _call_json(
            recorder, f"第{index}章编辑",
            editor_rules + "\n\n本次只写一个章节。不得重写报告开篇或其他章节。"
            "输出 JSON：{\"section_markdown\":\"本章完整正文，以解释充分为准，通常600至1100个汉字；必要时含一张有来源编号的勾稽表\"}。"
            "围绕给定主题，写清事实、变化、机制、反方解释及未解决的取证问题；只使用本次输入的发现编号。"
            "financial_brief 是程序按同年合并口径整理的计算底表。涉及其中指标、桥接和比例时必须与之相同；"
            "已在底表出现的数值不得称未取得。若底表没有该指标，不自行补造。正文不要重复章节标题。",
            {**editor_context, "theme": theme, "review": review_by_theme.get(theme_id, {}),
             "findings": related}, max_tokens=5000,
        )
        _write_json(output / f"editor_chapter_{index:02d}.json", chapter_result)
        chapter = str(chapter_result.get("section_markdown") or "").strip()
        if not chapter:
            raise ValueError(f"第{index}章没有返回正文。")
        chapter = re.sub(r"\A(?:#{1,6}\s+[^\n]+\n+)+", "", chapter).strip()
        chapters.append(f"## {index:02d} {title}\n\n{chapter}")
        outline.append({"section_title": title, "purpose": str(theme.get("provisional_view") or ""),
                        "finding_ids": sorted(related_ids)})
    if not chapters:
        raise ValueError("现有发现未能形成可阅读的分析章节。")
    quality_partial = any(
        any(marker in str(item.get(field) or "") for marker in ("部分", "未完成", "需复核", "待复核", "失败", "partial"))
        for item in module_inputs for field in ("analysis_status", "review_status")
    )
    status_label = "待补证草稿" if len(complete) < len(module_inputs) or quality_partial else "财报分析"
    brief_markdown = render_financial_brief(financial_brief)
    observations = _observation_section(themes, reviews)
    markdown = (
        f"# {report.get('company_name') or '公司'} {report.get('report_year') or ''} 年{status_label}\n\n"
        + intro + ("\n\n" + brief_markdown if brief_markdown else "")
        + "\n\n" + "\n\n".join(chapters)
        + ("\n\n" + observations if observations else "")
    )
    numerical_issues = audit_narrative(markdown, financial_brief)
    if numerical_issues:
        markdown = quarantine_inconsistent_paragraphs(markdown, numerical_issues, financial_brief)
        if status_label == "财报分析":
            markdown = markdown.replace("年财报分析", "年待补证草稿", 1)
    outline_result = {"outline": outline}
    _write_json(output / "editor_stage.json", {"outline": outline, "chapter_count": len(chapters)})
    citation_ids = set(re.findall(r"\[(F\d{4})\]", markdown))
    invalid_citations = sorted(citation_ids - finding_ids)
    for citation in invalid_citations:
        markdown = markdown.replace(f"[{citation}]", "")
    warnings = []
    if len(complete) < len(module_inputs):
        warnings.append("至少一个模块未完成；正文只覆盖已返回的可用结果。");
    if quality_partial:
        warnings.append("至少一个模块仍为部分完成或需复核；报告保持待补证身份。")
    if numerical_issues:
        warnings.append(f"发现 {len(numerical_issues)} 处与统一底表冲突的数值或缺失表述，相关段落已暂缓展示。")
    if invalid_citations:
        warnings.append("编辑稿中出现无对应来源的发现编号，已从正文删除。");
    if not citation_ids:
        warnings.append("正文未返回可识别的发现编号引用，请人工核对来源覆盖。");

    findings_doc = {
        "source_run_id": report_id,
        "company": report.get("company_name") or "公司名称未提供",
        "period": report.get("report_year") or "报告期间未提供",
        "reporting_scope": report_scope or "各模块口径请逐项核对",
        "generation_mode": "本机六模块结果经结构化发现、综合、反方复核和编辑生成。",
        "modules": [{key: value for key, value in item.items() if key != "source_blocks"} for item in module_inputs],
        "findings": findings,
    }
    audit = {
        "pipeline_version": "finscope-v5.2.0-python",
        "artifact_kind": "editorial_report",
        "status": "partial" if warnings else "completed",
        "generated_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "processing_record": {"stages": ["逐模块发现整理", "跨模块综合", "反方复核", "核心判断编辑", "逐章编辑"], "model": model_name()},
        "external_model_attempt": {"successful_model_completions": len(complete) + 3 + len(chapters), "transmission_note": "本次六模块材料发送到工作台当前配置的模型服务。"},
        "counts": {"modules": len(module_inputs), "completed_modules": len(complete), "findings": len(findings), "themes": len(themes), "reviews": len(reviews)},
        "checks": {
            "invalid_citations_removed": invalid_citations,
            "report_pages": {key: sorted(value) for key, value in allowed_pages.items()},
            "numerical_issues": [{"kind": item["kind"], "message": item["message"]} for item in numerical_issues],
        },
        "warnings": warnings,
    }
    _write_json(output / "findings.json", findings_doc)
    _write_json(output / "themes.json", {"source_run_id": report_id, "themes": themes})
    _write_json(output / "review.json", {"source_run_id": report_id, "overall_review": review_result.get("overall_review", ""), "reviews": reviews})
    _write_json(output / "outline.json", {"source_run_id": report_id, "outline": outline_result.get("outline", [])})
    _write_json(output / "audit.json", audit)
    (output / "report.md").write_text(markdown + "\n", encoding="utf-8")
    recorder.record("report_generation_completed", status=audit["status"], finding_count=len(findings), theme_count=len(themes))
    return {"status": audit["status"], "package_name": stamp, "counts": audit["counts"], "warnings": warnings}
