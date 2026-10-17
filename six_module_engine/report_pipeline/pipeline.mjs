import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(HERE, "..");
const EXPECTED_MODULES = ["business", "profit", "assets", "cashflow", "solvency", "disclosure"];
const MODULE_LABELS = {
  business: "业务与经营",
  profit: "盈利质量",
  assets: "资产质量",
  cashflow: "现金流",
  solvency: "偿债与资金压力",
  disclosure: "披露与特殊事项"
};
const INPUT_FIELDS = {
  business: ["summary", "business_summary", "business_summary_evidence", "growth_drivers", "topics", "dependencies", "risk_factors", "follow_up_checks", "uncertainties", "coverage_checks"],
  profit: ["summary", "summary_evidence", "profit_metrics", "profit_trajectory", "profit_bridge", "findings", "follow_up_checks", "coverage_checks", "uncertainties"],
  assets: ["summary", "analysis_text", "analysis_error", "analysis_missing_fields", "facts", "calculations", "evidence_notes", "calculation_limitations", "quality_issues"],
  cashflow: ["sections", "limitations", "follow_up_questions", "review_flags", "analysis_completeness"],
  solvency: ["headline", "findings", "debt_structure", "maturity_profile", "funding_sources", "cash_availability", "support_metrics", "obligations", "data_coverage", "limitations", "handoff_questions", "evidence_review"],
  disclosure: ["executive_summary", "audit_profile", "coverage", "limitations", "findings", "impacts", "partial_reason", "run_quality"]
};
let latestAudit = null;
let latestAuditPath = "";

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : "";
}

function readDotEnv(text) {
  const values = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const split = trimmed.indexOf("=");
    if (split < 1) continue;
    const key = trimmed.slice(0, split).trim();
    let value = trimmed.slice(split + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

async function loadEnvironment() {
  let fileValues = {};
  try {
    fileValues = readDotEnv(await fs.readFile(path.join(PROJECT_ROOT, ".env"), "utf8"));
  } catch {}
  return {
    ...fileValues,
    DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY || fileValues.DEEPSEEK_API_KEY || "",
    DEEPSEEK_MODEL: process.env.DEEPSEEK_MODEL || fileValues.DEEPSEEK_MODEL || "deepseek-flash",
    DEEPSEEK_BASE_URL: process.env.DEEPSEEK_BASE_URL || fileValues.DEEPSEEK_BASE_URL || "https://api.deepseek.com",
    DEEPSEEK_TIMEOUT_SECONDS: process.env.DEEPSEEK_TIMEOUT_SECONDS || fileValues.DEEPSEEK_TIMEOUT_SECONDS || "90"
  };
}

async function walkJsonFiles(directory) {
  const found = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) found.push(...await walkJsonFiles(fullPath));
    else if (entry.isFile() && entry.name.toLowerCase() === "result.json") found.push(fullPath);
  }
  return found;
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function normalizeArray(value) {
  return Array.isArray(value) ? value : [];
}

function buildModuleInput(moduleId, document) {
  const result = document.result && typeof document.result === "object" ? document.result : document;
  const report = result.report && typeof result.report === "object" ? result.report : {};
  const readPages = normalizeArray(document.read_pages || result.read_pages).filter(Number.isInteger);
  const blocks = [];
  for (const field of INPUT_FIELDS[moduleId]) {
    if (!Object.hasOwn(result, field) || result[field] === null || result[field] === undefined) continue;
    const value = result[field];
    if (Array.isArray(value)) {
      value.forEach((item, index) => blocks.push({
        source_ref: moduleId + "." + field + "[" + index + "]",
        data: item
      }));
    } else {
      blocks.push({ source_ref: moduleId + "." + field, data: value });
    }
  }
  return {
    module_id: moduleId,
    module_label: MODULE_LABELS[moduleId],
    source_status: document.status || "",
    analysis_status: document.analysis_status || result.analysis_status || result.analysis_completion_status || "",
    review_status: document.analysis_review_status || result.analysis_review_status || "",
    module_version: document.module_version || result.module_version || "",
    company: result.company || report.company || document.company || "",
    period: result.period || report.report_year || result.report_year || "",
    reporting_scope: result.reporting_scope || report.reporting_scope || "",
    analysis_note: document.analysis_note || result.partial_reason || result.coverage_note || "",
    read_pages: readPages,
    source_blocks: blocks
  };
}

async function readPrompt(name) {
  return fs.readFile(path.join(HERE, "prompts", name), "utf8");
}

async function callModel({ model, baseUrl, apiKey, timeoutSeconds, stage, systemPrompt, input, maxTokens, audit }) {
  let endpoint = baseUrl.replace(/\/+$/, "");
  if (!endpoint.endsWith("/chat/completions")) endpoint += "/chat/completions";
  const requestBody = {
    model,
    messages: [
      { role: "system", content: systemPrompt + "\n\n请使用 JSON 输出。JSON 中的字符串使用中文。" },
      { role: "user", content: "请处理以下 JSON 数据：\n" + JSON.stringify(input) }
    ],
    response_format: { type: "json_object" },
    max_tokens: maxTokens
  };
  const inputChars = JSON.stringify(input).length;
  const startedAt = new Date().toISOString();
  let response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Authorization": "Bearer " + apiKey,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(requestBody),
      signal: AbortSignal.timeout(Number(timeoutSeconds) * 1000)
    });
  } catch (error) {
    audit.calls.push({
      stage,
      started_at: startedAt,
      input_chars: inputChars,
      transport_error: error && error.name ? error.name : "NetworkError",
      http_response_received: false
    });
    throw new Error(stage + " 未收到 DeepSeek HTTP 响应");
  }
  if (!response.ok) {
    audit.calls.push({ stage, started_at: startedAt, http_status: response.status, input_chars: inputChars });
    throw new Error(stage + " 请求失败，HTTP " + response.status);
  }
  const responseJson = await response.json();
  const choice = responseJson.choices && responseJson.choices[0];
  const content = choice && choice.message && choice.message.content;
  audit.calls.push({
    stage,
    started_at: startedAt,
    completed_at: new Date().toISOString(),
    model: responseJson.model || model,
    input_chars: inputChars,
    finish_reason: choice ? choice.finish_reason || "" : "",
    usage: responseJson.usage || null,
    response_sha256: typeof content === "string" ? sha256(content) : ""
  });
  if (!content || typeof content !== "string") throw new Error(stage + " 返回了空内容");
  if (choice.finish_reason === "length") throw new Error(stage + " 输出达到长度上限，未保存不完整结果");
  try {
    return JSON.parse(content);
  } catch {
    throw new Error(stage + " 返回内容不是有效 JSON");
  }
}

