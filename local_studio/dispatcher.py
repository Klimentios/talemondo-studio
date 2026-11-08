"""The background dispatcher: one pass every few seconds picks the next queue (H3 or a side job)
and runs it; parked jobs are reconciled every 30 s. Only the process holding dispatcher.lock runs it."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import sys
import time
from datetime import datetime, timezone

from .store import StudioStore
from .worker import QueueWorker

SIDE_PRIORITY_STATES = frozenset({"uploading", "queued_gpu", "running", "downloading"})
# Background check of jobs parked in needs_reconciliation (H3 and side jobs).
RECONCILE_EVERY_S = 30.0
STALE_UPLOAD_S = 3600.0
# The model each queue loads on the GPU. Switching H3 (66 GB + its 51 GB encoder) for another model and back
# re-reads the weights from the Vast disk at ~200 MB/s: 10–12 min per switch (2026-11-02). Same-model jobs go together.
GPU_MODEL = {"h3": "h3", "motion": "h3", "upscale": "seedvr2", "audio": "audio", "workflow": "custom"}
# How long the oldest job of another model may wait behind the warm one; longer than one switch costs.
WARM_HOLD_S = 1200.0
_log = logging.getLogger("h3.studio")


def _age_s(created_at: object, now: float) -> float:
    """Seconds since created_at (SQLite CURRENT_TIMESTAMP, UTC, or epoch seconds); unknown = old."""
    if isinstance(created_at, (int, float)):
        return now - created_at
    try:
        stamp = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return now - stamp.timestamp()


def next_dispatch(*, h3_active: object, h3_pending: dict | None, upscale: dict | None,
                  audio: dict | None, paused: bool,
                  context_ir: dict | None = None, production_qc: dict | None = None,
                  motion: dict | None = None, warm: str | None = None, now: float | None = None,
                  workflow: dict | None = None) -> str | None:
    """Which queue gets this dispatcher pass. A side job already on the GPU is finished first;
    otherwise the oldest waiting job wins, and Pause holds every new side job (H3 obeys it itself).
    Only a job really on the GPU (or being uploaded) jumps the line: a job parked in
    needs_reconciliation is checked in the background and never blocks the others.
    `warm` is the model the GPU ran last (GPU_MODEL): a waiting job of that model goes first while the oldest job
    of another model has waited less than WARM_HOLD_S, so the weights are not swapped out and back."""
    for kind, item in (("motion", motion), ("workflow", workflow), ("production_qc", production_qc), ("audio", audio),
                       ("upscale", upscale), ("context_ir", context_ir)):
        if item and item["state"] in SIDE_PRIORITY_STATES:
            return kind
    upscale, audio = (item if item and item["state"] == "queued_local" else None for item in (upscale, audio))
    context_ir = context_ir if context_ir and context_ir["state"] == "queued_local" else None
    motion = motion if motion and motion["state"] == "queued_local" else None
    workflow = workflow if workflow and workflow["state"] == "queued_local" else None
    if h3_active:
        return "h3"
    candidates = [(item["created_at"], kind) for kind, item in
                  (("h3", h3_pending), ("upscale", upscale), ("audio", audio),
                   ("context_ir", context_ir), ("production_qc", production_qc), ("motion", motion),
                   ("workflow", workflow)) if item]
    next_kind = min(candidates)[1] if candidates else None
    if warm and not paused and next_kind and GPU_MODEL.get(next_kind) not in {None, warm}:
        same = [candidate for candidate in candidates if GPU_MODEL.get(candidate[1]) == warm]
        if same and _age_s(min(candidates)[0], time.time() if now is None else now) < WARM_HOLD_S:
            next_kind = min(same)[1]
    if next_kind not in {None, "h3"} and paused:
        return None
    return next_kind


class RateLimitedErrors:
    """logging.exception for dispatcher failures, at most once per minute per distinct error
    (a dead tunnel would otherwise write the same traceback every 3 s)."""

    WINDOW_S = 60.0

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def report(self, where: str) -> None:
        exc = sys.exc_info()[1]
        key = f"{where}:{type(exc).__name__}:{str(exc)[:200]}"
        now = time.monotonic()
        if now - self._last.get(key, -self.WINDOW_S) < self.WINDOW_S:
            self._suppressed[key] = self._suppressed.get(key, 0) + 1
            return
        self._last[key] = now
        repeated = self._suppressed.pop(key, 0)
        self.logger.exception("%s: ошибка%s", where, f" (ещё {repeated} раз за минуту)" if repeated else "")


def remove_stale_partials(studio: StudioStore) -> int:
    """Half-written files left by a crash (.part in results/ and assets/xx/, scene-frame temps, uploads).
    Called only by the process that holds dispatcher.lock, before its first pass, so nothing is
    writing them; a job in downloading simply downloads again."""
    removed = 0
    # upload-* are interrupted library uploads; only old ones, a new upload may be in flight right now.
    old_uploads = [path for path in studio.assets_dir.glob("upload-*")
                   if time.time() - path.stat().st_mtime > STALE_UPLOAD_S]
    candidates = [*studio.results_dir.glob(".*.part"), *studio.assets_dir.glob("*/.*.part"),
                  *studio.assets_dir.glob(".scene-frame-*.png"), *old_uploads]
    for path in candidates:
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


async def dispatch_loop(ctx) -> None:
    # The lock prevents uvicorn workers or accidental second launches from double dispatching.
    lock_path = ctx.data_root / "dispatcher.lock"
    with lock_path.open("a+b") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                # Another Studio process with this data folder dispatches; take over when it exits.
                _log.warning("Очередь обслуживает другой процесс Studio (%s занят); повтор через 30 с", lock_path)
                await asyncio.sleep(30)
        removed = remove_stale_partials(ctx.store)
        if removed:
            _log.warning("Удалены недокачанные файлы после прошлого запуска: %d", removed)
        worker = QueueWorker(ctx.store, ctx.gpu_transport, ctx.gpu_backoff, getattr(getattr(ctx, 'job_graphs', None), 'archive', None))
        runners = {"production_qc": ctx.production_queue.run_qc_once, "h3": worker.run_once,
                   "upscale": ctx.upscale_queue.run_once, "audio": ctx.audio_queue.run_once,
                   "motion": ctx.motion_queue.run_once, "workflow": ctx.workflow_queue.run_once}
        reconcilers = (worker.reconcile_once, ctx.upscale_queue.reconcile,
                       ctx.audio_queue.reconcile, ctx.motion_queue.reconcile,
                       ctx.workflow_queue.reconcile)
        errors = RateLimitedErrors(_log)

        async def stage_loop():
            while True:
                try:
                    # Direct Comfy execution must not race a new server-queue upload.
                    async with ctx.dispatch_lock:
                        direct = [ctx.motion_queue.first_active(), ctx.workflow_queue.first_active()]
                        if not any(job and job['state'] in SIDE_PRIORITY_STATES for job in direct):
                            await worker.stage_once()
                    await worker.stage_cloud_once()
                except Exception:
                    errors.report("Передача серверной очереди")
                await asyncio.sleep(1)
        staging_task = asyncio.create_task(stage_loop())
        try:
            await _dispatch_passes(ctx, runners, reconcilers, errors)
        finally:
            staging_task.cancel()
            await asyncio.gather(staging_task, return_exceptions=True)


async def _dispatch_passes(ctx, runners, reconcilers, errors):
    last_reconcile = 0.0
    last_video_sync = 0.0
    warm = None  # the model the GPU ran last; this process forgets it on restart (one plain FIFO pick)
    video_worker = QueueWorker(ctx.store, ctx.gpu_transport, ctx.gpu_backoff, getattr(getattr(ctx, 'job_graphs', None), 'archive', None))
    while True:
        try:
            async with ctx.dispatch_lock:
                if time.monotonic() - last_video_sync >= 15.0:
                    last_video_sync = time.monotonic()
                    await video_worker.sync_ready_videos_once()
                if time.monotonic() - last_reconcile >= RECONCILE_EVERY_S:
                    # Parked (ambiguous) jobs are checked here, outside the FIFO turn.
                    last_reconcile = time.monotonic()
                    for reconcile in reconcilers:
                        try:
                            await reconcile()
                        except Exception:
                            errors.report("Сверка заданий")
                await ctx.production_queue.advance()
                h3_active = ctx.store.active_job()
                kind = next_dispatch(
                    h3_active=h3_active, h3_pending=None if h3_active else ctx.store.oldest_pending(),
                    upscale=ctx.upscale_queue.first_active(), audio=ctx.audio_queue.first_active(),
                    paused=ctx.store.queue_paused(), context_ir=None,
                    production_qc=ctx.production_queue.first_qc(), motion=ctx.motion_queue.first_active(), warm=warm,
                    workflow=ctx.workflow_queue.first_active())
                if kind:
                    await runners[kind]()
                    warm = GPU_MODEL.get(kind, warm)
        except Exception:
            # The job state is persisted. Next pass attempts recovery/reconciliation.
            errors.report("Диспетчер очереди")
        await ctx.gpu_backoff.wait()
