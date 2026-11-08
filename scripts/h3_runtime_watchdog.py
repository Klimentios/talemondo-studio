#!/usr/bin/env python3
"""Conservative H3/Qwen watchdog for the four-port local Studio stack.

Rules (each one guards against a real way the stack was disturbed):
- A sleeping vLLM is healthy: /health 200, or /is_sleeping answering true/false.
- A service that Supervisor reports STOPPED was stopped on purpose (operator, or the
  h3-qwen-vram-guard freeing VRAM for a render) and is never started by the watchdog.
  STARTING/BACKOFF belong to Supervisor's own retry logic.
- Nothing is restarted while ComfyUI has a running/pending prompt, while a Director plan or
  render submission holds the GPU API's flock locks, or while ComfyUI's queue cannot be read.
- Fresh processes get a grace period (vLLM cold start takes minutes) and every restart is
  followed by a cooldown, so a broken service cannot be restart-looped by the watchdog.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path


COMFY = os.environ.get("H3_COMFY_URL", "http://127.0.0.1:18188").rstrip("/")
API = os.environ.get("H3_GPU_API_URL", "http://127.0.0.1:18189").rstrip("/")
QWEN = os.environ.get("H3_QWEN_URL", "http://127.0.0.1:8000").rstrip("/")
INTERVAL = max(10, int(os.environ.get("H3_WATCHDOG_INTERVAL", "15")))
# Matches the server that ran 2026-09-29: vLLM cold start can take minutes.
QWEN_FAILURE_LIMIT = max(3, int(os.environ.get("H3_QWEN_FAILURE_LIMIT", "12")))
API_FAILURE_LIMIT = max(3, int(os.environ.get("H3_API_FAILURE_LIMIT", "4")))
# No Qwen restart within this many seconds of the vLLM process start (model load / CUDA graphs).
STARTUP_GRACE_SECONDS = max(60, int(os.environ.get("H3_WATCHDOG_STARTUP_GRACE", "600")))
API_STARTUP_GRACE_SECONDS = 60
# After the watchdog restarted a service it waits this long before touching it again.
RESTART_COOLDOWN_SECONDS = max(60, int(os.environ.get("H3_WATCHDOG_RESTART_COOLDOWN", "900")))
# The GPU API holds these flocks while Qwen plans and while a graph is being queued
# (app/qwen_lifecycle.py planning_lock / lifecycle_lock).
BUSY_LOCKS = (Path("/tmp/h3-qwen-plan.lock"), Path("/tmp/h3-qwen-lifecycle.lock"))

API_SERVICE = "h3-api"
QWEN_SERVICE = "h3-qwen-director"


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z"), message, flush=True)


def request_json(url: str, *, payload: dict | None = None, timeout: int = 8) -> object:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise RuntimeError(f"HTTP {response.status} from {url}")
        raw = response.read()
        return json.loads(raw) if raw else {}


_UPTIME = re.compile(r"uptime (?:(\d+) days?, )?(\d+):(\d+):(\d+)")


def parse_supervisor_status(output: str) -> tuple[str, float | None]:
    """`supervisorctl status NAME` -> (STATE, uptime seconds or None)."""
    parts = output.split()
    state = parts[1] if len(parts) >= 2 else "UNKNOWN"
    match = _UPTIME.search(output)
    uptime = None
    if match:
        days, hours, minutes, seconds = (int(value or 0) for value in match.groups())
        uptime = float(((days * 24 + hours) * 60 + minutes) * 60 + seconds)
    return state, uptime


def supervisor_status(name: str) -> tuple[str, float | None]:
    # Exit code 3 is Supervisor's normal answer for a program that is not RUNNING.
    result = subprocess.run(["supervisorctl", "status", name], capture_output=True, text=True,
                            timeout=30, check=False)
    return parse_supervisor_status(result.stdout.strip())


def restart_service(name: str) -> None:
    subprocess.run(["supervisorctl", "restart", name], check=True, timeout=180,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def submission_busy() -> bool:
    """True while the GPU API plans with Qwen or queues a render (its flocks are held)."""
    for path in BUSY_LOCKS:
        try:
            with path.open("a+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            continue
    return False


def qwen_answer_healthy(health_ok: bool, sleeping: object) -> bool:
    """vLLM is healthy when /health answers, or when /is_sleeping answers either way."""
    if health_ok:
        return True
    if isinstance(sleeping, bool):
        return True
    return isinstance(sleeping, dict) and isinstance(sleeping.get("is_sleeping"), bool)


class Watchdog:
    def __init__(self, *, http: Callable[..., object] = request_json,
                 status: Callable[[str], tuple[str, float | None]] = supervisor_status,
                 restart: Callable[[str], None] = restart_service,
                 busy: Callable[[], bool] = submission_busy,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 logger: Callable[[str], None] = log) -> None:
        self.http, self.status, self.restart_cmd = http, status, restart
        self.busy, self.clock, self.sleep, self.log = busy, clock, sleep, logger
        self.idle_streak = 0
        self.cleaned_for_idle = False
        self.api_failures = 0
        self.qwen_failures = 0
        self.last_restart: dict[str, float] = {}
        self.legacy_api = False

    # -- probes ---------------------------------------------------------------------------
    def comfy_idle(self) -> bool | None:
        try:
            queue = self.http(f"{COMFY}/queue")
        except Exception as exc:
            self.log(f"ComfyUI queue check failed; no restart attempted: {exc}")
            return None
        if not isinstance(queue, dict):
            return None
        return not queue.get("queue_running") and not queue.get("queue_pending")

    def qwen_sleeping(self) -> object:
        try:
            return self.http(f"{QWEN}/is_sleeping")
        except Exception:
            return None

    def qwen_healthy(self) -> tuple[bool, str]:
        try:
            self.http(f"{QWEN}/health")
            return True, ""
        except Exception as exc:
            reason = str(exc)
        return qwen_answer_healthy(False, self.qwen_sleeping()), reason

    def api_healthy(self) -> tuple[bool, str]:
        self.legacy_api = False
        try:
            self.http(f"{API}/api/live")
            return True, ""
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return False, str(exc)
        except Exception as exc:
            return False, str(exc)
        # GPU API older than /api/live: its /api/health also proxies ComfyUI.
        self.legacy_api = True
        try:
            self.http(f"{API}/api/health")
            return True, ""
        except Exception as exc:
            return False, str(exc)

    # -- restart policy -------------------------------------------------------------------
    def cooling_down(self, name: str) -> bool:
        last = self.last_restart.get(name)
        return last is not None and self.clock() - last < RESTART_COOLDOWN_SECONDS

    def try_restart(self, name: str, why: str) -> bool:
        try:
            self.restart_cmd(name)
        except Exception as exc:
            self.log(f"{name} restart failed: {exc}")
            return False
        self.last_restart[name] = self.clock()
        self.log(f"restarted {name}: {why}")
        return True

    # -- one pass -------------------------------------------------------------------------
    def tick(self) -> None:
        idle = self.comfy_idle()
        busy = self.busy()
        # quiet: proven idle ComfyUI and no Director plan / submission in flight.
        quiet = idle is True and not busy

        if idle is True:
            self.idle_streak += 1
        elif idle is False:
            self.idle_streak = 0
            self.cleaned_for_idle = False
        if self.idle_streak >= 2 and quiet and not self.cleaned_for_idle:
            self.release_idle_vram()

        self.check_api(quiet)
        self.check_qwen(quiet)

    def release_idle_vram(self) -> None:
        sleeping = self.qwen_sleeping()
        if not (sleeping is True or isinstance(sleeping, dict) and sleeping.get("is_sleeping") is True):
            return
        try:
            for _ in range(2):
                self.http(f"{COMFY}/free", payload={"unload_models": True, "free_memory": True})
                self.sleep(2)
            self.cleaned_for_idle = True
            self.log("stable idle queue: released completed H3 models from VRAM")
        except Exception as exc:
            self.log(f"idle VRAM release failed: {exc}")

    def check_api(self, quiet: bool) -> None:
        healthy, reason = self.api_healthy()
        if healthy:
            self.api_failures = 0
            return
        state, uptime = self.status(API_SERVICE)
        if state != "RUNNING":
            # STOPPED: an operator stopped it. STARTING/BACKOFF/FATAL/EXITED: Supervisor
            # (autorestart=true) owns it; restarting from here would race its own retries.
            self.api_failures = 0
            self.log(f"GPU API unhealthy in Supervisor state {state}; watchdog leaves it to Supervisor")
            return
        if self.legacy_api and not self._comfy_answers():
            # Old API: /api/health fails with ComfyUI, which restarting h3-api cannot heal.
            self.api_failures = 0
            self.log(f"GPU API health failed because ComfyUI is unavailable; no API restart: {reason}")
            return
        self.api_failures += 1
        self.log(f"GPU API health failure {self.api_failures}/{API_FAILURE_LIMIT}: {reason}")
        if self.api_failures < API_FAILURE_LIMIT:
            return
        if uptime is not None and uptime < API_STARTUP_GRACE_SECONDS:
            return
        if not quiet:
            self.log("GPU API restart deferred: render or submission in progress")
            return
        if self.cooling_down(API_SERVICE):
            self.log("GPU API restart deferred: cooldown after previous restart")
            return
        if self.try_restart(API_SERVICE, f"{self.api_failures} consecutive failed health checks"):
            self.api_failures = 0

    def _comfy_answers(self) -> bool:
        try:
            self.http(f"{COMFY}/system_stats")
            return True
        except Exception:
            return False

    def check_qwen(self, quiet: bool) -> None:
        healthy, reason = self.qwen_healthy()
        if healthy:
            self.qwen_failures = 0
            return
        state, uptime = self.status(QWEN_SERVICE)
        if state in {"STOPPED", "STARTING", "BACKOFF", "STOPPING", "UNKNOWN"}:
            # STOPPED on purpose (operator or VRAM guard during a render); the rest is
            # Supervisor's own start sequence. Never start or restart it from here.
            self.qwen_failures = 0
            return
        self.qwen_failures += 1
        self.log(f"Qwen health failure {self.qwen_failures}/{QWEN_FAILURE_LIMIT} ({state}): {reason}")
        if self.qwen_failures < QWEN_FAILURE_LIMIT:
            return
        if state == "RUNNING" and (uptime is None or uptime < STARTUP_GRACE_SECONDS):
            return  # still loading weights / compiling CUDA graphs
        if not quiet:
            self.log("Qwen restart deferred: render or Director plan in progress")
            return
        if self.cooling_down(QWEN_SERVICE):
            return
        if self.try_restart(QWEN_SERVICE, f"{self.qwen_failures} failed health checks in state {state}"):
            self.qwen_failures = 0

    def run(self) -> None:
        self.log("watchdog started")
        while True:
            try:
                self.tick()
            except Exception as exc:  # the watchdog itself must never die
                self.log(f"watchdog pass failed: {type(exc).__name__}: {exc}")
            self.sleep(INTERVAL)


def main() -> None:
    Watchdog().run()


if __name__ == "__main__":
    main()
