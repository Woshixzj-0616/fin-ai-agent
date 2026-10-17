function panelHeader() {
  const header = document.createElement("header");
  header.className = "panel-header";

  const copy = document.createElement("div");
  const kicker = document.createElement("p");
  kicker.className = "section-kicker";
  kicker.textContent = "核查结果";
  const title = document.createElement("h3");
  title.textContent = "证据与结论";
  copy.append(kicker, title);
  header.append(copy);

  return header;
}

function emptyResults() {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  const title = document.createElement("strong");
  title.textContent = "尚无核查结果";
  const detail = document.createElement("span");
  detail.textContent = "结果将在运行完成后显示。";
  empty.append(title, detail);
  return empty;
}

function textValue(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function displayNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : String(value);
}

function bboxFrom(record, key) {
  const value = record?.[key];
  if (!Array.isArray(value) || value.length < 4) return null;
  const numbers = value.slice(0, 4).map(Number);
  return numbers.every(Number.isFinite) ? numbers : null;
}

function addBbox(dataset, record, key, name) {
  const bbox = bboxFrom(record, key);
  if (bbox) dataset[name] = JSON.stringify(bbox);
}

export function makeEvidenceButton(record, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "evidence-chip";
  button.textContent = label;
  button.dataset.evidenceId = record.evidence_id || "";
  button.dataset.pageLabel = record.page ? `第 ${record.page} 页` : "证据页";
  button.dataset.imageUrl = record.page_image_url || "";
  button.dataset.pageWidth = String(record.page_width || "");
  button.dataset.pageHeight = String(record.page_height || "");
  button.dataset.imageScale = String(record.image_scale || "");
  addBbox(button.dataset, record, "value_bbox", "valueBbox");
  addBbox(button.dataset, record, "label_bbox", "labelBbox");
  return button;
}

function section(title, count) {
  const header = document.createElement("div");
  header.className = "result-section-header";
  const heading = document.createElement("h4");
  heading.textContent = title;
  header.append(heading);
  if (count !== undefined) {
    const badge = document.createElement("span");
    badge.className = "badge badge-neutral";
    badge.textContent = String(count);
    header.append(badge);
  }
  return header;
}

function renderSummary(result) {
  const counts = result.counts || {};
  const summary = document.createElement("div");
  summary.className = "result-summary";
  const metrics = result.announcement ? [
    ["公告事件", counts.events], ["必填缺失", counts.required_missing], ["字段冲突", counts.conflicts],
  ] : [
    ["一致", counts.match],
    ["不一致", counts.mismatch],
    ["待核验", counts.unverified],
    ["问题", counts.issue],
  ];
  metrics.forEach(([label, value]) => {
    const metric = document.createElement("div");
    metric.className = "result-metric";
    const labelNode = document.createElement("span");
    labelNode.textContent = label;
    const valueNode = document.createElement("strong");
    valueNode.textContent = textValue(value);
    metric.append(labelNode, valueNode);
    summary.append(metric);
  });
  return summary;
}

function renderEvidence(evidence) {
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("证据", evidence.length));
  if (!evidence.length) {
    const empty = document.createElement("p");
    empty.className = "result-empty";
    empty.textContent = "本次运行没有形成证据记录。";
    wrapper.append(empty);
    return wrapper;
  }

  const list = document.createElement("div");
  list.className = "evidence-list";
  evidence.forEach((record) => {
    const item = document.createElement("article");
    item.className = "evidence-card";
    const heading = document.createElement("div");
    heading.className = "evidence-heading";
    const title = document.createElement("strong");
    title.textContent = record.metric_name || record.metric || "证据";
    heading.append(title);
    if (record.page_image_url) {
      heading.append(makeEvidenceButton(record, `第 ${textValue(record.page)} 页`));
    }
    const detail = document.createElement("p");
    detail.textContent = `${textValue(record.value)} ${record.unit || ""}`.trim();
    item.append(heading, detail, Object.assign(document.createElement("small"), {
      textContent: `${textValue(record.period_year)} · ${textValue(record.scope)} · ${textValue(record.adjustment)}`,
    }));
    list.append(item);
  });
  wrapper.append(list);
  return wrapper;
}

