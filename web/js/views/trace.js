const PHASE_LABELS = {
  run: "运行",
  identity: "身份",
  extraction: "解析",
  validation: "校验",
  calculation: "计算",
  claim: "主张",
  model: "模型",
  tools: "工具",
  report: "报告",
  system: "系统",
};

const ACTOR_LABELS = {
  runtime: "运行时",
  program: "本地程序",
  parser: "解析器",
  model: "模型",
  tools: "工具",
  legacy: "兼容层",
};

const SAFE_KEYS = new Set([
  "claim_count",
  "unclaimed_count",
  "claim_id",
  "claim_type",
  "metric",
  "metric_name",
  "period_year",
  "operator",
  "verification_action",
  "formula",
  "operation",
  "result",
  "tool",
  "status",
  "path",
  "size_bytes",
  "evidence_count",
  "document_id",
  "reused",
  "by_reference",
  "source_url",
]);

const PLAN_FIELDS = [
  "claim_id",
  "claim_type",
  "metric",
  "period_year",
  "operator",
  "verification_action",
];

function text(value, limit = 180) {
  if (value === null || value === undefined) return "";
  const output = typeof value === "object" ? JSON.stringify(value) : String(value);
  return output.length > limit ? `${output.slice(0, limit - 1)}…` : output;
}

function formatDuration(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return "";
  if (number < 1000) return `${Math.round(number)} ms`;
  return `${(number / 1000).toFixed(number < 10000 ? 1 : 0)} s`;
}

function formatPlan(plan) {
  if (!Array.isArray(plan)) return "";
  return plan
    .slice(0, 12)
    .map((item, index) => {
      if (!item || typeof item !== "object") return "";
      const fields = PLAN_FIELDS
        .filter((key) => item[key] !== null && item[key] !== undefined)
        .map((key) => `${key}=${text(item[key], 60)}`);
      return `${index + 1}. ${fields.join(" · ") || "结构化主张"}`;
    })
    .filter(Boolean)
    .join("\n");
}

function safePayloadRows(payload) {
  if (!payload || typeof payload !== "object") return [];
  const rows = [];
  for (const [key, value] of Object.entries(payload)) {
    if (key === "plan") continue;
    if (!SAFE_KEYS.has(key)) continue;
    if (value === null || value === undefined || value === "") continue;
    rows.push([key, text(value)]);
  }
  if (Array.isArray(payload.plan) && payload.plan.length) {
    const counts = `${payload.claim_count ?? payload.plan.length} 条主张`;
    rows.unshift(["计划", counts]);
  }
  return rows.slice(0, 8);
}

function panelHeader() {
  const header = document.createElement("header");
  header.className = "panel-header";

  const copy = document.createElement("div");
  const kicker = document.createElement("p");
  kicker.className = "section-kicker";
  kicker.textContent = "执行轨迹";
  const title = document.createElement("h3");
  title.textContent = "工具与裁决";
  copy.append(kicker, title);
  header.append(copy);

  return header;
}

function emptyTrace() {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  const title = document.createElement("strong");
  title.textContent = "执行轨迹尚未产生";
  const detail = document.createElement("span");
  detail.textContent = "分析开始后，这里会按顺序展示真实事件。";
  empty.append(title, detail);
  return empty;
}

function makeChip(label, value, className = "") {
  const chip = document.createElement("span");
  chip.className = `event-chip ${className}`.trim();
  const labelNode = document.createElement("b");
  labelNode.textContent = label;
  const valueNode = document.createElement("span");
  valueNode.textContent = value;
  chip.append(labelNode, valueNode);
  return chip;
}

export function renderTraceView(root, state) {
  root.replaceChildren(panelHeader());

  if (!state.events.length) {
    root.append(emptyTrace());
    return;
  }

  const list = document.createElement("ol");
  list.className = "trace-list";

  state.events.forEach((event) => {
    const item = document.createElement("li");
    item.className = "trace-item";
    item.dataset.status = event.status || "completed";

    const sequence = document.createElement("span");
    sequence.className = "trace-seq";
    sequence.textContent = String(event.seq ?? "—");

    const body = document.createElement("div");
    body.className = "trace-body";

    const heading = document.createElement("div");
    heading.className = "trace-heading";
    const title = document.createElement("strong");
    title.textContent = event.title || event.event_type || "运行事件";
    heading.append(title);

    const chips = document.createElement("div");
    chips.className = "trace-meta";
    chips.append(
      makeChip("阶段", PHASE_LABELS[event.phase] || event.phase || "系统"),
      makeChip("来源", ACTOR_LABELS[event.actor] || event.actor || "运行时"),
    );
    if (event.status && event.status !== "completed") {
      chips.append(makeChip("状态", event.status, `event-chip-${event.status}`));
    }
    const duration = formatDuration(event.duration_ms);
    if (duration) chips.append(makeChip("耗时", duration));
    heading.append(chips);

    body.append(heading);

    const summary = document.createElement("p");
    summary.textContent = event.summary || "";
    if (summary.textContent) body.append(summary);

    const rows = safePayloadRows(event.payload);
    if (rows.length) {
      const details = document.createElement("dl");
      details.className = "event-payload";
      rows.forEach(([key, value]) => {
        const label = document.createElement("dt");
        label.textContent = key;
        const content = document.createElement("dd");
        content.textContent = value;
        details.append(label, content);
      });
      body.append(details);
    }

    if (Array.isArray(event.payload?.plan) && event.payload.plan.length) {
      const plan = document.createElement("details");
      plan.className = "event-plan";
      const planTitle = document.createElement("summary");
      planTitle.textContent = "结构化计划";
      const planText = document.createElement("pre");
      planText.textContent = formatPlan(event.payload.plan);
      plan.append(planTitle, planText);
      body.append(plan);
    }

    if (Array.isArray(event.evidence_ids) && event.evidence_ids.length) {
      const evidence = document.createElement("div");
      evidence.className = "event-evidence";
      const label = document.createElement("span");
      label.textContent = "证据";
      evidence.append(label);
      event.evidence_ids.slice(0, 8).forEach((evidenceId) => {
        const chip = document.createElement("code");
        chip.textContent = evidenceId;
        evidence.append(chip);
      });
      body.append(evidence);
    }

    item.append(sequence, body);
    list.append(item);
  });

  root.append(list);
}