async function writeJson(filePath, value) {
  await fs.writeFile(filePath, JSON.stringify(value, null, 2) + "\n", "utf8");
}

function stringArray(value) {
  return normalizeArray(value).filter(item => typeof item === "string");
}

function intArray(value) {
  return normalizeArray(value).filter(Number.isInteger);
}

function cleanCell(value) {
  return String(value || "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ").trim();
}

function statusTable(modules) {
  const rows = [
    "| 模块 | 来源状态 | 分析状态 | 复核状态 | 提取发现数 |",
    "| --- | --- | --- | --- | ---: |"
  ];
  for (const module of modules) {
    rows.push("| " + MODULE_LABELS[module.module_id] + " | " +
      cleanCell(module.source_status || "未记录") + " | " +
      cleanCell(module.analysis_status || "未记录") + " | " +
      cleanCell(module.review_status || "未记录") + " | " + module.findings.length + " |");
  }
  return "## 六模块结果状态\n\n" + rows.join("\n");
}

function evidenceIndex(report, findings) {
  const used = [...new Set(report.match(/\[(F\d{4})\]/g) || [])].map(value => value.slice(1, -1));
  if (!used.length) return "";
  const byId = new Map(findings.map(item => [item.finding_id, item]));
  const lines = ["## 文中证据索引", ""];
  for (const id of used) {
    const finding = byId.get(id);
    if (!finding) continue;
    const pages = finding.source_pages.length ? "；年报页码：" + finding.source_pages.join("、") : "";
    lines.push("- [" + id + "] " + MODULE_LABELS[finding.module_id] + "：" + cleanCell(finding.title) + pages);
  }
  return lines.join("\n");
}

function claimLinesWithoutCitation(markdown) {
  return markdown.split(/\r?\n/).filter(line => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || /^\|[\s:|-]+\|?$/.test(trimmed)) return false;
    if (!/\d|%|亿元|万元|元/.test(trimmed)) return false;
    return !/\[F\d{4}\]/.test(trimmed);
  });
}

