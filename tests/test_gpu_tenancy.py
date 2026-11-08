"""One model on the GPU at a time (user, 2026-10-08)."""
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app import qwen_lifecycle


@contextmanager
def no_locks():
    yield


def test_exclusive_task_frees_comfy_before_running():
    order = []
    with patch.object(qwen_lifecycle, "render_submission", no_locks), \
         patch.object(qwen_lifecycle, "queue_active", return_value=False), \
         patch.object(qwen_lifecycle, "_free_comfy_models", lambda: order.append("free")):
        with qwen_lifecycle.exclusive_gpu_task():
            order.append("task")
    assert order == ["free", "task"]


def test_exclusive_task_refuses_beside_a_render():
    with patch.object(qwen_lifecycle, "render_submission", no_locks), \
         patch.object(qwen_lifecycle, "queue_active", return_value=True), \
         patch.object(qwen_lifecycle, "_free_comfy_models") as free:
        with pytest.raises(qwen_lifecycle.GpuBusy):
            with qwen_lifecycle.exclusive_gpu_task():
                raise AssertionError("must not run")
        free.assert_not_called()


def test_liveportrait_runs_inside_the_exclusive_task(tmp_path, monkeypatch):
    from app import acting
    seen = []

    @contextmanager
    def task():
        seen.append("enter")
        try:
            yield
        finally:
            seen.append("exit")

    def fake_run(command, **_):
        seen.append("liveportrait")
        raise RuntimeError("stop after the GPU step")

    monkeypatch.setenv("H3_ACTING_CACHE", str(tmp_path))
    monkeypatch.setattr(acting, "runtime_paths", lambda: (tmp_path, tmp_path / "python"))
    monkeypatch.setattr(acting, "_ffprobe", lambda path: None)
    monkeypatch.setattr(acting, "exclusive_gpu_task", task)
    monkeypatch.setattr(acting.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        acting._run(b"img", b"vid", "human", "k")
    assert seen == ["enter", "liveportrait", "exit"]


def test_every_planner_profile_is_gpu_exclusive():
    from app.scenarist_profile import PROFILES
    assert all(profile["exclusive_gpu"] for profile in PROFILES.values())
