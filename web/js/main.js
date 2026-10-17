import { createApiClient } from "./api.js";
import { createRunStore } from "./run-state.js";
import { createRunEventStream } from "./stream.js";
import { renderAskView } from "./views/ask.js";
import { renderResultsView } from "./views/results.js";
import { renderTaskView } from "./views/task.js";
import { renderTraceView } from "./views/trace.js";

const TERMINAL_STATUSES = new Set([
  "succeeded",
  "partial_failed",
  "failed",
  "cancelled",
  "expired",
]);

const RUN_STATUS = new Set([
  "queued",
  "running",
  "succeeded",
  "partial_failed",
  "failed",
  "cancelled",
  "expired",
]);

const STATUS_LABELS = {
  idle: "等待任务",
  queued: "等待执行",
  running: "正在分析",
  succeeded: "分析完成",
  partial_failed: "部分完成",
  failed: "分析失败",
  cancelled: "已取消",
  expired: "已超时",
};

const api = createApiClient();
const store = createRunStore();
let submitting = false;

const elements = {
  body: document.body,
  runList: document.querySelector("#run-list"),
  runSidebarToggle: document.querySelector("#run-sidebar-toggle"),
  sidebarBackdrop: document.querySelector("#sidebar-backdrop"),
  stageView: document.querySelector("#stage-view"),
  traceView: document.querySelector("#trace-view"),
  resultsView: document.querySelector("#results-view"),
  askView: document.querySelector("#ask-view"),
  askContent: document.querySelector("#ask-content"),
  askForm: document.querySelector("#ask-form"),
  askInput: document.querySelector("#ask-input"),
  searchForm: document.querySelector("#search-form"),
  searchInput: document.querySelector("#search-input"),
  statusRegion: document.querySelector("#status-region"),
  errorRegion: document.querySelector("#error-region"),
  taskForm: document.querySelector("#task-form"),
  taskCommand: document.querySelector("#task-command"),
  financialFields: document.querySelector("#financial-fields"),
  announcementFields: document.querySelector("#announcement-fields"),
  taskSubmit: document.querySelector("#task-submit"),
  cancelRun: document.querySelector("#cancel-run"),
  formNote: document.querySelector(".form-note"),
  sidebarFooter: document.querySelector(".sidebar-footer"),
  evidenceDialog: document.querySelector("#evidence-dialog"),
  evidenceClose: document.querySelector("#evidence-close"),
  evidenceImage: document.querySelector("#evidence-image"),
  evidenceBoxes: document.querySelector("#evidence-boxes"),
  evidenceStage: document.querySelector("#evidence-stage"),
  evidenceTitle: document.querySelector("#evidence-title"),
  evidenceCaption: document.querySelector("#evidence-caption"),
};

const stream = createRunEventStream({
  onEvent: handleStreamEvent,
  onConnection: handleConnection,
  onError: handleStreamError,
});

function runIdentity(run) {
  const summary = run?.input_summary || {};
  return summary.name || summary.code || run?.run_id || "未命名运行";
}

function currentRun(state = store.getState()) {
  return state.currentRun || state.runs.find((run) => run.run_id === state.currentRunId) || null;
}

function isTerminal(run) {
  return TERMINAL_STATUSES.has(run?.status);
}