async function main() {
  const sourceRunId = argValue("--source-run-id");
  if (!sourceRunId || !/^[a-zA-Z0-9_-]+$/.test(sourceRunId)) {
    throw new Error("请提供有效的 --source-run-id");
  }
  if (!process.argv.includes("--allow-external-model")) {
    throw new Error("默认保持本地。调用 DeepSeek 外部服务前，需在命令中明确添加 --allow-external-model");
  }
  const sourceRoot = path.join(PROJECT_ROOT, "data", "integrated_workbench", "runs", sourceRunId);
  const environment = await loadEnvironment();
  if (!environment.DEEPSEEK_API_KEY.trim()) throw new Error("项目 .env 尚未配置 DEEPSEEK_API_KEY");
  const sourceFiles = await walkJsonFiles(sourceRoot);
  const documents = new Map();
  const sourceManifest = [];
  for (const fullPath of sourceFiles) {
    const raw = await fs.readFile(fullPath);
    let document;
    try {
      document = JSON.parse(raw.toString("utf8"));
    } catch {
      continue;
    }
    if (!EXPECTED_MODULES.includes(document.module_id)) continue;
    if (documents.has(document.module_id)) throw new Error("来源运行中出现重复模块结果：" + document.module_id);
    documents.set(document.module_id, document);
    sourceManifest.push({
      module_id: document.module_id,
      path: path.relative(PROJECT_ROOT, fullPath).split(path.sep).join("/"),
      sha256: sha256(raw),
      bytes: raw.length,
      source_status: document.status || "",
      analysis_status: document.analysis_status || "",
      review_status: document.analysis_review_status || ""
    });
  }
  const missing = EXPECTED_MODULES.filter(moduleId => !documents.has(moduleId));
  if (missing.length) throw new Error("来源运行缺少模块结果：" + missing.join("、"));

  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const outputRoot = path.join(PROJECT_ROOT, "data", "integrated_workbench", "report_pipeline", sourceRunId);
  const outputDir = path.join(outputRoot, stamp);
  await fs.mkdir(outputDir, { recursive: true });
  const moduleInputs = EXPECTED_MODULES.map(moduleId => ({
    module_id: moduleId,
    document: documents.get(moduleId),
    input: buildModuleInput(moduleId, documents.get(moduleId))
  }));
  const audit = {
    pipeline_version: "0.1.0",
    status: "running",
    source_run_id: sourceRunId,
    output_dir: path.relative(PROJECT_ROOT, outputDir).split(path.sep).join("/"),
    started_at: new Date().toISOString(),
    model: environment.DEEPSEEK_MODEL,
    source_files: sourceManifest,
    calls: [],
    checks: {},
    warnings: []
  };
  latestAudit = audit;
  latestAuditPath = path.join(outputDir, "audit.json");
  await writeJson(latestAuditPath, audit);
  console.log("输出目录：" + outputDir);
  console.log("[1/4] 逐模块提取发现");

  const findings = [];
  const moduleResults = [];
  const findingsPath = path.join(outputDir, "findings.json");
  for (const item of moduleInputs) {
    console.log("  正在处理 " + MODULE_LABELS[item.module_id] + "（输入 " + JSON.stringify(item.input).length + " 字符）");
    const response = await callModel({
      model: environment.DEEPSEEK_MODEL,
      baseUrl: environment.DEEPSEEK_BASE_URL,
      apiKey: environment.DEEPSEEK_API_KEY,
      timeoutSeconds: environment.DEEPSEEK_TIMEOUT_SECONDS,
      stage: "findings:" + item.module_id,
      systemPrompt: await readPrompt("01_findings.md"),
      input: item.input,
      maxTokens: 6500,
      audit
    });
    const allowedRefs = new Set(item.input.source_blocks.map(block => block.source_ref));
    const parsedFindings = [];
    for (const rawFinding of normalizeArray(response.findings)) {
      const findingId = "F" + String(findings.length + parsedFindings.length + 1).padStart(4, "0");
      parsedFindings.push({
        finding_id: findingId,
        module_id: item.module_id,
        title: String(rawFinding.title || "未命名发现"),
        kind: String(rawFinding.kind || "interpretation"),
        claim: String(rawFinding.claim || ""),
        evidence_summary: String(rawFinding.evidence_summary || ""),
        source_refs: stringArray(rawFinding.source_refs),
        source_pages: intArray(rawFinding.source_pages),
        counterevidence_or_boundary: String(rawFinding.counterevidence_or_boundary || ""),
        why_it_matters: String(rawFinding.why_it_matters || ""),
        verification_question: String(rawFinding.verification_question || ""),
        confidence: String(rawFinding.confidence || "low"),
        invalid_source_refs: stringArray(rawFinding.source_refs).filter(ref => !allowedRefs.has(ref))
      });
    }
    findings.push(...parsedFindings);
    moduleResults.push({
      module_id: item.module_id,
      module_label: MODULE_LABELS[item.module_id],
      source_status: item.input.source_status,
      analysis_status: item.input.analysis_status,
      review_status: item.input.review_status,
      module_version: item.input.module_version,
      company: item.input.company,
      period: item.input.period,
      reporting_scope: item.input.reporting_scope,
      read_pages: item.input.read_pages,
      module_note: String(response.module_note || ""),
      findings: parsedFindings
    });
    await writeJson(findingsPath, {
      source_run_id: sourceRunId,
      extraction_state: moduleResults.length === EXPECTED_MODULES.length ? "complete" : "in_progress",
      modules: moduleResults,
      findings
    });
    await writeJson(path.join(outputDir, "audit.json"), audit);
  }

  console.log("[2/4] 归纳跨模块主题");
  const themesResponse = await callModel({
    model: environment.DEEPSEEK_MODEL,
    baseUrl: environment.DEEPSEEK_BASE_URL,
    apiKey: environment.DEEPSEEK_API_KEY,
    timeoutSeconds: environment.DEEPSEEK_TIMEOUT_SECONDS,
    stage: "synthesis",
    systemPrompt: await readPrompt("02_synthesis.md"),
    input: {
      source_run_id: sourceRunId,
      module_statuses: moduleResults.map(({ module_id, source_status, analysis_status, review_status, module_note }) => ({
        module_id, source_status, analysis_status, review_status, module_note
      })),
      findings
    },
    maxTokens: 9000,
    audit
  });
  const themes = normalizeArray(themesResponse.themes).map((rawTheme, index) => {
    const theme = rawTheme && typeof rawTheme === "object" ? rawTheme : {};
    return {
      ...theme,
      theme_id: "T" + String(index + 1).padStart(2, "0"),
      supporting_finding_ids: stringArray(theme.supporting_finding_ids),
      counterevidence_finding_ids: stringArray(theme.counterevidence_finding_ids)
    };
  });
  await writeJson(path.join(outputDir, "themes.json"), { source_run_id: sourceRunId, themes });
  await writeJson(path.join(outputDir, "audit.json"), audit);

  console.log("[3/4] 交叉质疑综合判断");
  const review = await callModel({
    model: environment.DEEPSEEK_MODEL,
    baseUrl: environment.DEEPSEEK_BASE_URL,
    apiKey: environment.DEEPSEEK_API_KEY,
    timeoutSeconds: environment.DEEPSEEK_TIMEOUT_SECONDS,
    stage: "challenge",
    systemPrompt: await readPrompt("03_challenge.md"),
    input: { themes, findings, module_statuses: moduleResults },
    maxTokens: 9000,
    audit
  });
  const reviews = normalizeArray(review.reviews);
  await writeJson(path.join(outputDir, "review.json"), {
    source_run_id: sourceRunId,
    overall_review: String(review.overall_review || ""),
    reviews
  });
  await writeJson(path.join(outputDir, "audit.json"), audit);

  console.log("[4/4] 编辑提纲和报告");
  const editor = await callModel({
    model: environment.DEEPSEEK_MODEL,
    baseUrl: environment.DEEPSEEK_BASE_URL,
    apiKey: environment.DEEPSEEK_API_KEY,
    timeoutSeconds: environment.DEEPSEEK_TIMEOUT_SECONDS,
    stage: "editor",
    systemPrompt: await readPrompt("04_editor.md"),
    input: {
      source_run_id: sourceRunId,
      company: moduleResults.map(item => item.company).find(Boolean) || "",
      period: moduleResults.map(item => item.period).find(Boolean) || "",
      reporting_scope: moduleResults.map(item => item.reporting_scope).find(Boolean) || "",
      module_statuses: moduleResults.map(({ module_id, module_label, source_status, analysis_status, review_status, module_note }) => ({
        module_id, module_label, source_status, analysis_status, review_status, module_note
      })),
      themes,
      review: { overall_review: review.overall_review, reviews },
      findings
    },
    maxTokens: 16000,
    audit
  });
  const outline = normalizeArray(editor.outline);
  const body = String(editor.report_markdown || "").trim();
  if (!body) throw new Error("编辑阶段没有返回报告正文");
  await writeJson(path.join(outputDir, "outline.json"), { source_run_id: sourceRunId, outline });

  const report = [
    body,
    statusTable(moduleResults),
    evidenceIndex(body, findings),
    "## 阅读边界\n\n本报告整理现有六模块结果。来源状态和复核状态按原记录保留；页码检查仅说明模块登记过该页，不代表已人工复核年报原文。部分、待复核或格式待复核内容不能视为完整结论。"
  ].filter(Boolean).join("\n\n");
  await fs.writeFile(path.join(outputDir, "report.md"), report + "\n", "utf8");

  const knownFindingIds = new Set(findings.map(item => item.finding_id));
  const knownThemeIds = new Set(themes.map(item => item.theme_id));
  const invalidThemeFindingRefs = themes.flatMap(theme => [
    ...theme.supporting_finding_ids,
    ...theme.counterevidence_finding_ids
  ]).filter(id => !knownFindingIds.has(id));
  const invalidReviewThemeRefs = reviews.map(item => item.theme_id).filter(id => !knownThemeIds.has(id));
  const invalidReviewFindingRefs = reviews.flatMap(item => [
    ...stringArray(item.supporting_finding_ids),
    ...stringArray(item.counterevidence_finding_ids)
  ]).filter(id => !knownFindingIds.has(id));
  const unreadPages = [];
  for (const finding of findings) {
    const module = moduleResults.find(item => item.module_id === finding.module_id);
    for (const page of finding.source_pages) {
      if (!module.read_pages.includes(page)) unreadPages.push({ finding_id: finding.finding_id, module_id: finding.module_id, page });
    }
  }
  const reportCitations = [...new Set(report.match(/\[(F\d{4})\]/g) || [])].map(value => value.slice(1, -1));
  const unknownReportCitations = reportCitations.filter(id => !knownFindingIds.has(id));
  const uncitedClaimLines = claimLinesWithoutCitation(body);
  audit.completed_at = new Date().toISOString();
  audit.status = "complete";
  audit.checks = {
    all_six_source_modules_present: moduleResults.length === EXPECTED_MODULES.length,
    all_finding_source_refs_exist: findings.every(item => item.source_refs.length > 0 && item.invalid_source_refs.length === 0),
    all_finding_pages_were_read: unreadPages.length === 0,
    theme_finding_references_resolve: invalidThemeFindingRefs.length === 0,
    review_theme_references_resolve: invalidReviewThemeRefs.length === 0,
    review_finding_references_resolve: invalidReviewFindingRefs.length === 0,
    report_citations_resolve: unknownReportCitations.length === 0,
    factual_lines_with_possible_missing_citation: uncitedClaimLines.length
  };
  audit.details = {
    finding_count: findings.length,
    theme_count: themes.length,
    review_count: reviews.length,
    report_citation_count: reportCitations.length,
    invalid_finding_source_refs: findings.filter(item => item.invalid_source_refs.length).map(item => ({
      finding_id: item.finding_id, refs: item.invalid_source_refs
    })),
    unread_pages: unreadPages,
    invalid_theme_finding_refs: invalidThemeFindingRefs,
    invalid_review_theme_refs: invalidReviewThemeRefs,
    invalid_review_finding_refs: invalidReviewFindingRefs,
    unknown_report_citations: unknownReportCitations,
    factual_lines_without_citation: uncitedClaimLines
  };
  if (uncitedClaimLines.length) audit.warnings.push("正文中有包含数字或金额但未检测到发现编号的行；请人工检查。");
  if (moduleResults.some(item => /partial|review|needs_review|format_review/i.test([
    item.source_status, item.analysis_status, item.review_status
  ].join(" ")))) audit.warnings.push("至少一个来源模块未标记为无复核限制；报告已附模块状态表。");
  audit.warnings.push("本地自动检查只核对来源编号和登记页码，不替代人工核对年报原文与会计判断。");
  await writeJson(path.join(outputDir, "audit.json"), audit);

  console.log("已完成。报告：" + path.join(outputDir, "report.md"));
  console.log("审计：" + path.join(outputDir, "audit.json"));
}

main().catch(async error => {
  if (latestAudit && latestAuditPath) {
    latestAudit.status = "failed";
    latestAudit.failed_at = new Date().toISOString();
    latestAudit.warnings.push(error.message);
    await writeJson(latestAuditPath, latestAudit).catch(() => {});
  }
  console.error("本地统筹流程中断：" + error.message);
  process.exitCode = 1;
});
