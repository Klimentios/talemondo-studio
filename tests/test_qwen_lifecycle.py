import unittest
import asyncio
from unittest.mock import patch

from app import qwen_lifecycle as lifecycle


class QwenLifecycleTests(unittest.TestCase):
    def test_active_comfy_queue_stops_qwen(self):
        with patch.object(lifecycle, "queue_active", return_value=True), \
             patch.object(lifecycle, "service_running", return_value=True), \
             patch.object(lifecycle, "_supervisor") as supervisor, \
             patch.object(lifecycle, "lifecycle_lock", return_value=_noop_lock()):
            self.assertIsNone(lifecycle.reconcile_once(1.0))
        supervisor.assert_called_once_with("stop", lifecycle.SERVICE)

    def test_qwen_starts_only_after_stable_idle_and_comfy_release(self):
        with patch.object(lifecycle, "queue_active", return_value=False), \
             patch.object(lifecycle, "service_running", return_value=False), \
             patch.object(lifecycle, "_free_comfy_models") as free, \
             patch.object(lifecycle, "_supervisor") as supervisor, \
             patch.object(lifecycle, "_wait_qwen_ready"), \
             patch.object(lifecycle, "_post") as post, \
             patch.object(lifecycle, "enabled", return_value=True), \
             patch.object(lifecycle, "lifecycle_lock", return_value=_noop_lock()), \
             patch.object(lifecycle.time, "monotonic", side_effect=[10.0, 15.0, 21.0]):
            idle = lifecycle.reconcile_once(None)
            idle = lifecycle.reconcile_once(idle)
            self.assertEqual(idle, 10.0)
            supervisor.assert_not_called()
            lifecycle.reconcile_once(idle)
        free.assert_called_once()
        supervisor.assert_called_once_with("start", lifecycle.SERVICE)
        post.assert_called_once_with(f"{lifecycle.QWEN_URL}/sleep?level=1")

    def test_render_submission_holds_lock_through_queue_call(self):
        events = []

        class RecordingLock:
            def __enter__(self): events.append("lock")
            def __exit__(self, *_): events.append("unlock")

        with patch.object(lifecycle, "enabled", return_value=True), \
             patch.object(lifecycle, "lifecycle_lock", return_value=RecordingLock()), \
             patch.object(lifecycle, "service_running", return_value=True), \
             patch.object(lifecycle, "_supervisor", side_effect=lambda *_: events.append("stop")):
            with lifecycle.render_submission():
                events.append("queue")
        self.assertEqual(events, ["lock", "stop", "queue", "unlock"])


class _noop_lock:
    def __enter__(self): return self
    def __exit__(self, *_): return None


if __name__ == "__main__":
    unittest.main()


def test_free_request_actually_unloads_comfy_models():
    with patch.object(lifecycle,'queue_active',return_value=False), patch.object(lifecycle,'_post') as post, \
         patch.object(lifecycle.time,'sleep'):
        lifecycle._free_comfy_models()
    assert post.call_count == 2
    post.assert_called_with(f'{lifecycle.COMFY_URL}/free', {'unload_models':True,'free_memory':True})


def test_stopped_supervisor_status_does_not_break_automatic_wakeup():
    import subprocess
    with patch.object(lifecycle.subprocess,'run',return_value=subprocess.CompletedProcess([],3, 'h3-qwen-director STOPPED','')):
        assert not lifecycle.service_running()


def test_busy_render_blocks_wakeup_even_if_qwen_service_already_runs():
    import pytest
    with patch.object(lifecycle,'enabled',return_value=True), patch.object(lifecycle,'lifecycle_lock',return_value=_noop_lock()), \
         patch.object(lifecycle,'queue_active',return_value=True), patch.object(lifecycle,'_wait_qwen_ready') as ready:
        with pytest.raises(RuntimeError,match='rendering'):
            lifecycle.ensure_started()
    ready.assert_not_called()


def test_concurrent_submissions_do_not_block_api_event_loop(tmp_path):
    events = []
    async def job(name):
        async with lifecycle.async_render_submission():
            events.append(f"{name}:enter")
            await asyncio.sleep(0.02)
            events.append(f"{name}:exit")
    async def run():
        await asyncio.wait_for(asyncio.gather(job("a"), job("b")), timeout=2)
    with patch.object(lifecycle, "enabled", return_value=False), \
         patch.object(lifecycle, "LOCK_PATH", tmp_path / "lifecycle.lock"), \
         patch.object(lifecycle, "PLAN_LOCK_PATH", tmp_path / "plan.lock"):
        asyncio.run(run())
    assert events in (["a:enter", "a:exit", "b:enter", "b:exit"],
                      ["b:enter", "b:exit", "a:enter", "a:exit"])


def test_cancelled_while_acquiring_still_releases_the_guard():
    import threading
    events = []
    entered = threading.Event()
    proceed = threading.Event()
    released = threading.Event()

    class SlowGuard:
        def __enter__(self):
            entered.set()
            proceed.wait(2)
            events.append("enter")
        def __exit__(self, *_):
            events.append("exit")
            released.set()

    async def run():
        async def job():
            async with lifecycle.async_render_submission():
                events.append("body")
        task = asyncio.ensure_future(job())
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            events.append("cancelled")
        proceed.set()
        assert len(lifecycle._PENDING_RELEASES) == 1  # strong reference kept
        await asyncio.wait_for(asyncio.gather(*lifecycle._PENDING_RELEASES), 2)
        assert released.is_set()

    with patch.object(lifecycle, "render_submission", return_value=SlowGuard()):
        asyncio.run(run())
    assert events == ["cancelled", "enter", "exit"]
    assert not lifecycle._PENDING_RELEASES
