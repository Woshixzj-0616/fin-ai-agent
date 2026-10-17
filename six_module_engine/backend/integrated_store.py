"""Persistent local storage for whole-report, six-module analyses."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.core.config import PROJECT_ROOT


DATA_DIR = PROJECT_ROOT / "data" / "integrated_workbench"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "integrated.sqlite3"
SEED_DB = DATA_DIR / "integrated.seed.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_store() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists() and SEED_DB.is_file():
        # 首次运行用只含五粮液 2023 样稿的种子库，避免运行时数据进入仓库。
        shutil.copyfile(SEED_DB, DB_PATH)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                selected_modules TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 0,
                synthesis_status TEXT NOT NULL DEFAULT 'pending',
                synthesis TEXT,
                error TEXT,
                workbench_project_id TEXT,
                company_name TEXT,
                security_code TEXT,
                report_year TEXT
            );
            CREATE TABLE IF NOT EXISTS module_tasks (
                report_id TEXT NOT NULL,
                module_id TEXT NOT NULL,
                module_version TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                analysis_status TEXT,
                review_status TEXT,
                result TEXT,
                run_dir TEXT,
                error TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (report_id, module_id),
                FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_reports_updated ON reports(updated_at DESC);
            """
        )
        report_columns = {row[1] for row in db.execute("PRAGMA table_info(reports)").fetchall()}
        if "workbench_project_id" not in report_columns:
            db.execute("ALTER TABLE reports ADD COLUMN workbench_project_id TEXT")
        if "company_name" not in report_columns:
            db.execute("ALTER TABLE reports ADD COLUMN company_name TEXT")
        if "security_code" not in report_columns:
            db.execute("ALTER TABLE reports ADD COLUMN security_code TEXT")
        if "report_year" not in report_columns:
            db.execute("ALTER TABLE reports ADD COLUMN report_year TEXT")
        stale = db.execute(
            "SELECT id FROM reports WHERE status IN ('queued','running')"
        ).fetchall()
        for row in stale:
            report_id = str(row["id"])
            db.execute(
                "UPDATE module_tasks SET status='failed',stage='服务重启',detail='服务重启前任务未结束。',"
                "error='任务未完成；请重试该模块。',updated_at=? WHERE report_id=? AND status IN ('queued','running')",
                (now_iso(), report_id),
            )
            completed = db.execute(
                "SELECT COUNT(*) FROM module_tasks WHERE report_id=? AND status='completed'", (report_id,)
            ).fetchone()[0]
            db.execute(
                "UPDATE reports SET status=?,error='工作台服务重启；未完成模块可单独重试。',updated_at=? WHERE id=?",
                ("partial" if completed else "failed", now_iso(), report_id),
            )


def create_report(
    report: dict[str, Any],
    modules: list[dict[str, str]],
    *,
    project_id: str | None = None,
    company: str | None = None,
    code: str | None = None,
    year: str | None = None,
) -> None:
    now = now_iso()
    with connect() as db:
        db.execute(
            "INSERT INTO reports (id,file_name,file_path,sha256,selected_modules,status,created_at,updated_at,workbench_project_id,company_name,security_code,report_year) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                report["id"], report["file_name"], report["file_path"], report["sha256"],
                json.dumps([item["module_id"] for item in modules]), "queued", now, now,
                project_id, company, code, year,
            ),
        )
        db.executemany(
            "INSERT INTO module_tasks (report_id,module_id,module_version,status,updated_at) VALUES (?,?,?,?,?)",
            [(report["id"], item["module_id"], item["module_version"], "queued", now) for item in modules],
        )


def update_report(report_id: str, **fields: Any) -> None:
    allowed = {"status", "updated_at", "page_count", "synthesis_status", "synthesis", "error"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if "synthesis" in values and not isinstance(values["synthesis"], (str, type(None))):
        values["synthesis"] = json.dumps(values["synthesis"], ensure_ascii=False, default=str)
    values["updated_at"] = now_iso()
    if not values:
        return
    assignments = ",".join(f"{key}=?" for key in values)
    with connect() as db:
        db.execute(f"UPDATE reports SET {assignments} WHERE id=?", (*values.values(), report_id))


def update_module(report_id: str, module_id: str, **fields: Any) -> None:
    allowed = {
        "module_version", "status", "stage", "detail", "analysis_status", "review_status",
        "result", "run_dir", "error", "attempt",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if "result" in values and not isinstance(values["result"], (str, type(None))):
        values["result"] = json.dumps(values["result"], ensure_ascii=False, default=str)
    values["updated_at"] = now_iso()
    assignments = ",".join(f"{key}=?" for key in values)
    with connect() as db:
        db.execute(
            f"UPDATE module_tasks SET {assignments} WHERE report_id=? AND module_id=?",
            (*values.values(), report_id, module_id),
        )
        db.execute("UPDATE reports SET updated_at=? WHERE id=?", (now_iso(), report_id))


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for field in ("selected_modules", "synthesis", "result"):
        if value.get(field):
            try:
                value[field] = json.loads(value[field])
            except (TypeError, json.JSONDecodeError):
                pass
    return value


def get_report(report_id: str) -> dict[str, Any] | None:
    with connect() as db:
        report = db.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        if report is None:
            return None
        modules = db.execute(
            "SELECT * FROM module_tasks WHERE report_id=? ORDER BY rowid", (report_id,)
        ).fetchall()
    result = _decode(report)
    result["modules"] = [_decode(item) for item in modules]
    return result


def list_reports(limit: int = 40) -> list[dict[str, Any]]:
    with connect() as db:
        reports = db.execute("SELECT * FROM reports ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        output = []
        for report in reports:
            item = _decode(report)
            modules = db.execute(
                "SELECT module_id,module_version,status,analysis_status,review_status,stage,error,attempt,updated_at, "
                "CASE WHEN result IS NOT NULL AND result != '' AND result != '{}' THEN 1 ELSE 0 END AS result_available "
                "FROM module_tasks WHERE report_id=? ORDER BY rowid",
                (item["id"],),
            ).fetchall()
            item["modules"] = [dict(module) for module in modules]
            output.append(item)
    return output
