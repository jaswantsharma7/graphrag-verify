"""Durable document, job, and query metadata."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


HISTORY_LIMIT = 100
INTERRUPTED_ERROR = "Ingestion was interrupted. Retry this document to index it again."


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_document(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "name", "status", "created_at", "chunks", "entities",
        "relationships", "error", "job_id",
    )
    return {key: row[key] for key in keys}


def public_job(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "document_id", "status", "stage", "progress", "error",
        "created_at", "updated_at",
    )
    return {key: row[key] for key in keys}


class RepositoryConflict(Exception):
    """A concurrent change makes a metadata operation invalid."""


class Repository:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "metadata.sqlite3"
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','processing','ready','failed')),
                    created_at TEXT NOT NULL,
                    chunks INTEGER NOT NULL DEFAULT 0,
                    entities INTEGER NOT NULL DEFAULT 0,
                    relationships INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    job_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    storage_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('queued','processing','completed','failed')),
                    stage TEXT NOT NULL,
                    progress REAL NOT NULL CHECK(progress >= 0 AND progress <= 1),
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS query_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    result TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connection(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_documents(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC, id DESC"
            ).fetchall()
            return [public_document(row) for row in rows]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        record = self.get_document_record(document_id)
        return public_document(record) if record else None

    def get_document_record(self, document_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return public_job(row) if row else None

    def create_document(
        self, name: str, content_hash: str, storage_name: str
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        with self._connection(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM documents WHERE content_hash = ?", (content_hash,)
            ).fetchone()
            if existing:
                job = connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (existing["job_id"],)
                ).fetchone()
                return public_document(existing), public_job(job), False
            document_id, job_id, now = uuid4().hex, uuid4().hex, timestamp()
            connection.execute(
                "INSERT INTO documents "
                "(id, name, status, created_at, job_id, content_hash, storage_name) "
                "VALUES (?, ?, 'queued', ?, ?, ?, ?)",
                (document_id, name, now, job_id, content_hash, storage_name),
            )
            connection.execute(
                "INSERT INTO jobs "
                "(id, document_id, status, stage, progress, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'queued', 0, ?, ?)",
                (job_id, document_id, now, now),
            )
            document = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return public_document(document), public_job(job), True

    def claim_next_job(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        # Claims are atomic; the pipeline and its locks still require one Uvicorn worker.
        with self._connection(immediate=True) as connection:
            job = connection.execute(
                "SELECT jobs.* FROM jobs JOIN documents ON documents.id = jobs.document_id "
                "WHERE jobs.status = 'queued' AND documents.status = 'queued' "
                "AND documents.job_id = jobs.id ORDER BY jobs.created_at, jobs.id LIMIT 1"
            ).fetchone()
            if not job:
                return None
            now = timestamp()
            connection.execute(
                "UPDATE jobs SET status = 'processing', stage = 'parsing', "
                "progress = 0.1, updated_at = ? WHERE id = ? AND status = 'queued'",
                (now, job["id"]),
            )
            connection.execute(
                "UPDATE documents SET status = 'processing', error = NULL WHERE id = ?",
                (job["document_id"],),
            )
            document = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (job["document_id"],)
            ).fetchone()
            claimed = connection.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
            return dict(document), public_job(claimed)

    def update_job(self, job_id: str, stage: str, progress: float) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE jobs SET stage = ?, progress = ?, updated_at = ? "
                "WHERE id = ? AND status = 'processing'",
                (stage, progress, timestamp(), job_id),
            )

    def complete_job(self, job_id: str, counts: dict[str, int]) -> None:
        with self._connection(immediate=True) as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job or job["status"] != "processing":
                raise RepositoryConflict("The ingestion job is no longer active.")
            connection.execute(
                "UPDATE documents SET status = 'ready', chunks = ?, entities = ?, "
                "relationships = ?, error = NULL WHERE id = ? AND job_id = ?",
                (counts["chunks"], counts["entities"], counts["relationships"],
                 job["document_id"], job_id),
            )
            connection.execute(
                "UPDATE jobs SET status = 'completed', stage = 'completed', progress = 1, "
                "error = NULL, updated_at = ? WHERE id = ?",
                (timestamp(), job_id),
            )

    def fail_job(self, job_id: str, error: str) -> None:
        with self._connection(immediate=True) as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job or job["status"] == "completed":
                return
            connection.execute(
                "UPDATE jobs SET status = 'failed', stage = 'failed', error = ?, "
                "updated_at = ? WHERE id = ?",
                (error, timestamp(), job_id),
            )
            connection.execute(
                "UPDATE documents SET status = 'failed', chunks = 0, entities = 0, "
                "relationships = 0, error = ? WHERE id = ? AND job_id = ?",
                (error, job["document_id"], job_id),
            )

    def recover_interrupted_jobs(self) -> list[str]:
        with self._connection(immediate=True) as connection:
            rows = connection.execute(
                "SELECT DISTINCT documents.id FROM documents LEFT JOIN jobs "
                "ON jobs.id = documents.job_id WHERE documents.status = 'processing' "
                "OR jobs.status = 'processing'"
            ).fetchall()
            connection.execute(
                "UPDATE jobs SET status = 'failed', stage = 'failed', error = ?, "
                "updated_at = ? WHERE status = 'processing'",
                (INTERRUPTED_ERROR, timestamp()),
            )
            for row in rows:
                connection.execute(
                    "UPDATE documents SET status = 'failed', chunks = 0, entities = 0, "
                    "relationships = 0, error = ? WHERE id = ?",
                    (INTERRUPTED_ERROR, row["id"]),
                )
            return [row["id"] for row in rows]

    def retry_document(self, document_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._connection(immediate=True) as connection:
            document = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not document:
                raise KeyError(document_id)
            if document["status"] != "failed":
                raise RepositoryConflict("Only failed documents can be retried.")
            job_id, now = uuid4().hex, timestamp()
            connection.execute(
                "INSERT INTO jobs "
                "(id, document_id, status, stage, progress, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'queued', 0, ?, ?)",
                (job_id, document_id, now, now),
            )
            connection.execute(
                "UPDATE documents SET status = 'queued', chunks = 0, entities = 0, "
                "relationships = 0, error = NULL, job_id = ? WHERE id = ?",
                (job_id, document_id),
            )
            updated = connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return public_document(updated), public_job(job)

    def delete_document(self, document_id: str) -> None:
        with self._connection(immediate=True) as connection:
            document = connection.execute(
                "SELECT status FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if not document:
                raise KeyError(document_id)
            if document["status"] in ("queued", "processing"):
                raise RepositoryConflict("Wait for active ingestion to finish before deleting.")
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    def fail_document_removal(self, document_id: str) -> None:
        error = "Document removal did not finish. Try deleting this document again."
        with self._connection(immediate=True) as connection:
            connection.execute(
                "UPDATE documents SET status = 'failed', chunks = 0, entities = 0, "
                "relationships = 0, error = ? WHERE id = ?",
                (error, document_id),
            )
            connection.execute(
                "UPDATE jobs SET status = 'failed', stage = 'failed', error = ?, updated_at = ? "
                "WHERE id = (SELECT job_id FROM documents WHERE id = ?)",
                (error, timestamp(), document_id),
            )

    def counts(self) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS documents, "
                "COALESCE(SUM(CASE WHEN status = 'ready' THEN chunks ELSE 0 END), 0) AS chunks, "
                "COALESCE(SUM(CASE WHEN status = 'ready' THEN entities ELSE 0 END), 0) AS entities, "
                "COALESCE(SUM(CASE WHEN status = 'ready' THEN relationships ELSE 0 END), 0) "
                "AS relationships FROM documents"
            ).fetchone()
            return {key: int(row[key]) for key in row.keys()}

    def save_query(self, result: dict[str, Any]) -> None:
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
        with self._connection(immediate=True) as connection:
            connection.execute(
                "INSERT INTO query_history (result, created_at) VALUES (?, ?)",
                (serialized, timestamp()),
            )
            connection.execute(
                "DELETE FROM query_history WHERE sequence NOT IN "
                "(SELECT sequence FROM query_history ORDER BY sequence DESC LIMIT ?)",
                (HISTORY_LIMIT,),
            )

    def list_queries(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT result FROM query_history ORDER BY sequence DESC LIMIT ?",
                (max(1, min(limit, HISTORY_LIMIT)),),
            ).fetchall()
            return [json.loads(row["result"]) for row in rows]
