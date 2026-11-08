"""Durable, atomic revision and event guard for an existing SQLite state owner.

The state owner supplies its revision reader, mutation and domain validation.
Both callbacks run on one connection inside the transaction; an error rolls back.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Collection
from contextlib import closing

from local_studio.agents.contracts import AgentTaskRequest, validate_agent_contract


class StateConflict(RuntimeError):
    pass


class EventConflict(RuntimeError):
    pass


class UnauthorizedAgent(RuntimeError):
    pass


def _fingerprint(request: AgentTaskRequest) -> str:
    body = {"agent_role": request.agent_role, "project_id": request.project_id,
            "shot_id": request.shot_id, "base_revision": request.base_revision,
            "action": request.action, "patch": request.patch}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class StateTransactionManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with closing(sqlite3.connect(db_path)) as db:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS agent_event_receipts ("
                           "event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                           "project_id TEXT NOT NULL, shot_id TEXT NOT NULL, revision INTEGER NOT NULL)")

    def apply(self, request: AgentTaskRequest, *, allowed_roles: Collection[str],
              read_revision: Callable[[sqlite3.Connection, AgentTaskRequest], int],
              mutate: Callable[[sqlite3.Connection, AgentTaskRequest], int]) -> tuple[int, bool]:
        """Return (revision, replayed). Never mark an event without applying its state."""
        errors = validate_agent_contract(request)
        if errors or request.action != "update_shot":
            raise ValueError("; ".join(errors) if errors else "Only mutations can be applied")
        if request.agent_role not in allowed_roles:
            raise UnauthorizedAgent(request.agent_role)
        fingerprint = _fingerprint(request)
        with closing(sqlite3.connect(self.db_path, timeout=30)) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                receipt = db.execute("SELECT fingerprint, revision FROM agent_event_receipts WHERE event_id=?",
                                     (request.event_id,)).fetchone()
                if receipt:
                    if receipt[0] != fingerprint:
                        raise EventConflict("Event ID reused with different content")
                    return receipt[1], True
                current = read_revision(db, request)
                if current != request.base_revision:
                    raise StateConflict(f"Expected revision {request.base_revision}; current {current}")
                revision = mutate(db, request)
                if revision != current + 1:
                    raise ValueError("State owner must advance its revision exactly once")
                db.execute("INSERT INTO agent_event_receipts(event_id,fingerprint,project_id,shot_id,revision) "
                           "VALUES (?,?,?,?,?)", (request.event_id, fingerprint, request.project_id,
                                               request.shot_id, revision))
                return revision, False
