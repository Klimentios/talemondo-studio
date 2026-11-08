"""QC gate: the GPU queue stops when finished takes pile up without anybody looking at them.

Without it, a long batch can render take after take, every one later rejected, while the agent watches
infrastructure and probe counters instead of a single finished clip. production_gates.py stops after rejected
renders; this gate covers the other case: nobody reviewed at all.

A take counts as reviewed when it has a production review (production_review_record → production_reviews/)
or the user removed it from the feed (hidden). «Продолжить» in the queue (PUT /api/queue paused=false)
acknowledges everything finished so far. The limit lives in <runtime>/review_gate.json ({"limit": 3}; 0 = off).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

LIMIT_DEFAULT = 3


def _now() -> str:
    """SQLite CURRENT_TIMESTAMP format (UTC), comparable with jobs.updated_at."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _path(store) -> Path:
    return Path(store.root) / "review_gate.json"


def _read(store) -> dict:
    try:
        return json.loads(_path(store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(store, gate: dict) -> None:
    _path(store).write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")


def unreviewed_takes(store) -> list[str]:
    gate = _read(store)
    if "ack_at" not in gate:  # first use: history before the gate is not debt
        gate["ack_at"] = _now()
        _write(store, gate)
    reviews = Path(store.root) / "production_reviews"
    with store.connect() as db:
        rows = db.execute("SELECT job_id FROM jobs WHERE state='completed' AND hidden=0 AND updated_at > ? "
                          "ORDER BY updated_at", (gate["ack_at"],)).fetchall()
    return [r[0] for r in rows if not (reviews / (hashlib.sha256(r[0].encode()).hexdigest() + ".json")).exists()]


def review_hold(store) -> dict | None:
    """None, or why the queue waits: {unreviewed, limit, job_ids, reason}."""
    limit = int(_read(store).get("limit", LIMIT_DEFAULT))
    if limit <= 0:
        return None
    pending = unreviewed_takes(store)
    if len(pending) < limit:
        return None
    return {"unreviewed": len(pending), "limit": limit, "job_ids": pending,
            "reason": f"{len(pending)} готовых ролика без проверки: посмотри их (production_review_record или "
                      "«Продолжить» после просмотра) — очередь стоит, чтобы не жечь GPU на брак"}


def acknowledge(store) -> None:
    """«Продолжить»: the finished takes so far were looked at."""
    _write(store, {**_read(store), "ack_at": _now()})