function persistCurrentRun(runId) {
  try {
    if (runId) localStorage.setItem("fin-current-run", runId);
    else localStorage.removeItem("fin-current-run");
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
}

function storedCurrentRun() {
  try {
    return localStorage.getItem("fin-current-run");
  } catch {
    return null;
  }
}

function upsertRun(run) {
  if (!run?.run_id) return;
  const state = store.getState();
  const existing = state.runs.filter((item) => item.run_id !== run.run_id);
  store.setState({
    runs: [run, ...existing],
    currentRun: state.currentRunId === run.run_id ? { ...state.currentRun, ...run } : state.currentRun,
  });
}

function setError(error) {
  store.setState({ error: error ? String(error.message || error) : null });
}

function setConnection(connection) {
  store.setState({ connection });
}

function renderRunList(state) {
  elements.runList.replaceChildren();
  if (!state.runs.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state empty-state-compact";
    const title = document.createElement("strong");
    title.textContent = "尚无运行";
    const detail = document.createElement("span");
    detail.textContent = "建立核查任务后会显示在这里。";
    empty.append(title, detail);
    elements.runList.append(empty);
    return;
  }

  state.runs.forEach((run) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "run-item";
    item.dataset.runId = run.run_id;
    item.setAttribute("aria-pressed", String(run.run_id === state.currentRunId));
    if (run.run_id === state.currentRunId) item.dataset.current = "true";

    const title = document.createElement("strong");
    title.textContent = runIdentity(run);
    const detail = document.createElement("span");
    detail.textContent = `${STATUS_LABELS[run.status] || run.status || "等待任务"} · ${run.run_id || ""}`;
    item.append(title, detail);
    elements.runList.append(item);
  });
}

function renderStatus(state) {
  const run = currentRun(state);
  const runStatus = run?.status || "idle";
  const visualStatus = state.error ? "failed" : runStatus;

  elements.statusRegion.dataset.status = visualStatus;
  elements.statusRegion.textContent = state.error
    ? "运行异常"
    : run
      ? `${STATUS_LABELS[runStatus] || runStatus} · ${runIdentity(run)}`
      : STATUS_LABELS[runStatus] || runStatus;

  elements.errorRegion.hidden = !state.error;
  elements.errorRegion.textContent = state.error || "";

  const footerLabel = elements.sidebarFooter?.querySelector(".sidebar-status-label");
  if (footerLabel) {
    footerLabel.textContent = state.error
      ? "运行异常"
      : STATUS_LABELS[runStatus] || runStatus;
  }

  const dot = elements.sidebarFooter?.querySelector(".status-dot");
  if (dot) {
    dot.className = "status-dot";
    dot.classList.add(
      state.error || visualStatus === "failed" ? "status-dot-error" :
      visualStatus === "succeeded" ? "status-dot-success" :
      visualStatus === "running" ? "status-dot-running" : "status-dot-idle",
    );
  }
}

function renderControls(state) {
  const run = currentRun(state);
  const running = Boolean(run && !isTerminal(run));
  elements.cancelRun.disabled = !running || state.cancelPending;
  elements.cancelRun.textContent = state.cancelPending ? "正在取消…" : "取消运行";

  const file = elements.taskForm.elements.namedItem("pdf")?.files?.[0];
  elements.taskSubmit.disabled = submitting || !file;
  elements.taskSubmit.textContent = submitting ? "正在提交…" : "开始核查";
  elements.taskCommand.disabled = submitting;
  elements.formNote.textContent = running
    ? "当前运行仍在进行；新提交会创建一条独立运行。"
    : file
      ? `已选择：${file.name}`
      : "尚未开始分析。";
}

function renderApp(state) {
  renderRunList(state);
  renderStatus(state);
  renderTaskView(elements.stageView, state);
  renderTraceView(elements.traceView, state);
  renderResultsView(elements.resultsView, state);
  renderAskView(elements.askContent, state);
  renderControls(state);
}

function setSidebarOpen(open) {
  elements.body.dataset.sidebarOpen = open ? "true" : "false";
  elements.runSidebarToggle.setAttribute("aria-expanded", String(open));
  elements.runSidebarToggle.setAttribute(
    "aria-label",
    open ? "关闭运行列表" : "展开运行列表",
  );
}

