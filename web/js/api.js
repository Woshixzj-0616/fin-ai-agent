const JSON_HEADERS = Object.freeze({
  "Content-Type": "application/json",
});

async function readResponse(response) {
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: { message: text } };
    }
  }
  if (!response.ok) {
    const message = payload?.error?.message || payload?.error || `请求失败 (${response.status})`;
    const error = new Error(String(message));
    error.status = response.status;
    error.code = payload?.error?.code || "HTTP_ERROR";
    error.payload = payload;
    throw error;
  }
  return payload;
}

export function createApiClient({ baseUrl = "" } = {}) {
  const root = String(baseUrl || "").replace(/\/$/, "");

  async function request(path, options = {}) {
    const response = await fetch(`${root}${path}`, {
      credentials: "same-origin",
      ...options,
    });
    return readResponse(response);
  }

  return {
    listRuns() {
      return request("/api/runs");
    },

    getRun(runId, { includeResult = false } = {}) {
      const suffix = includeResult ? "?include_result=1" : "";
      return request(`/api/runs/${encodeURIComponent(runId)}${suffix}`);
    },

    createRun(formData) {
      return request("/api/runs", {
        method: "POST",
        body: formData,
      });
    },

    cancelRun(runId) {
      return request(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
        method: "POST",
      });
    },

    ask(runId, question) {
      return request(`/api/runs/${encodeURIComponent(runId)}/ask`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ question }),
      });
    },

    search(runId, query, topK = 8) {
      return request(`/api/runs/${encodeURIComponent(runId)}/search`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ query, top_k: topK }),
      });
    },
  };
}
