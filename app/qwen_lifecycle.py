"""Keep the local Qwen planner off the GPU while ComfyUI renders H3."""
from __future__ import annotations

import fcntl
import asyncio
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from contextlib import contextmanager, asynccontextmanager


COMFY_URL = os.environ.get("H3_COMFY_URL", "http://127.0.0.1:18188").rstrip("/")
QWEN_URL = os.environ.get("H3_QWEN_URL", "http://127.0.0.1:8000").rstrip("/")
SERVICE = "h3-qwen-director"
LOCK_PATH = Path("/tmp/h3-qwen-lifecycle.lock")
PLAN_LOCK_PATH = Path("/tmp/h3-qwen-plan.lock")
IDLE_SECONDS = 10


def enabled() -> bool:
    return os.environ.get("H3_QWEN_AUTOMANAGE") == "1"


@contextmanager
def lifecycle_lock():
    with LOCK_PATH.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def planning_lock():
    """Keep an in-flight Director response ahead of a new audio GPU task."""
    with PLAN_LOCK_PATH.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _supervisor(*args: str) -> str:
    result = subprocess.run(["supervisorctl", *args], capture_output=True, text=True,
                            timeout=120, check=False)
    if result.returncode and not (args and args[0] == "status" and result.returncode == 3):
        raise RuntimeError(f"supervisorctl {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def service_running() -> bool:
    output = _supervisor("status", SERVICE)
    return len(output.split()) >= 2 and output.split()[1] == "RUNNING"


def queue_active() -> bool:
    with urllib.request.urlopen(f"{COMFY_URL}/queue", timeout=10) as response:
        queue = json.load(response)
    return bool(queue.get("queue_running") or queue.get("queue_pending"))


def _post(url: str, payload: dict | None = None) -> None:
    request = urllib.request.Request(url, data=json.dumps(payload or {}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"{url} returned HTTP {response.status}")


def _free_comfy_models() -> None:
    for _ in range(2):
        if queue_active():
            raise RuntimeError("ComfyUI became busy before Qwen restart")
        _post(f"{COMFY_URL}/free", {"unload_models": True, "free_memory": True})
        time.sleep(2)


def _wait_qwen_ready() -> None:
    # A 27B profile (H3_SCENARIST_PROFILE) loads ~31 GB and compiles on its first start.
    deadline = time.monotonic() + float(os.environ.get("H3_QWEN_START_TIMEOUT") or 300)
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{QWEN_URL}/is_sleeping", timeout=5) as response:
                result = json.load(response)
            if isinstance(result, bool) or isinstance(result, dict) and isinstance(result.get("is_sleeping"), bool):
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError("Qwen did not become ready after supervisor start")


def ensure_started() -> None:
    """Start Qwen for the next Director request, after ComfyUI has become idle."""
    if not enabled():
        return
    with lifecycle_lock():
        if queue_active():
            raise RuntimeError("ComfyUI is rendering; Qwen cannot start")
        if service_running():
            _wait_qwen_ready()
            return
        if queue_active():
            raise RuntimeError("ComfyUI is rendering; Qwen cannot start")
        _free_comfy_models()
        if queue_active():
            raise RuntimeError("ComfyUI became busy before Qwen start")
        _supervisor("start", SERVICE)
        _wait_qwen_ready()
        from .scenarist_profile import sleep_level
        _post(f"{QWEN_URL}/sleep?level={sleep_level()}")


@contextmanager
def render_submission():
    """Stop Qwen before a graph can enter ComfyUI's queue.

    Hold the same lock as the idle watcher until queue submission finishes, so
    the watcher cannot restart Qwen in the gap between stop and submission.
    """
    with planning_lock():
        with lifecycle_lock():
            if enabled() and service_running():
                _supervisor("stop", SERVICE)
            yield


class GpuBusy(RuntimeError):
    """ComfyUI is rendering; a GPU task outside ComfyUI must not start beside it."""


@contextmanager
def exclusive_gpu_task():
    """Own the GPU for a model that runs outside ComfyUI (LivePortrait…): one model at a time.

    Same locks as a render submission (no H3 graph and no Qwen wake can start meanwhile), refuses
    while ComfyUI renders, then unloads the finished H3 weights before the task loads its own.
    """
    with render_submission():
        if queue_active():
            raise GpuBusy("H3 сейчас рендерит; задача начнётся после очереди")
        _free_comfy_models()
        yield


_PENDING_RELEASES: set[asyncio.Future] = set()


async def _release_after_enter(guard, enter: asyncio.Future) -> None:
    try:
        await enter
    except BaseException:
        return  # __enter__ failed: nothing was acquired
    await asyncio.to_thread(guard.__exit__, None, None, None)


@asynccontextmanager
async def async_render_submission():
    """Hold the process lifecycle lock without blocking the API event loop.

    A second request may arrive while the first awaits ComfyUI. Waiting for
    flock on the event loop would prevent that first request from completing.
    """
    guard = render_submission()
    enter = asyncio.ensure_future(asyncio.to_thread(guard.__enter__))
    try:
        await asyncio.shield(enter)
    except asyncio.CancelledError:
        # The worker thread keeps running and will still acquire the flocks.
        # Release them once it does, or every later render waits forever.
        release = asyncio.ensure_future(_release_after_enter(guard, enter))
        _PENDING_RELEASES.add(release)
        release.add_done_callback(_PENDING_RELEASES.discard)
        raise
    try:
        yield
    finally:
        await asyncio.to_thread(guard.__exit__, None, None, None)


def reconcile_once(idle_since: float | None) -> float | None:
    """One watcher pass; returns the start of the current idle interval."""
    if queue_active():
        with lifecycle_lock():
            if service_running():
                _supervisor("stop", SERVICE)
        return None
    now = time.monotonic()
    if idle_since is None:
        return now
    if now - idle_since < IDLE_SECONDS:
        return idle_since
    ensure_started()
    return idle_since


def main() -> None:
    if not enabled():
        raise SystemExit("H3_QWEN_AUTOMANAGE=1 is required")
    idle_since = None
    while True:
        try:
            idle_since = reconcile_once(idle_since)
        except Exception as exc:
            print(f"Qwen lifecycle: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