function updateRunFromEvent(payload) {
  const state = store.getState();
  if (!payload || payload.run_id !== state.currentRunId) return;
  const lastSeq = Number(state.events[state.events.length - 1]?.seq || 0);
  const nextSeq = Number(payload.seq || 0);
  if (!Number.isFinite(nextSeq) || nextSeq <= lastSeq) return;

  const events = [...state.events, payload];
  const run = { ...(state.currentRun || {}), run_id: payload.run_id };
  if (RUN_STATUS.has(payload.status)) run.status = payload.status;
  if (payload.phase) run.current_phase = payload.phase;
  if (payload.status === "running") run.status = "running";
  if (TERMINAL_STATUSES.has(payload.status)) {
    run.status = payload.status;
    run.finished_at = payload.timestamp || run.finished_at;
  }

  store.setState({ events, currentRun: run });
  const runIndex = state.runs.findIndex((item) => item.run_id === payload.run_id);
  if (runIndex >= 0) {
    const runs = [...state.runs];
    runs[runIndex] = { ...runs[runIndex], ...run };
    store.setState({ runs });
  }
}

async function handleStreamEvent(payload) {
  updateRunFromEvent(payload);
  if (!TERMINAL_STATUSES.has(payload?.status)) return;
  stream.close();
  setConnection("closed");
  await loadRun(payload.run_id, { connect: false, includeResult: true });
  await refreshRuns();
}

function handleConnection(connection) {
  setConnection(connection === "open" ? "open" : connection);
  if (connection === "open") setError(null);
}

function handleStreamError() {
  const state = store.getState();
  if (isTerminal(state.currentRun)) return;
  setConnection("reconnecting");
}

async function refreshRuns() {
  const payload = await api.listRuns();
  const runs = Array.isArray(payload?.runs) ? payload.runs : [];
  store.setState({ runs });
  return runs;
}

async function loadRun(runId, { connect = true, includeResult = false } = {}) {
  if (!runId) return null;
  const state = store.getState();
  if (state.currentRunId !== runId) {
    store.setState({
      currentRunId: runId,
      currentRun: null,
      events: [],
      result: null,
      error: null,
      cancelPending: false,
      askHistory: [],
      searchResults: [],
      searchStatus: "idle",
      connection: "idle",
    });
  }
  persistCurrentRun(runId);
  try {
    const run = await api.getRun(runId);
    upsertRun(run);
    store.setState({ currentRun: run });
    if (isTerminal(run)) {
      if (includeResult || !store.getState().result) {
        const detail = await api.getRun(runId, { includeResult: true });
        upsertRun(detail);
        store.setState({ currentRun: detail, result: detail.result || null });
      }
      setConnection("closed");
      if (connect && !store.getState().events.length) {
        stream.connect(runId, { afterSeq: 0 });
      }
      return run;
    }
    if (connect) {
      const afterSeq = Number(store.getState().events[store.getState().events.length - 1]?.seq || 0);
      stream.connect(runId, { afterSeq });
    }
    return run;
  } catch (error) {
    setError(error);
    return null;
  }
}

async function selectRun(runId) {
  if (!runId || runId === store.getState().currentRunId) {
    if (runId) await loadRun(runId);
    return;
  }
  stream.close();
  await loadRun(runId);
}

async function initialise() {
  try {
    const runs = await refreshRuns();
    const saved = storedCurrentRun();
    const selected = runs.find((run) => run.run_id === saved)?.run_id || runs[0]?.run_id;
    if (selected) await loadRun(selected, { connect: true });
    else setConnection("idle");
  } catch (error) {
    setError(error);
  }
}

function commandValue() {
  return elements.taskCommand.value === "announcement" ? "announcement" : "financial";
}

