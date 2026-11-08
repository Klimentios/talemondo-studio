"""scripts/h3_runtime_watchdog.py: never disturb a sleeping Qwen, a render or a planned job."""
from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "h3_runtime_watchdog.py"
_spec = importlib.util.spec_from_file_location("h3_runtime_watchdog", _PATH)
wd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wd)


class Stack:
    """Fake HTTP + Supervisor. `answers[url_suffix]` is a value or an exception instance."""

    def __init__(self) -> None:
        self.answers: dict[str, object] = {
            "18188/queue": {"queue_running": [], "queue_pending": []},
            "18188/system_stats": {},
            "18188/free": {},
            "18189/api/live": {"status": "ok"},
            "8000/health": {},
            "8000/is_sleeping": {"is_sleeping": True},
        }
        self.states = {"h3-api": ("RUNNING", 3600.0), "h3-qwen-director": ("RUNNING", 3600.0)}
        self.restarts: list[str] = []
        self.busy = False
        self.now = 10_000.0
        self.logs: list[str] = []

    def http(self, url, *, payload=None, timeout=8):
        for suffix, answer in self.answers.items():
            if url.endswith(suffix):
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO())

    def watchdog(self) -> "wd.Watchdog":
        return wd.Watchdog(http=self.http, status=lambda name: self.states[name],
                           restart=self.restarts.append, busy=lambda: self.busy,
                           clock=lambda: self.now, sleep=lambda _s: None, logger=self.logs.append)


def run(stack: Stack, dog, ticks: int) -> None:
    for _ in range(ticks):
        dog.tick()
        stack.now += 15


def test_sleeping_vllm_is_healthy_even_when_health_fails():
    stack = Stack()
    stack.answers["8000/health"] = ConnectionError("refused")
    stack.answers["8000/is_sleeping"] = {"is_sleeping": True}
    run(stack, stack.watchdog(), 50)
    assert stack.restarts == []


def test_bare_bool_is_sleeping_counts_as_healthy():
    assert wd.qwen_answer_healthy(False, True)
    assert wd.qwen_answer_healthy(False, {"is_sleeping": False})
    assert not wd.qwen_answer_healthy(False, None)
    assert not wd.qwen_answer_healthy(False, {"detail": "x"})


def test_dead_running_qwen_restarts_once_then_cools_down():
    stack = Stack()
    stack.answers["8000/health"] = ConnectionError("refused")
    stack.answers["8000/is_sleeping"] = ConnectionError("refused")
    dog = stack.watchdog()
    run(stack, dog, wd.QWEN_FAILURE_LIMIT - 1)
    assert stack.restarts == []
    run(stack, dog, 1)
    assert stack.restarts == ["h3-qwen-director"]
    run(stack, dog, 40)  # 10 min of failures: inside the cooldown
    assert stack.restarts == ["h3-qwen-director"]


def test_qwen_in_startup_grace_is_not_restarted():
    stack = Stack()
    stack.answers["8000/health"] = ConnectionError("refused")
    stack.answers["8000/is_sleeping"] = ConnectionError("refused")
    stack.states["h3-qwen-director"] = ("RUNNING", 30.0)
    run(stack, stack.watchdog(), 30)
    assert stack.restarts == []


@pytest.mark.parametrize("state", ["STOPPED", "STARTING", "BACKOFF"])
def test_qwen_stopped_by_guard_or_starting_is_left_alone(state):
    stack = Stack()
    stack.answers["8000/health"] = ConnectionError("refused")
    stack.answers["8000/is_sleeping"] = ConnectionError("refused")
    stack.states["h3-qwen-director"] = (state, None)
    run(stack, stack.watchdog(), 60)
    assert stack.restarts == []


def test_fatal_qwen_restarts_when_quiet():
    stack = Stack()
    stack.answers["8000/health"] = ConnectionError("refused")
    stack.answers["8000/is_sleeping"] = ConnectionError("refused")
    stack.states["h3-qwen-director"] = ("FATAL", None)
    run(stack, stack.watchdog(), wd.QWEN_FAILURE_LIMIT)
    assert stack.restarts == ["h3-qwen-director"]


