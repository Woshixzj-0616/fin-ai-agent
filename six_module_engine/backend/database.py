"""Store local analysis runs and the original uploaded PDFs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_DATA = ROOT / "data" / "app"
UPLOAD_DIR = APP_DATA / "uploads"
DB_PATH = APP_DATA / "runs.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                analysis_module TEXT NOT NULL DEFAULT 'overview',
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 0,
                read_pages TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                result TEXT
            )
            """
        )
        columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(runs)")}
        if "analysis_module" not in columns:
            db.execute(
                "ALTER TABLE runs ADD COLUMN analysis_module TEXT NOT NULL DEFAULT 'overview'"
            )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS run_pages (
                run_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (run_id, page_number)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE (run_id, ordinal)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                error TEXT,
                trace TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "UPDATE runs SET status='failed', stage='服务重启', "
            "error='服务在任务完成前重启，请重新提交文件。', updated_at=? "
            "WHERE status IN ('queued', 'running')",
            (now_iso(),),
        )


def save_run_pages(run_id: str, pages: list[dict[str, Any]]) -> None:
    with connect() as db:
        db.executemany(
            "INSERT OR REPLACE INTO run_pages (run_id, page_number, text) VALUES (?, ?, ?)",
            [(run_id, int(page["page"]), str(page["text"])) for page in pages],
        )


def get_run_page(run_id: str, page_number: int) -> str | None:
    with connect() as db:
        row = db.execute(
            "SELECT text FROM run_pages WHERE run_id = ? AND page_number = ?",
            (run_id, page_number),
        ).fetchone()
    return str(row["text"]) if row else None


def search_run_pages(run_id: str, query: str, limit: int = 4) -> list[dict[str, Any]]:
    from backend.pdf_reader import rank_search_pages

    with connect() as db:
        rows = db.execute(
            "SELECT page_number, text FROM run_pages WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    pages = [{"page": int(row["page_number"]), "text": str(row["text"])} for row in rows]
    return rank_search_pages(pages, query, limit=limit)


def set_analysis_step(run_id: str, name: str, status: str, detail: str = "") -> None:
    with connect() as db:
        row = db.execute(
            "SELECT ordinal FROM analysis_steps WHERE run_id = ? AND name = ?",
            (run_id, name),
        ).fetchone()
        now = now_iso()
        if row:
            db.execute(
                "UPDATE analysis_steps SET status = ?, detail = ?, updated_at = ? "
                "WHERE run_id = ? AND name = ?",
                (status, detail[:1000], now, run_id, name),
            )
        else:
            ordinal = db.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM analysis_steps WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO analysis_steps (run_id, ordinal, name, status, detail, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, ordinal, name, status, detail[:1000], now),
            )


def list_analysis_steps(run_id: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT ordinal, name, status, detail, updated_at FROM analysis_steps "
            "WHERE run_id = ? ORDER BY ordinal",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_conversation_turn(turn: dict[str, Any]) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO conversation_turns "
            "(id, run_id, question, answer, status, error, trace, created_at, updated_at) "
            "VALUES (?, ?, ?, '', 'queued', NULL, '[]', ?, ?)",
            (turn["id"], turn["run_id"], turn["question"], turn["created_at"], turn["created_at"]),
        )


def update_conversation_turn(turn_id: str, **fields: Any) -> None:
    allowed = {"answer", "status", "error", "trace"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if "trace" in values and not isinstance(values["trace"], str):
        values["trace"] = json.dumps(values["trace"], ensure_ascii=False)
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as db:
        db.execute(
            f"UPDATE conversation_turns SET {assignments} WHERE id = ?",
            (*values.values(), turn_id),
        )


def _turn_as_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["trace"] = json.loads(item.get("trace") or "[]")
    return item


def get_conversation_turn(turn_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM conversation_turns WHERE id = ?", (turn_id,)).fetchone()
    return _turn_as_dict(row) if row else None


def list_conversation_turns(run_id: str, limit: int = 30) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM (SELECT * FROM conversation_turns WHERE run_id = ? "
            "ORDER BY created_at DESC LIMIT ?) ORDER BY created_at",
            (run_id, limit),
        ).fetchall()
    return [_turn_as_dict(row) for row in rows]


def has_pending_conversation_turn(run_id: str) -> bool:
    with connect() as db:
        row = db.execute(
            "SELECT 1 FROM conversation_turns WHERE run_id = ? "
            "AND status IN ('queued', 'running') LIMIT 1",
            (run_id,),
        ).fetchone()
    return row is not None


def create_run(run: dict[str, Any]) -> None:
    with connect() as db:
        db.execute(
            """
            INSERT INTO runs
              (id, file_name, file_path, analysis_module, sha256, status, stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["id"], run["file_name"], run["file_path"], run.get("analysis_module", "overview"), run["sha256"],
                run["status"], run["stage"], run["created_at"], run["updated_at"],
            ),
        )


def update_run(run_id: str, **fields: Any) -> None:
    allowed = {"status", "stage", "updated_at", "page_count", "read_pages", "error", "result"}
    values: dict[str, Any] = {key: value for key, value in fields.items() if key in allowed}
    if "read_pages" in values and not isinstance(values["read_pages"], str):
        values["read_pages"] = json.dumps(values["read_pages"], ensure_ascii=False)
    if "result" in values and not isinstance(values["result"], (str, type(None))):
        values["result"] = json.dumps(values["result"], ensure_ascii=False)
    values["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with connect() as db:
        db.execute(
            f"UPDATE runs SET {assignments} WHERE id = ?",
            (*values.values(), run_id),
        )


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["read_pages"] = json.loads(item["read_pages"] or "[]")
    item["result"] = json.loads(item["result"]) if item["result"] else None
    return item


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect() as db:
        return _as_dict(db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_as_dict(row) for row in rows if row is not None]