function buildTaskForm() {
  const form = new FormData();
  const command = commandValue();
  const fields = elements.taskForm.elements;
  form.append("command", command);
  const pdf = fields.namedItem("pdf")?.files?.[0];
  if (!pdf) throw new Error("请选择 PDF 文件");
  form.append("pdf", pdf);

  if (command === "financial") {
    for (const name of ["code", "name", "year", "draft"]) {
      const value = fields.namedItem(name)?.value?.trim();
      if (value) form.append(name, value);
    }
    const comparisons = fields.namedItem("comparison_pdf")?.files || [];
    Array.from(comparisons).slice(0, 4).forEach((file) => {
      form.append("comparison_pdf", file);
    });
  } else {
    form.append("event_type", fields.namedItem("event_type")?.value || "auto");
    const ocr = fields.namedItem("ocr");
    form.append("ocr", ocr?.checked ? "true" : "false");
  }
  return form;
}

async function handleTaskSubmit(event) {
  event.preventDefault();
  if (submitting) return;
  try {
    const form = buildTaskForm();
    submitting = true;
    setError(null);
    store.setState({ cancelPending: false });
    renderControls(store.getState());
    const created = await api.createRun(form);
    const runId = created.run_id;
    persistCurrentRun(runId);
    store.setState({
      currentRunId: runId,
      currentRun: {
        run_id: runId,
        status: created.status || "queued",
        input_summary: {},
      },
      events: [],
      result: null,
      error: null,
      connection: "idle",
      askHistory: [],
      searchResults: [],
      searchStatus: "idle",
    });
    await loadRun(runId, { connect: true });
    await refreshRuns();
    elements.taskForm.reset();
    commandChanged();
  } catch (error) {
    setError(error);
  } finally {
    submitting = false;
    renderControls(store.getState());
  }
}

async function handleCancel() {
  const state = store.getState();
  const run = currentRun(state);
  if (!run || isTerminal(run) || state.cancelPending) return;
  store.setState({ cancelPending: true });
  try {
    await api.cancelRun(run.run_id);
    await loadRun(run.run_id, { connect: false, includeResult: true });
    await refreshRuns();
  } catch (error) {
    setError(error);
  } finally {
    store.setState({ cancelPending: false });
  }
}

function openEvidence(button) {
  const imageUrl = button.dataset.imageUrl;
  if (!imageUrl) return;
  const pageLabel = button.dataset.pageLabel || button.textContent.trim() || "—";
  const pageWidth = Number(button.dataset.pageWidth) || 0;
  const pageHeight = Number(button.dataset.pageHeight) || 0;
  elements.evidenceTitle.textContent = `页图定位 · ${pageLabel}`;
  elements.evidenceCaption.textContent = button.dataset.evidenceId || "";
  elements.evidenceImage.dataset.pageWidth = String(pageWidth);
  elements.evidenceImage.dataset.pageHeight = String(pageHeight);
  elements.evidenceImage.dataset.valueBbox = button.dataset.valueBbox || "";
  elements.evidenceImage.dataset.labelBbox = button.dataset.labelBbox || "";
  elements.evidenceBoxes.replaceChildren();
  if (pageWidth && pageHeight) {
    elements.evidenceStage.style.aspectRatio = `${pageWidth} / ${pageHeight}`;
  } else {
    elements.evidenceStage.style.removeProperty("aspect-ratio");
  }
  elements.evidenceImage.onload = () => drawEvidenceBoxes();
  elements.evidenceImage.src = imageUrl;
  if (elements.evidenceDialog.showModal) elements.evidenceDialog.showModal();
  else elements.evidenceDialog.setAttribute("open", "");
  requestAnimationFrame(drawEvidenceBoxes);
}

function drawBoxes(bbox, pageWidth, pageHeight, label) {
  if (!bbox || !pageWidth || !pageHeight) return;
  const [x0, y0, x1, y1] = bbox.map(Number);
  if (![x0, y0, x1, y1].every(Number.isFinite)) return;
  const left = Math.max(0, Math.min(100, (x0 / pageWidth) * 100));
  const top = Math.max(0, Math.min(100, (y0 / pageHeight) * 100));
  const right = Math.max(left, Math.min(100, (x1 / pageWidth) * 100));
  const bottom = Math.max(top, Math.min(100, (y1 / pageHeight) * 100));
  const box = document.createElement("span");
  box.className = `evidence-box evidence-box-${label}`;
  box.style.left = `${left}%`;
  box.style.top = `${top}%`;
  box.style.width = `${right - left}%`;
  box.style.height = `${bottom - top}%`;
  elements.evidenceBoxes.append(box);
}

