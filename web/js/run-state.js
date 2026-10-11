const EMPTY_STATE = Object.freeze({
  runs: [],
  currentRunId: null,
  currentRun: null,
  events: [],
  result: null,
  connection: "idle",
  error: null,
  cancelPending: false,
  askHistory: [],
  searchResults: [],
  searchStatus: "idle",
});

export function createRunStore() {
  let state = { ...EMPTY_STATE };
  const listeners = new Set();

  function getState() {
    return state;
  }

  function setState(update) {
    const patch = typeof update === "function" ? update(state) : update;
    if (!patch || typeof patch !== "object") {
      throw new TypeError("setState 需要对象或返回对象的函数");
    }
    state = { ...state, ...patch };
    for (const listener of listeners) {
      listener(state);
    }
    return state;
  }

  function subscribe(listener) {
    if (typeof listener !== "function") {
      throw new TypeError("subscribe 需要函数");
    }
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  return { getState, setState, subscribe };
}

export { EMPTY_STATE };
