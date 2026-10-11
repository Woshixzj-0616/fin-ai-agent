"""后端运行控制：沿用 fin 协议，集中配置、全文编号、分块和跨恢复预算。

不保存凭证，不依赖前端或 new 的目录、环境和历史记录。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULTS = {
    "http": {"timeout_seconds": 60, "request_deadline_seconds": 240,
             "max_retries": 3, "max_output_tokens": 8192},
    "document": {"deadline_seconds": 600, "max_characters": 240000,
                 "max_sentences": 2000, "chunk_characters": 12000,
                 "chunk_sentences": 40, "max_http_attempts": 40, "token_budget": 500000},
    "sources": {"timeout_seconds": 20, "request_deadline_seconds": 60,
                "task_deadline_seconds": 600, "max_attempts": 3, "max_http_attempts": 120,
                "min_interval_milliseconds": 1000, "max_redirects": 5, "max_bytes": 104857600},
    "tools": {"max_rounds": 5, "max_calls": 30, "max_repairs": 2},
}
_BOUNDS = {
    ("http", "timeout_seconds"): (1, 600),
    ("http", "request_deadline_seconds"): (1, 1800),
    ("http", "max_retries"): (0, 5),
    ("http", "max_output_tokens"): (256, 32768),
    ("document", "deadline_seconds"): (1, 7200),
    ("document", "max_characters"): (1, 1000000),
    ("document", "max_sentences"): (1, 10000),
    ("document", "chunk_characters"): (1, 12000),
    ("document", "chunk_sentences"): (1, 40),
    ("document", "max_http_attempts"): (1, 500),
    ("document", "token_budget"): (1, 10000000),
    ("tools", "max_rounds"): (1, 30),
    ("tools", "max_calls"): (0, 300),
    ("tools", "max_repairs"): (0, 10),
    ("sources", "timeout_seconds"): (1, 120),
    ("sources", "request_deadline_seconds"): (1, 600),
    ("sources", "task_deadline_seconds"): (1, 7200),
    ("sources", "max_attempts"): (1, 5),
    ("sources", "max_http_attempts"): (1, 1000),
    ("sources", "min_interval_milliseconds"): (0, 10000),
    ("sources", "max_redirects"): (0, 10),
    ("sources", "max_bytes"): (1024, 104857600),
}


class ExecutionLimit(RuntimeError):
    """时间或预算耗尽；不是可重试的网络故障。"""


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def load_config(path: Path | None = None) -> dict:
    selected = Path(path or os.environ.get("FIN_AUDIT_CONFIG") or
                    Path(__file__).with_name("backend_config.json"))
    content = selected.read_bytes()
    if len(content) > 65536:
        raise ValueError("后端配置超过64KiB")
    supplied = json.loads(content.decode("utf-8-sig"))
    config = copy.deepcopy(DEFAULTS)
    if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
        raise ValueError("后端配置含未知分组")
    for section, values in supplied.items():
        if not isinstance(values, dict) or set(values) - set(DEFAULTS[section]):
            raise ValueError(f"后端配置含未知字段：{section}")
        config[section].update(values)
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    if not isinstance(config, dict) or set(config) != set(DEFAULTS):
        raise ValueError("后端配置分组不完整")
    for section, expected in DEFAULTS.items():
        if not isinstance(config[section], dict) or set(config[section]) != set(expected):
            raise ValueError(f"后端配置字段不完整：{section}")
        for key, value in config[section].items():
            low, high = _BOUNDS[(section, key)]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"后端配置越界：{section}.{key}")
    if config["http"]["timeout_seconds"] > config["http"]["request_deadline_seconds"]:
        raise ValueError("单次连接/读取时限不能超过请求总时限")
    if config["sources"]["timeout_seconds"] > config["sources"]["request_deadline_seconds"]:
        raise ValueError("来源连接/读取时限不能超过来源请求总时限")


def atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def sentence_rows(draft: str, config: dict) -> list[dict]:
    limits = config["document"]
    if not draft.strip():
        raise ValueError("草稿为空")
    if len(draft) > limits["max_characters"]:
        raise ValueError("草稿超过已配置的整篇字数上限；没有截断输入")
    texts = [line.strip() for line in re.split(r"(?<=[。！？；])|\r?\n+", draft) if line.strip()]
    if len(texts) > limits["max_sentences"]:
        raise ValueError("草稿超过已配置的整篇句数上限；没有排除后续句子")
    rows, cursor = [], 0
    for number, text in enumerate(texts, 1):
        start = draft.find(text, cursor)
        if start < 0:
            raise ValueError("句子位置无法定位")
        rows.append({"sentence_id": number, "text": text,
                     "start": start, "end": start + len(text)})
        cursor = start + len(text)
    return rows


def plan_chunks(draft: str, rows: list[dict], config: dict) -> list[dict]:
    """保持整句与原文偏移；章节边界优先。超长单句单列失败，不切掉后半句。"""
    limits = config["document"]
    chunks, group, heading = [], [], ""

    def emit():
        nonlocal group
        if group:
            start, end = group[0]["start"], group[-1]["end"]
            chunks.append({"chunk_id": len(chunks) + 1, "start": start, "end": end,
                           "text": draft[start:end], "heading": heading,
                           "sentences": [{"sentence_id": r["sentence_id"], "text": r["text"]}
                                         for r in group],
                           "oversized_sentence": len(group) == 1 and
                           end - start > limits["chunk_characters"]})
            group = []

    for row in rows:
        title = bool(re.match(r"^(?:#{1,6}\s|[一二三四五六七八九十]+[、．.]|第[一二三四五六七八九十\d]+[章节])",
                              row["text"]))
        if title:
            emit()
            heading = row["text"]
        if group and (len(group) >= limits["chunk_sentences"] or
                      row["end"] - group[0]["start"] > limits["chunk_characters"]):
            emit()
        group.append(row)
        if row["end"] - row["start"] > limits["chunk_characters"]:
            emit()
    emit()
    return chunks


def chunk_context(draft: str, chunk: dict, companies: dict) -> dict:
    """只继承原文出现的最近公司与期间提示；不把已加载材料当作草稿上下文。"""
    preceding = draft[:chunk["start"]]
    hits = []
    for code, names in companies.items():
        for name in [code, *names]:
            pos = preceding.rfind(name)
            if pos >= 0:
                hits.append((pos, code, name))
    company = max(hits, default=None)
    anchor = preceding[company[0]:] if company else preceding[-1000:]
    periods = list(re.finditer(r"(?:19|20)\d{2}年?[^。！？\r\n]{0,16}", anchor))
    return {"section_heading": chunk["heading"],
            "company_code": company[1] if company else None,
            "company_text": company[2] if company else None,
            "period_text": periods[-1].group() if periods else None,
            "basis": "仅来自当前分块之前的草稿原文；分块内明确表述优先"}


class ExecutionBudget:
    """尝试/工具/Token跨恢复累计；任务时限按每次主动运行计，不包含人工等待。"""

    def __init__(self, config: dict, run, previous: dict | None = None):
        self.config, self.run, self.started = config, run, time.monotonic()
        self.deadline = self.started + config["document"]["deadline_seconds"]
        state = previous or {}
        if state:
            attempts = state.get("attempts")
            if not isinstance(attempts, list):
                raise ValueError("恢复预算缺少尝试记录")
            known, charged, unknown = 0, 0, 0
            for index, attempt in enumerate(attempts, 1):
                if (not isinstance(attempt, dict) or attempt.get("attempt") != index
                        or type(attempt.get("reserved_tokens")) is not int or attempt["reserved_tokens"] < 0):
                    raise ValueError("恢复预算的尝试记录不合法")
                tokens = attempt.get("tokens")
                if tokens is None:
                    charged += attempt["reserved_tokens"]
                    unknown += 1
                elif type(tokens) is int and tokens >= 0:
                    known += tokens
                    charged += tokens
                else:
                    raise ValueError("恢复预算用量不合法")
            if (state.get("known_tokens") != known or state.get("tokens_charged") != charged
                    or state.get("unknown_attempts") != unknown
                    or type(state.get("tool_calls")) is not int or state["tool_calls"] < 0):
                raise ValueError("恢复预算汇总与逐次记录不一致")
        self.attempts = copy.deepcopy(state.get("attempts", []))
        self.tool_calls = int(state.get("tool_calls", 0))
        self.tokens_charged = int(state.get("tokens_charged", 0))
        self.known_tokens = int(state.get("known_tokens", 0))
        self.unknown_attempts = int(state.get("unknown_attempts", 0))
        self.previous_seconds = float(state.get("active_seconds", 0))

    def remaining_seconds(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ExecutionLimit("整篇任务时限已用尽")
        return remaining

    def before_attempt(self, payload: dict) -> None:
        self.remaining_seconds()
        limits = self.config["document"]
        if len(self.attempts) >= limits["max_http_attempts"]:
            raise ExecutionLimit("整篇HTTP尝试预算已用尽（网络重试也计数）")
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        reserve = len(encoded) + self.config["http"]["max_output_tokens"]
        if self.tokens_charged + reserve > limits["token_budget"]:
            raise ExecutionLimit("整篇Token预算不足以预留下一次请求")
        record = {"attempt": len(self.attempts) + 1,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "request_sha256": hashlib.sha256(encoded).hexdigest(),
                  "reserved_tokens": reserve, "tokens": None, "status": "started"}
        self.attempts.append(record)
        self.tokens_charged += reserve
        self.unknown_attempts += 1
        self.run.event("llm_attempt_started", **record)

    def after_attempt(self, status: int | None, body: dict | None, elapsed: float) -> None:
        record = self.attempts[-1]
        usage = body.get("usage") if isinstance(body, dict) else None
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        if type(tokens) is int and tokens >= 0:
            record["tokens"] = tokens
            self.tokens_charged += tokens - record["reserved_tokens"]
            self.known_tokens += tokens
            self.unknown_attempts -= 1
        record.update(status=status, elapsed_seconds=round(elapsed, 4))
        self.run.event("llm_attempt_finished", **record)

    def before_tool(self) -> bool:
        self.remaining_seconds()
        if self.tool_calls >= self.config["tools"]["max_calls"]:
            return False
        self.tool_calls += 1
        return True

    def snapshot(self) -> dict:
        return {"attempts": self.attempts, "tool_calls": self.tool_calls,
                "tokens_charged": self.tokens_charged, "known_tokens": self.known_tokens,
                "unknown_attempts": self.unknown_attempts,
                "active_seconds": round(self.previous_seconds + time.monotonic() - self.started, 4),
                "token_accounting": "已返回用量按实际值；未知尝试保留请求UTF-8字节数+输出上限预占"}