function drawEvidenceBoxes() {
  const image = elements.evidenceImage;
  const pageWidth = Number(image.dataset.pageWidth) || 0;
  const pageHeight = Number(image.dataset.pageHeight) || 0;
  elements.evidenceBoxes.replaceChildren();
  for (const [key, label] of [["valueBbox", "value"], ["labelBbox", "label"]]) {
    const raw = image.dataset[key];
    if (!raw) continue;
    try {
      drawBoxes(JSON.parse(raw), pageWidth, pageHeight, label);
    } catch {
      // Invalid geometry is omitted rather than guessed.
    }
  }
}

function closeEvidence() {
  elements.evidenceImage.removeAttribute("src");
  elements.evidenceBoxes.replaceChildren();
  if (elements.evidenceDialog.close) elements.evidenceDialog.close();
  else elements.evidenceDialog.removeAttribute("open");
}

async function handleAsk(event) {
  event.preventDefault();
  const question = elements.askInput.value.trim();
  const run = currentRun();
  if (!question || !run || !isTerminal(run)) return;
  try {
    const answer = await api.ask(run.run_id, question);
    store.setState({
      askHistory: [...store.getState().askHistory, { question, answer }],
      error: null,
    });
    elements.askInput.value = "";
  } catch (error) {
    setError(error);
  }
}

async function handleSearch(event) {
  event.preventDefault();
  const query = elements.searchInput.value.trim();
  const run = currentRun();
  if (!query || !run || !isTerminal(run)) return;
  store.setState({ searchStatus: "loading", searchResults: [] });
  try {
    const result = await api.search(run.run_id, query);
    const hits = Array.isArray(result.hits) ? result.hits : [];
    store.setState({
      searchResults: hits,
      searchStatus: result.status || (hits.length ? "ok" : "empty"),
      error: null,
    });
  } catch (error) {
    store.setState({ searchStatus: "error" });
    setError(error);
  }
}

function commandChanged() {
  const announcement = commandValue() === "announcement";
  elements.financialFields.hidden = announcement;
  elements.announcementFields.hidden = !announcement;
  renderControls(store.getState());
}

elements.runSidebarToggle.addEventListener("click", () => {
  setSidebarOpen(elements.body.dataset.sidebarOpen !== "true");
});

elements.sidebarBackdrop.addEventListener("click", () => {
  setSidebarOpen(false);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setSidebarOpen(false);
    if (elements.evidenceDialog.open) closeEvidence();
  }
});

elements.runList.addEventListener("click", (event) => {
  const item = event.target.closest("[data-run-id]");
  if (item) selectRun(item.dataset.runId);
});

elements.taskForm.addEventListener("submit", handleTaskSubmit);
elements.taskForm.addEventListener("change", renderControls.bind(null, store.getState()));
elements.taskCommand.addEventListener("change", commandChanged);
elements.cancelRun.addEventListener("click", handleCancel);
elements.resultsView.addEventListener("click", (event) => {
  const button = event.target.closest("[data-image-url]");
  if (button) openEvidence(button);
});
elements.askForm.addEventListener("submit", handleAsk);
elements.searchForm.addEventListener("submit", handleSearch);
elements.evidenceClose.addEventListener("click", closeEvidence);
elements.evidenceDialog.addEventListener("click", (event) => {
  if (event.target === elements.evidenceDialog) closeEvidence();
});
window.addEventListener("resize", () => {
  if (elements.evidenceDialog.open) drawEvidenceBoxes();
});

store.subscribe(renderApp);
renderApp(store.getState());
commandChanged();
initialise();