def test_nothing_restarts_while_render_runs():
    stack = Stack()
    stack.answers["18188/queue"] = {"queue_running": [[1, "p"]], "queue_pending": []}
    stack.answers["18189/api/live"] = ConnectionError("refused")
    stack.answers["8000/health"] = ConnectionError("refused")
    stack.answers["8000/is_sleeping"] = ConnectionError("refused")
    dog = stack.watchdog()
    run(stack, dog, 60)
    assert stack.restarts == []
    stack.answers["18188/queue"] = {"queue_running": [], "queue_pending": []}
    run(stack, dog, 1)
    assert set(stack.restarts) == {"h3-api", "h3-qwen-director"}


def test_nothing_restarts_while_comfy_queue_unreadable_or_plan_in_flight():
    stack = Stack()
    stack.answers["18188/queue"] = ConnectionError("refused")
    stack.answers["18189/api/live"] = ConnectionError("refused")
    run(stack, stack.watchdog(), 30)
    stack2 = Stack()
    stack2.busy = True
    stack2.answers["18189/api/live"] = ConnectionError("refused")
    run(stack2, stack2.watchdog(), 30)
    assert stack.restarts == [] and stack2.restarts == []


def test_api_restart_needs_limit_and_running_state():
    stack = Stack()
    stack.answers["18189/api/live"] = ConnectionError("refused")
    dog = stack.watchdog()
    run(stack, dog, wd.API_FAILURE_LIMIT - 1)
    assert stack.restarts == []
    run(stack, dog, 1)
    assert stack.restarts == ["h3-api"]
    stopped = Stack()
    stopped.answers["18189/api/live"] = ConnectionError("refused")
    stopped.states["h3-api"] = ("STOPPED", None)
    run(stopped, stopped.watchdog(), 20)
    assert stopped.restarts == []


def test_legacy_api_without_live_falls_back_to_health_and_spares_it_when_comfy_down():
    stack = Stack()
    del stack.answers["18189/api/live"]  # -> 404
    stack.answers["18189/api/health"] = urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO())
    stack.answers["18188/system_stats"] = ConnectionError("comfy down")
    run(stack, stack.watchdog(), 20)
    assert stack.restarts == []


def test_idle_vram_release_only_when_qwen_sleeps_and_quiet():
    stack = Stack()
    freed = []
    http = stack.http

    def spy(url, **kw):
        if url.endswith("/free"):
            freed.append(url)
        return http(url, **kw)

    stack.http = spy
    run(stack, stack.watchdog(), 5)
    assert len(freed) == 2  # once per idle interval, two passes
    stack2 = Stack()
    stack2.answers["8000/is_sleeping"] = {"is_sleeping": False}
    freed2 = []
    http2 = stack2.http
    stack2.http = lambda url, **kw: (freed2.append(url) if url.endswith("/free") else None) or http2(url, **kw)
    run(stack2, stack2.watchdog(), 5)
    assert freed2 == []


def test_supervisor_status_parsing():
    assert wd.parse_supervisor_status("h3-api   RUNNING   pid 12, uptime 0:01:05") == ("RUNNING", 65.0)
    assert wd.parse_supervisor_status("q  RUNNING   pid 1, uptime 2 days, 1:00:00")[1] == 2 * 86400 + 3600
    assert wd.parse_supervisor_status("q  STOPPED   Oct 05 07:00 AM") == ("STOPPED", None)
    assert wd.parse_supervisor_status("") == ("UNKNOWN", None)


def test_submission_busy_sees_held_flock(tmp_path, monkeypatch):
    import fcntl
    lock = tmp_path / "plan.lock"
    monkeypatch.setattr(wd, "BUSY_LOCKS", (lock,))
    assert wd.submission_busy() is False
    with lock.open("a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        # flock is per open file description: a second open() in this process contends.
        assert wd.submission_busy() is True
    assert wd.submission_busy() is False
