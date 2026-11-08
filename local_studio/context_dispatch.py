"""Subscription planning has its own worker; it must not occupy a GPU dispatch turn."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import time

log = logging.getLogger("h3.context_dispatch")


async def context_dispatch_loop(queue) -> None:
    # Multiple Studio processes can share a data folder. Only one may start an
    # agent for a persisted context request; ambiguous work is reconciled first.
    path = queue.studio.root / "context-dispatch.lock"
    with path.open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(5)
        last_reconcile = last_error = float("-inf")
        while True:
            try:
                if time.monotonic() - last_reconcile > 15:
                    await queue.reconcile()
                    last_reconcile = time.monotonic()
                await queue.run_once()
            except Exception:
                if time.monotonic() - last_error > 30:
                    log.exception("Подготовка контекста через агента")
                    last_error = time.monotonic()
            await asyncio.sleep(1)
