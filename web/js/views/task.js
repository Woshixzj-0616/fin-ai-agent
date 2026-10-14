const STAGES = [
  {
    key: "run",
    number: "01",
    title: "任务准备",
    detail: "校验输入与运行身份",
    phases: new Set(["run", "identity"]),
  },
  {
    key: "extraction",
    number: "02",
    title: "文档解析",
    detail: "提取页码与原文证据",
    phases: new Set(["extraction"]),
  },
  {
    key: "decision",
    number: "03",
    title: "指标核查",
    detail: "计算并执行本地裁决",
    phases: new Set(["validation", "calculation", "claim", "model", "tools", "system"]),
  },
  {
    key: "report",
    number: "04",
    title: "报告整理",
    detail: "汇总结构化结论",
    phases: new Set(["report"]),
  },
];

function currentStageIndex(state) {
  if (["succeeded", "partial_failed"].includes(state.currentRun?.status)) return STAGES.length - 1;
  const phase = state.currentRun?.current_phase;
  if (phase) {
    const index = STAGES.findIndex((stage) => stage.phases.has(phase));
    if (index >= 0) return index;
  }
  const latest = state.events[state.events.length - 1];
  if (latest?.phase) {
    const index = STAGES.findIndex((stage) => stage.phases.has(latest.phase));
    if (index >= 0) return index;
  }
  return 0;
}

function stageState(index, state) {
  const currentRun = state.currentRun;
  if (!currentRun) return "idle";
  const status = currentRun.status || "idle";
  const activeIndex = currentStageIndex(state);
  const terminal = ["succeeded", "partial_failed", "failed", "cancelled", "expired"].includes(status);
  if (terminal) {
    if (status === "succeeded" || status === "partial_failed") return "complete";
    if (index < activeIndex) return "complete";
    if (index === activeIndex) return "error";
    return "idle";
  }
  if (index < activeIndex) return "complete";
  if (index === activeIndex) return "active";
  return "idle";
}

function stageDetail(stage, index, state) {
  const latest = state.events[state.events.length - 1];
  const latestStage = STAGES[currentStageIndex(state)];
  if (latest && latestStage?.key === stage.key && index === currentStageIndex(state)) {
    return latest.summary || latest.title || stage.detail;
  }
  return stage.detail;
}

export function renderTaskView(root, state) {
  root.replaceChildren();

  const grid = document.createElement("div");
  grid.className = "stage-grid";
  const activeIndex = currentStageIndex(state);

  STAGES.forEach((stage, index) => {
    const item = document.createElement("article");
    item.className = "stage-item";
    item.dataset.state = stageState(index, state);
    if (index === activeIndex) item.dataset.current = "true";

    const indexNode = document.createElement("span");
    indexNode.className = "stage-index";
    indexNode.textContent = stage.number;

    const titleNode = document.createElement("strong");
    titleNode.textContent = stage.title;

    const detailNode = document.createElement("span");
    detailNode.className = "stage-detail";
    detailNode.textContent = stageDetail(stage, index, state);

    item.append(indexNode, titleNode, detailNode);
    grid.append(item);
  });

  root.append(grid);
}
