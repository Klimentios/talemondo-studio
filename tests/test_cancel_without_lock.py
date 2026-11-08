"""Audit 42.3A №15: cancel and «Удалить всё» never wait for the dispatch lock; acting waits bounded."""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpx

from local_studio import main as local_main
from local_studio import acting
from local_studio.store import StudioStore


class CancelWithoutLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = local_main.ctx.store, local_main.ctx.gpu_transport
        local_main.ctx.store = StudioStore(Path(self.temp.name))
        path = local_main.ctx.store.assets_dir / "a.png"
        path.write_bytes(b"test")
        self.asset = local_main.ctx.store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png",
                                                size=4, sha256=hashlib.sha256(b"test").hexdigest(), path=path)

    def tearDown(self):
        local_main.ctx.store, local_main.ctx.gpu_transport = self.saved
        self.temp.cleanup()

    def job(self, state=None, remote=None):
        job = local_main.ctx.store.create_job(prompt="<Picture 1>", asset_ids=[self.asset["asset_id"]], settings={})
        if state:
            local_main.ctx.store.transition(job["job_id"], state, remote_job_id=remote)
        return job["job_id"]

    def call(self, method, url):
        async def exercise():
            await local_main.ctx.dispatch_lock.acquire()  # a long upload holds the lock
            try:
                transport = httpx.ASGITransport(app=local_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                    return await asyncio.wait_for(client.request(method, url), timeout=5)
            finally:
                local_main.ctx.dispatch_lock.release()
        return asyncio.run(exercise())

    def test_cancel_running_and_parked_while_lock_is_held(self):
        local_main.ctx.gpu_transport = type("T", (), {"cancel": AsyncMock(return_value=True)})()
        running = self.job("running", "remote-1")
        response = self.call("POST", f"/api/jobs/{running}/cancel")
        self.assertEqual((response.status_code, response.json()["state"]), (200, "stopping"))
        parked = self.job("needs_reconciliation")
        response = self.call("POST", f"/api/jobs/{parked}/cancel")
        self.assertEqual((response.status_code, response.json()["state"]), (200, "cancelled"))
        local_main.ctx.gpu_transport = type("T", (), {"cancel": AsyncMock(return_value=False)})()
        done = self.job("running", "remote-2")
        self.assertEqual(self.call("POST", f"/api/jobs/{done}/cancel").status_code, 409)
        self.assertEqual(self.call("POST", "/api/jobs/missing/cancel").status_code, 404)

    def test_clear_history_while_lock_is_held(self):
        waiting = self.job()
        response = self.call("DELETE", "/api/history")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(local_main.ctx.store.get_job(waiting)["state"], "cancelled")


class ActingLockTests(unittest.TestCase):
    def test_acting_wait_for_the_queue_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            assets = []
            for kind, mime, data in (("image", "image/png", b"img"), ("video", "video/mp4", b"vid")):
                path = store.assets_dir / kind
                path.write_bytes(data)
                assets.append(store.add_asset(kind=kind, role="r", original_name=kind, mime=mime, size=3,
                                              sha256=hashlib.sha256(data).hexdigest(), path=path)["asset_id"])
            lock = asyncio.Lock()
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(acting.make_router(store, "http://gpu", lock))
            original = acting.LOCK_WAIT_S
            acting.LOCK_WAIT_S = 0.05

            async def exercise():
                await lock.acquire()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                    return await client.post("/api/acting/prepare", json={
                        "mode": "human", "source_asset_id": assets[0], "driving_asset_id": assets[1]})
            try:
                response = asyncio.run(exercise())
            finally:
                acting.LOCK_WAIT_S = original
            self.assertEqual(response.status_code, 409)
            self.assertIn("занят очередью", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()


class CancelOnDeadGpuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = local_main.ctx.store, local_main.ctx.gpu_transport, local_main.ctx.gpu_url
        local_main.ctx.store = StudioStore(Path(self.temp.name))
        path = local_main.ctx.store.assets_dir / "a.png"
        path.write_bytes(b"test")
        asset = local_main.ctx.store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png",
                                           size=4, sha256=hashlib.sha256(b"test").hexdigest(), path=path)
        self.ids = []
        for remote in ("remote-dead", "minimax:123"):
            job = local_main.ctx.store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            local_main.ctx.store.transition(job["job_id"], "running", remote_job_id=remote)
            self.ids.append(job["job_id"])
        local_main.ctx.gpu_transport = type("T", (), {"cancel": AsyncMock(side_effect=httpx.ConnectError("down"))})()
        local_main.ctx.gpu_url = "http://127.0.0.1:9"  # nothing listens: the GPU server is gone

    def tearDown(self):
        local_main.ctx.store, local_main.ctx.gpu_transport, local_main.ctx.gpu_url = self.saved
        self.temp.cleanup()

    def cancel(self, job_id):
        async def exercise():
            transport = httpx.ASGITransport(app=local_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                return await asyncio.wait_for(client.post(f"/api/jobs/{job_id}/cancel"), timeout=20)
        return asyncio.run(exercise())

    def test_cancel_closes_a_gpu_job_as_lost_only_when_the_gpu_is_gone(self):
        response = self.cancel(self.ids[0])
        self.assertEqual((response.status_code, response.json()["state"]), (200, "interrupted"))
        self.assertIn("недоступен", response.json()["error"])
        self.assertEqual(self.cancel(self.ids[1]).status_code, 502)  # a cloud job is never closed locally