function renderRows(rows) {
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("指标与裁决", rows.length));
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "result-empty";
    empty.textContent = "本次运行没有指标行。";
    wrapper.append(empty);
    return wrapper;
  }

  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "data-table result-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["公司 / 指标", "期间", "本期", "比较基期", "比较类型 / 复算增减", "核对披露", "页码"].forEach((label) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const rowNode = document.createElement("tr");
    const metric = document.createElement("td");
    metric.textContent = `${row.company_name || ""} ${row.metric_name || row.metric || "—"}`.trim();
    const period = document.createElement("td");
    period.textContent = `${textValue(row.period_start)} 至 ${textValue(row.period_end)}`;
    const current = document.createElement("td");
    const unit = row.normalized_unit || row.unit || "";
    current.textContent = `${displayNumber(row.current ?? row.value)} ${unit}`;
    const previous = document.createElement("td");
    previous.textContent = `${displayNumber(row.previous)} ${unit} ${row.previous_period_end || ""}`;
    const yoy = document.createElement("td");
    const change = row.change || row.yoy;
    const check = row.reported_change_check || row.reported_yoy_check;
    yoy.textContent = `${row.comparison_label || "同比"}：${change?.status === "ok" ? `${check?.calculated_rounded ?? displayNumber(change.value)}${change.unit || "%"}` : (change?.reason || textValue(change?.status))}`;
    const outcome = document.createElement("td");
    const reported = row.reported_change || row.reported_yoy;
    outcome.textContent = check ? `${({ match: "一致", mismatch: "不一致，需复核" })[check.status] || "未核对"} · ${check.reported}${check.unit || "%"}`
      : ({ not_disclosed: "原文未披露", unparsed: "披露值无法解析", not_comparable: "无法核对（期间不可比）" })[row.disclosure_status]
        || (reported?.value != null ? "无法核对" : "未提取到披露值");
    const page = document.createElement("td");
    if (row.page_image_url) {
      page.append(makeEvidenceButton(row, textValue(row.page)));
    } else {
      page.textContent = textValue(row.page);
    }
    rowNode.append(metric, period, current, previous, yoy, outcome, page);
    body.append(rowNode);
  });
  table.append(head, body);
  scroll.append(table);
  wrapper.append(scroll);
  return wrapper;
}

function renderChecks(bundle, evidence) {
  const checks = Array.isArray(bundle.checks) ? bundle.checks : [];
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("研报草稿核查", checks.length));
  const note = document.createElement("p");
  note.className = "result-empty";
  note.textContent = [bundle.reason, bundle.mode === "local_supplemental"
    ? "本次未调用大模型，未检查的句子不能视为通过。"
    : "确认错误与证据支持由程序裁定；模型判断须人工复核。"].filter(Boolean).join(" ");
  wrapper.append(note);
  const byId = new Map(evidence.map(item => [item.evidence_id, item]));
  for (const check of checks) {
    const card = document.createElement("article");
    card.className = "check-card";
    const title = document.createElement("strong");
    title.textContent = `${textValue(check.claim_id)} · ${textValue(check.status)} · ${check.track === "model" ? "模型判断" : "程序核查"}`;
    const quote = document.createElement("blockquote");
    quote.textContent = check.quote || check.original_sentence || check.plain_claim || "—";
    card.append(title, quote);
    const resolution = check.resolution;
    if (resolution) {
      const candidates = (resolution.candidates || [])
        .map(item => `${item.code || "—"}:${item.matched || item.name || "—"}`).join("、");
      const conflicts = (resolution.conflicts || []).map(item => item.field).filter(Boolean).join("、");
      for (const [label, value] of [
        ["解析", resolution.label || resolution.status],
        ["候选", candidates],
        ["冲突字段", conflicts],
        ["下一步", resolution.next_action?.message],
      ]) {
        if (!value) continue;
        const detail = document.createElement("p");
        detail.textContent = `${label}：${value}`;
        card.append(detail);
      }
    }
    for (const [label, value] of [["依据", check.reason], ["修改建议", check.suggestion],
      ["替换", check.correction ? `${textValue(check.correction.before)} → ${textValue(check.correction.after)}` : null]]) {
      if (!value) continue;
      const detail = document.createElement("p");
      detail.textContent = `${label}：${value}`;
      card.append(detail);
    }
    for (const id of check.evidence_ids || []) {
      const record = byId.get(id);
      if (record?.page_image_url) card.append(makeEvidenceButton(record, `${id} · 第 ${record.page} 页`));
      else card.append(Object.assign(document.createElement("code"), { textContent: id }));
    }
    if (check.calculation) {
      const details = document.createElement("details");
      details.append(Object.assign(document.createElement("summary"), { textContent: "计算过程" }),
        Object.assign(document.createElement("pre"), { textContent: JSON.stringify(check.calculation, null, 2) }));
      card.append(details);
    }
    wrapper.append(card);
  }
  return wrapper;
}

