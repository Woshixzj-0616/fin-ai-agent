const TERMINAL = new Set(["succeeded", "partial_failed", "failed", "cancelled", "expired"]);

function emptyAsk(message) {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  const title = document.createElement("strong");
  title.textContent = "尚无可追问的结果";
  const detail = document.createElement("span");
  detail.textContent = message;
  empty.append(title, detail);
  return empty;
}

function answerText(answer) {
  if (typeof answer === "string") return answer;
  if (!answer || typeof answer !== "object") return "未返回答案";
  if (answer.answer) return String(answer.answer);
  if (answer.message) return String(answer.message);
  if (answer.status) return `状态：${answer.status}`;
  return "已返回结构化答案";
}

function evidenceCodes(ids) {
  const wrapper = document.createElement("div");
  wrapper.className = "answer-evidence";
  const label = document.createElement("span");
  label.textContent = "证据";
  wrapper.append(label);
  (Array.isArray(ids) ? ids : []).slice(0, 6).forEach((id) => {
    const code = document.createElement("code");
    code.textContent = String(id);
    wrapper.append(code);
  });
  return wrapper;
}

function renderHistory(history) {
  if (!history.length) return null;
  const section = document.createElement("section");
  section.className = "ask-section";
  const title = document.createElement("h4");
  title.textContent = "追问记录";
  section.append(title);

  history.forEach((item) => {
    const card = document.createElement("article");
    card.className = "answer-card";
    const question = document.createElement("strong");
    question.textContent = item.question || "未记录问题";
    const answer = document.createElement("p");
    answer.textContent = answerText(item.answer);
    card.append(question, answer);
    if (item.answer?.evidence_ids) card.append(evidenceCodes(item.answer.evidence_ids));
    section.append(card);
  });
  return section;
}

function renderSearch(status, results) {
  const section = document.createElement("section");
  section.className = "ask-section";
  const title = document.createElement("h4");
  title.textContent = "检索结果";
  section.append(title);

  if (status === "loading") {
    const loading = document.createElement("p");
    loading.className = "result-empty";
    loading.textContent = "正在检索当前运行的页文本…";
    section.append(loading);
    return section;
  }
  if (status === "empty") {
    const empty = document.createElement("p");
    empty.className = "result-empty";
    empty.textContent = "当前运行没有可检索的页文本。";
    section.append(empty);
    return section;
  }
  if (status === "error") {
    const error = document.createElement("p");
    error.className = "result-empty";
    error.textContent = "检索失败，请稍后重试。";
    section.append(error);
    return section;
  }
  if (!results.length) return null;

  results.forEach((hit) => {
    const card = document.createElement("article");
    card.className = "search-hit";
    const heading = document.createElement("strong");
    heading.textContent = `${hit.page ? `第 ${hit.page} 页` : "命中段落"} · ${hit.document_key || "当前运行"}`;
    const snippet = document.createElement("p");
    snippet.textContent = hit.text || hit.snippet || hit.excerpt || "";
    card.append(heading, snippet);
    if (hit.evidence_ids) card.append(evidenceCodes(hit.evidence_ids));
    section.append(card);
  });
  return section;
}

export function renderAskView(root, state) {
  root.replaceChildren();

  const run = state.currentRun;
  if (!run || !TERMINAL.has(run.status)) {
    root.append(emptyAsk("完成核查后，问题只会检索当前运行的证据。"));
    return;
  }

  const scope = document.createElement("p");
  scope.className = "ask-scope";
  scope.textContent = `当前运行：${run.input_summary?.name || run.input_summary?.code || run.run_id}`;
  root.append(scope);

  const history = renderHistory(Array.isArray(state.askHistory) ? state.askHistory : []);
  if (history) root.append(history);

  const search = renderSearch(state.searchStatus, Array.isArray(state.searchResults) ? state.searchResults : []);
  if (search) root.append(search);
}
