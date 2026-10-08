"""Persistent storage for two-document analyst-report reviews."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.database import APP_DATA, UPLOAD_DIR

DB_PATH = APP_DATA / "runs.sqlite3"
REVIEW_UPLOAD_DIR = UPLOAD_DIR / "reviews"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    return db


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def init_review_db() -> None:
    REVIEW_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS review_runs (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL,
              metadata TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              error TEXT, analyst_page_count INTEGER NOT NULL DEFAULT 0,
              financial_page_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS review_documents (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, role TEXT NOT NULL,
              file_name TEXT NOT NULL, file_path TEXT NOT NULL, sha256 TEXT NOT NULL,
              page_count INTEGER NOT NULL DEFAULT 0, readable_pages INTEGER NOT NULL DEFAULT 0,
              extraction_status TEXT NOT NULL DEFAULT 'pending',
              FOREIGN KEY(run_id) REFERENCES review_runs(id)
            );
            CREATE INDEX IF NOT EXISTS idx_review_documents_run ON review_documents(run_id, role);
            CREATE TABLE IF NOT EXISTS review_pages (
              document_id TEXT NOT NULL, page_number INTEGER NOT NULL, text TEXT NOT NULL,
              PRIMARY KEY(document_id, page_number),
              FOREIGN KEY(document_id) REFERENCES review_documents(id)
            );
            CREATE TABLE IF NOT EXISTS review_chunks (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, document_id TEXT NOT NULL,
              chunk_index INTEGER NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
              prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
              UNIQUE(document_id, chunk_index)
            );
            CREATE TABLE IF NOT EXISTS review_claims (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, source_document_id TEXT NOT NULL,
              source_page INTEGER NOT NULL, source_quote TEXT NOT NULL, claim_text TEXT NOT NULL,
              category TEXT NOT NULL, metric TEXT NOT NULL DEFAULT '', period TEXT NOT NULL DEFAULT '',
              value TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '', claim_type TEXT NOT NULL DEFAULT 'historical_fact',
              extraction_verified INTEGER NOT NULL DEFAULT 0, processing_status TEXT NOT NULL DEFAULT 'pending',
              outcome TEXT, rationale TEXT NOT NULL DEFAULT '', calculation TEXT NOT NULL DEFAULT '',
              suggestion TEXT NOT NULL DEFAULT '', error TEXT, trace TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, claim_key TEXT NOT NULL,
              UNIQUE(run_id, claim_key)
            );
            CREATE INDEX IF NOT EXISTS idx_review_claims_run ON review_claims(run_id, source_page);
            CREATE TABLE IF NOT EXISTS review_evidence (
              id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL,
              document_id TEXT NOT NULL, page_number INTEGER NOT NULL, quote TEXT NOT NULL,
              quote_verified INTEGER NOT NULL DEFAULT 0, evidence_role TEXT NOT NULL,
              UNIQUE(claim_id, document_id, page_number, quote)
            );
            CREATE TABLE IF NOT EXISTS review_questions (
              id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, question TEXT NOT NULL,
              answer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              token_usage TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        question_columns = {row["name"] for row in db.execute("PRAGMA table_info(review_questions)").fetchall()}
        if "token_usage" not in question_columns:
            db.execute("ALTER TABLE review_questions ADD COLUMN token_usage TEXT NOT NULL DEFAULT '{}' ")
        db.execute(
            "UPDATE review_runs SET status='failed', stage='服务重启', error='服务在处理期间重启；已保存的核查项仍可查看并继续。', updated_at=? WHERE status IN ('queued','running')",
            (_now(),),
        )
        db.execute(
            "UPDATE review_claims SET processing_status='pending', error='服务重启后可继续处理。', updated_at=? WHERE processing_status='running'",
            (_now(),),
        )


def create_review(run: dict[str, Any], documents: list[dict[str, Any]]) -> None:
    with _connect() as db:
        db.execute(
            "INSERT INTO review_runs (id,status,stage,metadata,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (run["id"], run["status"], run["stage"], _json(run["metadata"]), run["created_at"], run["updated_at"]),
        )
        db.executemany(
            "INSERT INTO review_documents (id,run_id,role,file_name,file_path,sha256) VALUES (?,?,?,?,?,?)",
            [(d["id"], run["id"], d["role"], d["file_name"], d["file_path"], d["sha256"]) for d in documents],
        )


def _decode(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for key in json_fields:
        value[key] = json.loads(value.get(key) or "{}")
    return value


def get_review(run_id: str) -> dict[str, Any] | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM review_runs WHERE id=?", (run_id,)).fetchone()
    return _decode(row, ("metadata",))


def list_reviews(limit: int = 30) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM review_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_decode(row, ("metadata",)) or {} for row in rows]


def get_document(document_id: str) -> dict[str, Any] | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM review_documents WHERE id=?", (document_id,)).fetchone()
    return _decode(row)


def get_documents(run_id: str) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM review_documents WHERE run_id=? ORDER BY role", (run_id,)).fetchall()
    return [_decode(row) or {} for row in rows]


def update_review(run_id: str, **fields: Any) -> None:
    allowed = {"status", "stage", "error", "analyst_page_count", "financial_page_count"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _now()
    sql = ", ".join(f"{key}=?" for key in values)
    with _connect() as db:
        db.execute(f"UPDATE review_runs SET {sql} WHERE id=?", (*values.values(), run_id))


def save_pages(document_id: str, pages: list[dict[str, Any]]) -> None:
    with _connect() as db:
        db.executemany(
            "INSERT OR REPLACE INTO review_pages(document_id,page_number,text) VALUES(?,?,?)",
            [(document_id, int(page["page"]), str(page["text"])) for page in pages],
        )
        db.execute(
            "UPDATE review_documents SET page_count=?,readable_pages=?,extraction_status='completed' WHERE id=?",
            (len(pages), sum(bool(str(page["text"]).strip()) for page in pages), document_id),
        )


def get_page(document_id: str, page_number: int) -> str | None:
    with _connect() as db:
        row = db.execute("SELECT text FROM review_pages WHERE document_id=? AND page_number=?", (document_id, page_number)).fetchone()
    return str(row["text"]) if row else None


def search_pages(document_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
    from backend.pdf_reader import rank_search_pages

    with _connect() as db:
        rows = db.execute("SELECT page_number,text FROM review_pages WHERE document_id=?", (document_id,)).fetchall()
    pages = [{"page": int(row["page_number"]), "text": str(row["text"])} for row in rows]
    return rank_search_pages(pages, query, limit=limit, excerpt_chars=2800)


def list_chunks(document_id: str) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM review_chunks WHERE document_id=? ORDER BY chunk_index", (document_id,)).fetchall()
    return [dict(row) for row in rows]


def save_chunk(chunk: dict[str, Any]) -> None:
    with _connect() as db:
        db.execute(
            "INSERT INTO review_chunks(id,run_id,document_id,chunk_index,status,detail,prompt_tokens,completion_tokens) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(document_id,chunk_index) DO UPDATE SET status=excluded.status,detail=excluded.detail,prompt_tokens=excluded.prompt_tokens,completion_tokens=excluded.completion_tokens",
            (chunk["id"], chunk["run_id"], chunk["document_id"], chunk["chunk_index"], chunk["status"], chunk.get("detail", ""), chunk.get("prompt_tokens", 0), chunk.get("completion_tokens", 0)),
        )


def save_claim(run_id: str, document_id: str, claim: dict[str, Any]) -> str:
    page = int(claim["source_page"])
    quote = str(claim["source_quote"]).strip()
    normalized = "".join(quote.casefold().split())
    key = hashlib.sha256(f"{page}|{normalized}|{claim.get('claim_text','')}".encode("utf-8")).hexdigest()
    claim_id = hashlib.sha256(f"{run_id}|{key}".encode("utf-8")).hexdigest()[:32]
    now = _now()
    with _connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO review_claims(id,run_id,source_document_id,source_page,source_quote,claim_text,category,metric,period,value,unit,claim_type,extraction_verified,created_at,updated_at,claim_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, run_id, document_id, page, quote, str(claim.get("claim_text", ""))[:1200], str(claim.get("category", "financial_fact"))[:60], str(claim.get("metric", ""))[:100], str(claim.get("period", ""))[:80], str(claim.get("value", ""))[:100], str(claim.get("unit", ""))[:50], str(claim.get("claim_type", "historical_fact"))[:50], int(bool(claim.get("extraction_verified"))), now, now, key),
        )
    return claim_id


def list_claims(run_id: str) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM review_claims WHERE run_id=? ORDER BY source_page,created_at", (run_id,)).fetchall()
    claims = []
    for row in rows:
        item = dict(row)
        item["trace"] = json.loads(item.get("trace") or "[]")
        item["evidence"] = list_evidence(item["id"])
        claims.append(item)
    return claims


def get_claim(claim_id: str) -> dict[str, Any] | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM review_claims WHERE id=?", (claim_id,)).fetchone()
    item = dict(row) if row else None
    if item:
        item["trace"] = json.loads(item.get("trace") or "[]")
        item["evidence"] = list_evidence(claim_id)
    return item


def update_claim(claim_id: str, **fields: Any) -> None:
    allowed = {"processing_status", "outcome", "rationale", "calculation", "suggestion", "error", "trace"}
    values = {key: value for key, value in fields.items() if key in allowed}
    if "trace" in values:
        values["trace"] = _json(values["trace"])
    if not values:
        return
    values["updated_at"] = _now()
    sql = ", ".join(f"{key}=?" for key in values)
    with _connect() as db:
        db.execute(f"UPDATE review_claims SET {sql} WHERE id=?", (*values.values(), claim_id))


def save_evidence(claim_id: str, document_id: str, page_number: int, quote: str, verified: bool, role: str) -> None:
    with _connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO review_evidence(claim_id,document_id,page_number,quote,quote_verified,evidence_role) VALUES(?,?,?,?,?,?)",
            (claim_id, document_id, page_number, quote[:800], int(verified), role),
        )


def list_evidence(claim_id: str) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM review_evidence WHERE claim_id=? ORDER BY evidence_role,page_number", (claim_id,)).fetchall()
    return [dict(row) for row in rows]


def save_question(question: dict[str, Any]) -> None:
    now = _now()
    with _connect() as db:
        db.execute("INSERT INTO review_questions(id,claim_id,question,status,created_at,updated_at) VALUES(?,?,?,'queued',?,?)", (question["id"], question["claim_id"], question["question"], now, now))


def update_question(question_id: str, answer: str, status: str, token_usage: dict[str, int] | None = None) -> None:
    with _connect() as db:
        db.execute("UPDATE review_questions SET answer=?,status=?,updated_at=?,token_usage=? WHERE id=?", (answer, status, _now(), _json(token_usage or {}), question_id))


def list_questions(claim_id: str) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM review_questions WHERE claim_id=? ORDER BY created_at", (claim_id,)).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        item["token_usage"] = json.loads(item.get("token_usage") or "{}")
    return items


def retry_claim_ids(run_id: str) -> list[str]:
    with _connect() as db:
        rows = db.execute("SELECT id FROM review_claims WHERE run_id=? AND processing_status IN ('pending','failed') ORDER BY source_page", (run_id,)).fetchall()
    return [str(row["id"]) for row in rows]