function renderSignals(signals) {
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("业绩与异常信号", signals.length));
  const groups = new Map();
  for (const signal of signals) {
    const key = `${signal.document_id}|${signal.period_start}|${signal.period_end}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(signal);
  }
  for (const group of groups.values()) {
    const source = group[0];
    const title = document.createElement("h5");
    title.textContent = `${source.company_name || source.document_id} · ${source.period_start || ""} 至 ${source.period_end || ""}`;
    wrapper.append(title);
    for (const signal of group) {
      const row = document.createElement("p");
      row.textContent = `【${signal.epistemic_type || "需复核"}】${signal.description || ""} ${signal.value == null ? "" : displayNumber(signal.value)}${signal.unit || ""}。${signal.interpretation || ""} 来源 PDF 页：${(signal.source_pages || []).map(s => s.page).join("、")}`;
      wrapper.append(row);
    }
  }
  return wrapper;
}

function renderQuarters(result) {
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("单季环比与累计拆季"));
  for (const row of result.qoq_rows || []) {
    const detail = document.createElement("p");
    const change = row.qoq || {};
    detail.textContent = `${row.company_code} ${row.metric} · ${row.period_start} 至 ${row.period_end}：${change.status === "ok" ? `${change.value}%` : (change.reason || (change.reasons || []).join("、") || change.status)}`;
    wrapper.append(detail);
  }
  for (const row of result.qoq_blocked || []) {
    const detail = document.createElement("article");
    detail.className = "check-card";
    detail.append(Object.assign(document.createElement("strong"), { textContent: `${row.company_code} ${row.metric} · ${row.period_start} 至 ${row.period_end}：未获可比性确认` }));
    const text = document.createElement("p");
    text.textContent = `${row.reason || "累计拆季受阻"} ${(row.reasons || []).join("、")}。尝试值 ${row.attempted_value ?? "—"} 元，仅供复核操作数，不作为有效单季值。`;
    detail.append(text);
    for (const source of row.sources || []) {
      detail.append(Object.assign(document.createElement("small"), { textContent: `来源：${source.document_id} · ${source.period_start} 至 ${source.period_end} · PDF第${source.page}页 ` }));
    }
    wrapper.append(detail);
  }
  return wrapper;
}

function renderExports(exports, provenance) {
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("保存成果"));
  const actions = document.createElement("div");
  actions.className = "export-actions";
  for (const [kind, label] of [["result.json", "结构化结果"], ["report.md", "分析报告"], ["explanation.json", "分析过程与依据"], ["reproduction.zip", "完整复现包"]]) {
    const url = exports[kind];
    // 下载地址必须属于当前服务的运行导出接口。
    if (typeof url !== "string" || !/^\/api\/runs\/[^/]+\/exports\/(result\.json|report\.md|explanation\.json|reproduction\.zip)$/.test(url)) continue;
    const link = document.createElement("a");
    link.className = "button";
    link.href = url;
    link.download = "";
    link.textContent = label;
    actions.append(link);
  }
  wrapper.append(actions);
  if (provenance) {
    const note = document.createElement("small");
    note.textContent = `已保存执行时源码与依赖版本 · 源码指纹 ${textValue(provenance.source_fingerprint)}`;
    wrapper.append(note);
  }
  return wrapper;
}

function renderIssues(issues, failures) {
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("问题与失败", issues.length + failures.length));
  if (!issues.length && !failures.length) {
    const empty = document.createElement("p");
    empty.className = "result-empty";
    empty.textContent = "没有已知问题或失败项。";
    wrapper.append(empty);
    return wrapper;
  }
  const list = document.createElement("ul");
  list.className = "issue-list";
  issues.forEach((item) => {
    const row = document.createElement("li");
    row.className = "issue-item issue-item-warning";
    const title = document.createElement("strong");
    title.textContent = item.metric_name || item.evidence_id || "证据问题";
    const detail = document.createElement("span");
    detail.textContent = (item.issues || []).join("、") || "需要人工复核";
    row.append(title, detail);
    list.append(row);
  });
  failures.forEach((item) => {
    const row = document.createElement("li");
    row.className = "issue-item issue-item-danger";
    const title = document.createElement("strong");
    title.textContent = item.document_id || item.error_type || "运行失败项";
    const detail = document.createElement("span");
    detail.textContent = item.message || item.error || "处理未完成";
    row.append(title, detail);
    list.append(row);
  });
  wrapper.append(list);
  return wrapper;
}

function renderReport(report) {
  if (!report) return null;
  const wrapper = document.createElement("section");
  wrapper.className = "result-section";
  wrapper.append(section("报告"));
  const details = document.createElement("details");
  details.className = "report-preview";
  const summary = document.createElement("summary");
  summary.textContent = "查看报告文本";
  const pre = document.createElement("pre");
  pre.textContent = report;
  details.append(summary, pre);
  wrapper.append(details);
  return wrapper;
}

export function renderResultsView(root, state) {
  root.replaceChildren(panelHeader());

  const result = state.result;
  if (!result) {
    root.append(emptyResults());
    return;
  }

  root.append(renderSummary(result));
  if (result.exports) root.append(renderExports(result.exports, result.provenance));
  if (result.checks) root.append(renderChecks(result.checks, Array.isArray(result.evidence) ? result.evidence : []));
  if (result.signals?.length) root.append(renderSignals(result.signals));
  if (result.qoq_rows?.length || result.qoq_blocked?.length) root.append(renderQuarters(result));
  root.append(renderEvidence(Array.isArray(result.evidence) ? result.evidence : []));
  root.append(renderRows(Array.isArray(result.rows) ? result.rows : []));
  root.append(renderIssues(
    Array.isArray(result.issues) ? result.issues : [],
    Array.isArray(result.failures) ? result.failures : [],
  ));
  const report = renderReport(result.report_md);
  if (report) root.append(report);
}
