"""Shared queue rules of the Mac side jobs (upscale, audio, keyframe) — same safety as local_studio/worker.py.

* An ambiguous submit is parked in ``needs_reconciliation`` and never resent. It does not take the
  dispatcher's FIFO turn: ``first_active()`` skips it, ``reconcile()`` checks it in the background by
  client ID and closes it as ``failed`` after ``RECONCILE_EXPIRY_S``.
* ``cancel()`` is a guarded UPDATE (no dispatch lock): the dispatcher never overwrites ``cancelled``.
* A lost remote job (``unknown``/``lost`` or a 404 from a new server) fails instead of polling forever.
* A download that keeps failing fails after ``DOWNLOAD_FAILURES_LIMIT`` tries, or after
  ``DOWNLOAD_OUTAGE_LIMIT_S`` without a connection (the result stays on the GPU meanwhile).
"""
from __future__ import annotations

from datetime import datetime, timezone
import time

import httpx

from .worker import DOWNLOAD_OUTAGE_LIMIT_S, UNKNOWN_POLLS_LIMIT, is_connection_error


RECONCILE_EXPIRY_S = 7200
DOWNLOAD_FAILURES_LIMIT = 5
# needs_reconciliation is deliberately absent: it must not block later jobs (see reconcile()).
DISPATCH_STATES = ("queued_local", "uploading", "queued_gpu", "running", "downloading")
ACTIVE = frozenset(DISPATCH_STATES) | {"needs_reconciliation"}
TERMINAL = frozenset({"completed", "failed", "cancelled"})
# GPU answers that prove a submit was refused before its receipt was written.
REFUSED_BEFORE_QUEUE = frozenset({413, 422, 503})


def age_seconds(stamp: str | None) -> float | None:
    """Age of an SQLite CURRENT_TIMESTAMP (UTC, 'YYYY-MM-DD HH:MM:SS')."""
    try:
        moment = datetime.fromisoformat(str(stamp).replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


def response_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = None
    return str(detail or f"GPU HTTP {response.status_code}")[:600]


def is_not_sent(exc: BaseException) -> bool:
    """No TCP connection was made, so nothing reached the GPU: safe to keep the job queued."""
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


class SideQueueRules:
    """Mixin for UpscaleQueue / AudioQueue / MotionQueue. Subclasses set TABLE, ID, NOUN and _lookup()."""

    TABLE = ""
    ID = ""
    NOUN = "задание"

    def _init_rules(self) -> None:
        self._download_failures: dict[str, int] = {}
        self._download_outage_since: dict[str, float] = {}
        self._lost_polls: dict[str, int] = {}

    # --- selection -------------------------------------------------------------------------
    def first_active(self) -> dict | None:
        """Oldest job that may take a dispatcher pass (never one parked for reconciliation)."""
        marks = ",".join("?" * len(DISPATCH_STATES))
        with self.store.connect() as db:
            row = db.execute(f"SELECT * FROM {self.TABLE} WHERE state IN ({marks}) "
                             "ORDER BY created_at, rowid LIMIT 1", DISPATCH_STATES).fetchone()
            return dict(row) if row else None

    def reconciling(self) -> list[dict]:
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                f"SELECT * FROM {self.TABLE} WHERE state='needs_reconciliation' ORDER BY created_at, rowid")]

    def _row(self, uid: str) -> dict | None:
        with self.store.connect() as db:
            row = db.execute(f"SELECT * FROM {self.TABLE} WHERE {self.ID}=?", (uid,)).fetchone()
            return dict(row) if row else None

    # --- state changes ---------------------------------------------------------------------
    def park(self, uid: str, error: str) -> None:
        """needs_reconciliation; entering it again keeps updated_at, so the 2 h expiry really runs."""
        with self.store.connect() as db:
            db.execute(f"UPDATE {self.TABLE} SET state='needs_reconciliation', error=?, "
                       f"updated_at=CURRENT_TIMESTAMP WHERE {self.ID}=? "
                       "AND state NOT IN ('needs_reconciliation','completed','failed','cancelled')", (error, uid))

    def cancel(self, uid: str) -> dict:
        """Stop tracking a side job in any active state. There is no GPU-side cancel for side jobs:
        a job already on the GPU may still finish there, its result is ignored."""
        marks = ",".join("?" * len(ACTIVE))
        with self.store.connect() as db:
            changed = db.execute(
                f"UPDATE {self.TABLE} SET state='cancelled', error=?, updated_at=CURRENT_TIMESTAMP "
                f"WHERE {self.ID}=? AND state IN ({marks})",
                ("Отменено пользователем", uid, *sorted(ACTIVE))).rowcount
        row = self._row(uid)
        if row is None:
            raise LookupError(f"{self.NOUN.capitalize()} не найдено")
        if not changed:
            raise ValueError(f"{self.NOUN.capitalize()} уже завершено — отменять нечего")
        self._forget(uid)
        return row

    def _forget(self, uid: str) -> None:
        self._download_failures.pop(uid, None)
        self._download_outage_since.pop(uid, None)
        self._lost_polls.pop(uid, None)

    # --- background reconciliation ---------------------------------------------------------
    async def reconcile(self) -> bool:
        """Resolve parked jobs by client ID without resending them; expire them after 2 h."""
        changed = False
        reachable = True
        for job in self.reconciling():
            uid = job[self.ID]
            if reachable:
                try:
                    receipt = await self._lookup(uid)
                except Exception:
                    reachable = False  # GPU offline: still apply the expiry below
                else:
                    if receipt and receipt.get("job_id") and receipt.get("status") != "needs_reconciliation":
                        self.update(uid, "queued_gpu", remote_job_id=receipt["job_id"], error="")
                        changed = True
                        continue
            age = age_seconds(job.get("updated_at"))
            if age is not None and age >= RECONCILE_EXPIRY_S:
                self.update(uid, "failed", error=(
                    f"За {RECONCILE_EXPIRY_S // 3600} ч не удалось подтвердить, что GPU принял {self.NOUN}; "
                    "автоповтор отключён — запусти заново"))
                changed = True
        return changed

    # --- polling and download outcomes -----------------------------------------------------
    def lost_on_gpu(self, uid: str, remote: dict | None) -> str | None:
        """Error text when the GPU no longer knows the job (remote=None means HTTP 404)."""
        if remote is not None and remote.get("status") != "unknown":
            self._lost_polls.pop(uid, None)
            return None
        polls = self._lost_polls.get(uid, 0) + 1
        self._lost_polls[uid] = polls
        if (remote is not None and remote.get("lost") is True) or polls >= UNKNOWN_POLLS_LIMIT:
            self._lost_polls.pop(uid, None)
            if remote is not None and remote.get("error"):
                return str(remote["error"])
            return (f"GPU не знает это {self.NOUN} (перезапуск или новый сервер); "
                    "результат потерян, автоповтор отключён")
        return None

    def download_failed(self, uid: str, exc: BaseException) -> str | None:
        """Error text once a download should stop retrying; None means try again next pass."""
        if is_connection_error(exc):
            since = self._download_outage_since.setdefault(uid, time.monotonic())
            if time.monotonic() - since >= DOWNLOAD_OUTAGE_LIMIT_S:
                self._forget(uid)
                return f"GPU недоступен {DOWNLOAD_OUTAGE_LIMIT_S // 60} мин при скачивании: {exc}"
            return None
        self._download_outage_since.pop(uid, None)
        failures = self._download_failures.get(uid, 0) + 1
        self._download_failures[uid] = failures
        if failures >= DOWNLOAD_FAILURES_LIMIT:
            self._forget(uid)
            return f"Скачивание результата не удалось {failures} раз подряд: {exc}"
        return None


