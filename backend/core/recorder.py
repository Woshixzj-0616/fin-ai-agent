"""Append-only per-run records with large payloads stored as local artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    raise TypeError(f"无法记录类型: {type(value).__name__}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned[:72] or "artifact"


class RunRecorder:
    """Record actual run events immediately; never write credentials or headers."""

    def __init__(self, run_dir: str | Path, *, run_id: str, module_id: str):
        self.run_dir = Path(run_dir).resolve()
        self.run_id = run_id
        self.module_id = module_id
        self.events_path = self.run_dir / "events.jsonl"
        self.artifacts_dir = self.run_dir / "artifacts"
        self._lock = threading.RLock()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def save_artifact(self, name: str, value: Any) -> dict[str, Any]:
        filename = f"{_slug(name)}.json"
        target = (self.artifacts_dir / filename).resolve()
        if self.artifacts_dir.resolve() not in target.parents:
            raise ValueError("artifact path escapes the run directory")
        payload = json.dumps(value, ensure_ascii=False, indent=2, default=_json_default)
        content = payload.encode("utf-8")
        with self._lock:
            target.write_bytes(content)
        return {
            "path": str(target.relative_to(self.run_dir)),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        row = {
            "event_id": uuid.uuid4().hex,
            "at": _now(),
            "run_id": self.run_id,
            "module_id": self.module_id,
            "event": event,
            **fields,
        }
        serialized = json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=_json_default)
        with self._lock:
            with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return row

    def progress(self, stage: str, status: str, detail: str = "") -> None:
        self.record("module_progress", stage=stage, status=status, detail=detail)
        payload = {"at": _now(), "stage": stage, "status": status, "detail": detail}
        target = self.run_dir / "progress.json"
        with self._lock:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def tool_call(self, name: str, arguments: Any, result: Any = None, *, error: str = "", pages: list[int] | None = None) -> None:
        call_id = uuid.uuid4().hex
        input_artifact = self.save_artifact(f"tool_{call_id}_input", arguments)
        output_artifact = self.save_artifact(f"tool_{call_id}_output", result) if result is not None else None
        self.record(
            "tool_call",
            call_id=call_id,
            tool=name,
            input=input_artifact,
            output=output_artifact,
            pages=pages or [],
            error=error,
        )

    def calculation(self, name: str, *, inputs: Any, formula: str, output: Any, rule_version: str = "") -> None:
        call_id = uuid.uuid4().hex
        input_artifact = self.save_artifact(f"calc_{call_id}_input", inputs)
        output_artifact = self.save_artifact(f"calc_{call_id}_output", output)
        self.record(
            "calculation",
            calculation=name,
            rule_version=rule_version,
            formula=formula,
            inputs=input_artifact,
            output=output_artifact,
        )

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        self.record("run_manifest", manifest=manifest)
        (self.run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
        )

    def write_result(self, result: dict[str, Any]) -> None:
        artifact = self.save_artifact("complete_result", result)
        self.record("result_saved", artifact=artifact, status=result.get("status"))
        (self.run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8"
        )
