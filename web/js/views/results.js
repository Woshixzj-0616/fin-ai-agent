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

function makeEvidenceButton(record, label) {
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
  [
    ["一致", counts.match],
    ["不一致", counts.mismatch],
    ["待核验", counts.unverified],
    ["问题", counts.issue],
  ].forEach(([label, value]) => {
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
  ["指标", "本期", "上期", "页码"].forEach((label) => {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const rowNode = document.createElement("tr");
    const metric = document.createElement("td");
    metric.textContent = row.metric_name || row.metric || "—";
    const current = document.createElement("td");
    current.textContent = textValue(row.current ?? row.value);
    const previous = document.createElement("td");
    previous.textContent = textValue(row.previous);
    const page = document.createElement("td");
    if (row.page_image_url) {
      page.append(makeEvidenceButton(row, textValue(row.page)));
    } else {
      page.textContent = textValue(row.page);
    }
    rowNode.append(metric, current, previous, page);
    body.append(rowNode);
  });
  table.append(head, body);
  scroll.append(table);
  wrapper.append(scroll);
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
  root.append(renderEvidence(Array.isArray(result.evidence) ? result.evidence : []));
  root.append(renderRows(Array.isArray(result.rows) ? result.rows : []));
  root.append(renderIssues(
    Array.isArray(result.issues) ? result.issues : [],
    Array.isArray(result.failures) ? result.failures : [],
  ));
  const report = renderReport(result.report_md);
  if (report) root.append(report);
}