# Queue page (GET /api/side-jobs): the side jobs share the GPU and the dispatcher with H3, so an
# H3 job waits behind them; the page showed «0 в работе» while an upscale or an edit held the GPU.
SIDE_KINDS = {
    "motion": ("motion_id", "Сцена Motion Director", "/?view=scenes"),
    "upscale": ("upscale_id", "Улучшение ролика", "/#jobQueue"),
    "audio": ("audio_id", "Звук и речь", "/audio-studio.html"),
    "workflow": ("job_id", "Свой воркфлоу", "/workflows.html"),
}


_HIDDEN_TABLE = ("CREATE TABLE IF NOT EXISTS side_job_hidden (kind TEXT NOT NULL, id TEXT NOT NULL, "
                 "hidden_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (kind, id))")


def hidden_side_jobs(store) -> set[tuple[str, str]]:
    with store.connect() as db:
        db.execute(_HIDDEN_TABLE)
        return {(row[0], row[1]) for row in db.execute("SELECT kind, id FROM side_job_hidden")}


def hide_side_jobs(store, queues: dict, only: tuple[str, str] | None = None) -> int:
    """Hide finished side jobs from the queue page («Убрать» / «Удалить всё»). Only the card goes:
    the job row, its files and results stay. A job still in the queue or on the GPU is never hidden."""
    rows = []
    for kind, queue in queues.items():
        id_key = SIDE_KINDS[kind][0]
        rows += [(kind, item[id_key], item["state"]) for item in queue.list()]
    if only is not None:
        match = [row for row in rows if row[:2] == only]
        if not match:
            raise LookupError("Задание не найдено")
        if match[0][2] not in TERMINAL:
            raise ValueError("Задание ещё в работе: сначала дождись конца или отмени его")
        rows = match
    targets = [(kind, uid) for kind, uid, state in rows if state in TERMINAL]
    with store.connect() as db:
        db.execute(_HIDDEN_TABLE)
        db.executemany("INSERT OR IGNORE INTO side_job_hidden(kind, id) VALUES (?, ?)", targets)
    return len(targets)


def side_job_summary(queues: dict, recent: int = 6, hidden: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """Every active side job plus the ``recent`` newest finished ones that are not hidden, newest first."""
    rows = []
    for kind, queue in queues.items():
        id_key, title, href = SIDE_KINDS[kind]
        for item in queue.list():
            if (kind, item[id_key]) in hidden and item["state"] not in ACTIVE:
                continue
            rows.append({"kind": kind, "id": item[id_key], "title": title, "href": href,
                         "state": item["state"], "error": (item.get("error") or "").strip() or None,
                         "source_job_id": item.get("source_job_id"), "mode": item.get("mode"),
                         "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
                         **({"scene_id": item["scene_id"], "revision": item["revision"],
                             "file_url": f"/api/studio/motion-director/jobs/{item[id_key]}/file" if item["state"] == "completed" else None}
                            if kind == "motion" else {})})
    rows.sort(key=lambda row: (row["created_at"] or "", row["id"]), reverse=True)
    active = [row for row in rows if row["state"] in ACTIVE]
    return active + [row for row in rows if row["state"] not in ACTIVE][:recent]
