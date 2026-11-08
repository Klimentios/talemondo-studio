from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path


class JobRegistry:
    """Crash-safe client UUID ledger. An ambiguous `accepted` is never requeued."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS client_jobs (
                client_job_id TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('accepted', 'queued')),
                remote_job_id TEXT,
                director_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(client_jobs)")}
            if "director_json" not in columns:
                db.execute("ALTER TABLE client_jobs ADD COLUMN director_json TEXT")
            if "planned_remote_id" not in columns:
                # ComfyUI prompt id chosen before the POST /prompt: an `accepted` row whose
                # API process died mid-submit can be matched against ComfyUI afterwards.
                db.execute("ALTER TABLE client_jobs ADD COLUMN planned_remote_id TEXT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def accept(self, client_job_id: str, request_hash: str, *, planned_remote_id: str | None = None,
               director_json: str | None = None) -> tuple[bool, dict]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM client_jobs WHERE client_job_id=?", (client_job_id,)).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ValueError("client_job_id уже использован с другим заданием")
                return False, dict(existing)
            db.execute("INSERT INTO client_jobs(client_job_id,request_hash,state,planned_remote_id,director_json)"
                       " VALUES (?,?,'accepted',?,?)",
                       (client_job_id, request_hash, planned_remote_id, director_json))
            return True, self.get(client_job_id, db=db)

    def queued(self, client_job_id: str, remote_job_id: str, director_json: str | None = None) -> None:
        with self._connect() as db:
            db.execute("UPDATE client_jobs SET state='queued', remote_job_id=?,"
                       " director_json=COALESCE(?, director_json) WHERE client_job_id=? AND state='accepted'",
                       (remote_job_id, director_json, client_job_id))
            if db.total_changes != 1:
                raise RuntimeError("Нельзя подтвердить запись remote_job_id")

    def get(self, client_job_id: str, *, db: sqlite3.Connection | None = None) -> dict | None:
        if db is not None:
            row = db.execute("SELECT * FROM client_jobs WHERE client_job_id=?", (client_job_id,)).fetchone()
            return dict(row) if row else None
        with self._connect() as connection:
            return self.get(client_job_id, db=connection)

    def owns_remote(self, remote_job_id: str) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM client_jobs WHERE remote_job_id=? AND state='queued'",
                              (remote_job_id,)).fetchone() is not None


# ComfyUI states that prove the planned prompt id reached its queue.
_KNOWN_TO_COMFY = {"queued", "running", "success", "error"}


async def recover_accepted(registry: JobRegistry, comfy: object, record: dict | None) -> dict | None:
    """Promote an `accepted` row whose submit was cut off (GPU API restart) when ComfyUI
    knows its planned prompt id. Unreachable ComfyUI or an unknown id leaves it untouched:
    an ambiguous receipt is never requeued."""
    if not record or record.get("state") != "accepted" or not record.get("planned_remote_id"):
        return record
    planned = record["planned_remote_id"]
    try:
        status = await comfy.status(planned)
    except Exception:
        return record
    if status.get("status") not in _KNOWN_TO_COMFY:
        return record
    try:
        registry.queued(record["client_job_id"], planned)
    except RuntimeError:
        pass  # promoted concurrently by the submit that is still finishing
    return registry.get(record["client_job_id"])
