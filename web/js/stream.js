const LAST_EVENT_ID_QUERY = "after_seq";

function parseEvent(event) {
  try {
    return JSON.parse(event.data);
  } catch {
    return {
      event_type: "message",
      title: "事件解析失败",
      summary: "服务端事件不是合法 JSON",
      payload: {},
      seq: Number(event.lastEventId || 0),
    };
  }
}

export function createRunEventStream({
  onEvent = () => {},
  onConnection = () => {},
  onError = () => {},
} = {}) {
  let source = null;
  let currentRunId = null;

  function close() {
    if (source) {
      source.close();
      source = null;
    }
    currentRunId = null;
  }

  function connect(runId, { afterSeq = 0 } = {}) {
    close();
    currentRunId = String(runId || "");
    if (!currentRunId) {
      throw new TypeError("runId 不能为空");
    }
    const cursor = Math.max(0, Number(afterSeq) || 0);
    const url = `/api/runs/${encodeURIComponent(currentRunId)}/events?${LAST_EVENT_ID_QUERY}=${cursor}`;
    // The browser owns the Last-Event-ID header; after_seq lets our
    // explicit reconnect path resume from the same persisted cursor.
    source = new EventSource(url, { withCredentials: true });

    source.onopen = () => {
      onConnection("open");
    };

    source.onmessage = (event) => {
      const payload = parseEvent(event);
      const lastEventId = Number(event.lastEventId || payload.seq || 0);
      onEvent(payload, {
        lastEventId: Number.isFinite(lastEventId) ? lastEventId : 0,
        lastEventIdHeader: "last-event-id",
      });
    };

    source.onerror = (error) => {
      onError(error);
      onConnection("error");
    };
  }

  return {
    connect,
    close,
    getRunId() {
      return currentRunId;
    },
    getReadyState() {
      return source ? source.readyState : EventSource.CLOSED;
    },
  };
}
