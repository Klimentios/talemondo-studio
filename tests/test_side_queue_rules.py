"""Audit 42.3A: side jobs (upscale/audio/motion) and parked H3 jobs must never block the Mac queue."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
import unittest.mock
from unittest.mock import patch

import httpx

from local_studio import side_jobs
from local_studio.audio_jobs import AudioQueue
from local_studio.store import StudioStore
from local_studio.upscale import UpscaleQueue
from local_studio.worker import QueueWorker

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class Gpu:
    """Scripted GPU API: routes[(method, path-prefix)] -> Response or Exception."""

    def __init__(self):
        self.routes: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        for (method, prefix), answer in sorted(self.routes.items(), key=lambda kv: -len(kv[0][1])):
            if request.method == method and request.url.path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return httpx.Response(404, json={"detail": "not found"})

    def posts(self, prefix: str) -> int:
        return sum(1 for method, path in self.calls if method == "POST" and path.startswith(prefix))


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StudioStore(Path(self.temp.name))
        self.gpu = Gpu()
        original = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(self.gpu.handler))
            return original(*args, **kwargs)
        # All queue modules share the one httpx module object: patch it once.
        self.patch = patch("httpx.AsyncClient", client)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def age(self, table: str, hours: float) -> None:
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            db.execute(f"UPDATE {table} SET updated_at=datetime('now','-{hours} hours')")

    def run1(self, queue):
        return asyncio.run(queue.run_once())

    def completed_source(self) -> str:
        job_id = str(uuid.uuid4())
        path = self.store.results_dir / f"{job_id}.mp4"
        path.write_bytes(b"\0" * 2048)
        with self.store.connect() as db:
            db.execute("INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,"
                       "mapping_json,output_path) VALUES (?,?,?,?,?,?,?,?)",
                       (job_id, "completed", "p", "p", "{}", "{}", "{}", str(path)))
        return job_id

    def image(self) -> str:
        path = self.store.assets_dir / uuid.uuid4().hex
        path.write_bytes(PNG)
        return self.store.add_asset(kind="image", role="Персонаж целиком", original_name="a.png", mime="image/png",
                                    size=len(PNG), sha256=hashlib.sha256(PNG).hexdigest(), path=path)["asset_id"]


class UpscaleRulesTests(Base):
    def queue(self):
        return UpscaleQueue(self.store, "http://gpu")

    def caps(self, available=True, missing=()):
        self.gpu.routes[("GET", "/api/upscale/capabilities")] = httpx.Response(
            200, json={"available": available, "missing": list(missing), "model": "SeedVR2", "mode": "upscale"})

    def test_capabilities_are_checked_before_upload(self):
        queue = self.queue()
        job = queue.create(self.completed_source())
        self.caps(False, ["SeedVR2VideoUpscaler"])
        self.assertTrue(self.run1(queue))
        row = queue._row(job["upscale_id"])
        self.assertEqual(row["state"], "failed")
        self.assertIn("SeedVR2VideoUpscaler", row["error"])
        self.assertEqual(self.gpu.posts("/api/upscale/jobs"), 0)

    def test_refusals_before_acceptance_fail_and_connect_error_requeues(self):
        self.caps()
        for status in (413, 422, 503):
            queue = self.queue()
            job = queue.create(self.completed_source())
            self.gpu.routes[("POST", "/api/upscale/jobs")] = httpx.Response(status, json={"detail": f"нет {status}"})
            self.run1(queue)
            row = queue._row(job["upscale_id"])
            self.assertEqual((row["state"], row["error"]), ("failed", f"нет {status}"))
        queue = self.queue()
        job = queue.create(self.completed_source())
        self.gpu.routes[("POST", "/api/upscale/jobs")] = httpx.ConnectError("refused")
        self.run1(queue)
        self.assertEqual(queue._row(job["upscale_id"])["state"], "queued_local")
        self.gpu.routes[("POST", "/api/upscale/jobs")] = httpx.ReadTimeout("slow")
        self.run1(queue)
        self.assertEqual(queue._row(job["upscale_id"])["state"], "needs_reconciliation")

    def test_lost_and_404_on_poll_fail(self):
        self.caps()
        self.gpu.routes[("POST", "/api/upscale/jobs")] = httpx.Response(202, json={"job_id": "r1"})
        queue = self.queue()
        job = queue.create(self.completed_source())
        self.run1(queue)
        self.assertEqual(queue._row(job["upscale_id"])["state"], "queued_gpu")
        self.gpu.routes[("GET", "/api/upscale/jobs/r1")] = httpx.Response(200, json={"status": "unknown", "lost": True, "error": "потерян"})
        self.run1(queue)
        self.assertEqual(queue._row(job["upscale_id"])["state"], "failed")
        # 404 (new server): a few answers in a row, then failed — not an endless poll.
        job = queue.create(self.completed_source())
        self.gpu.routes[("GET", "/api/upscale/jobs/r1")] = httpx.Response(404)
        self.run1(queue)  # submit
        for _ in range(side_jobs.UNKNOWN_POLLS_LIMIT - 1):
            self.run1(queue)
            self.assertEqual(queue._row(job["upscale_id"])["state"], "queued_gpu")
        self.run1(queue)
        row = queue._row(job["upscale_id"])
        self.assertEqual(row["state"], "failed")
        self.assertIn("GPU не знает", row["error"])

    def test_download_404_stops_after_limit(self):
        queue = self.queue()
        job = queue.create(self.completed_source())
        queue.update(job["upscale_id"], "downloading", remote_job_id="r1")
        for _ in range(side_jobs.DOWNLOAD_FAILURES_LIMIT - 1):
            self.assertFalse(self.run1(queue))
            self.assertEqual(queue._row(job["upscale_id"])["state"], "downloading")
        self.assertTrue(self.run1(queue))
        self.assertEqual(queue._row(job["upscale_id"])["state"], "failed")
        self.assertFalse(list(self.store.results_dir.glob("*.part")))

    def test_download_outage_fails_after_outage_limit(self):
        queue = self.queue()
        job = queue.create(self.completed_source())
        queue.update(job["upscale_id"], "downloading", remote_job_id="r1")
        self.gpu.routes[("GET", "/api/upscale/jobs/r1/video")] = httpx.ConnectError("tunnel down")
        for _ in range(10):
            self.run1(queue)
        self.assertEqual(queue._row(job["upscale_id"])["state"], "downloading")
        queue._download_outage_since[job["upscale_id"]] -= side_jobs.DOWNLOAD_OUTAGE_LIMIT_S
        self.run1(queue)
        self.assertEqual(queue._row(job["upscale_id"])["state"], "failed")

    def test_parked_job_expires_and_can_be_cancelled(self):
        queue = self.queue()
        job = queue.create(self.completed_source())
        queue.update(job["upscale_id"], "uploading")
        self.run1(queue)  # crash mid-upload: GPU has no receipt -> parked, never resent
        self.assertEqual(queue._row(job["upscale_id"])["state"], "needs_reconciliation")
        self.assertIsNone(queue.first_active())
        self.age("upscale_jobs", 1)
        asyncio.run(queue.reconcile())
        self.assertEqual(queue._row(job["upscale_id"])["state"], "needs_reconciliation")
        self.age("upscale_jobs", 3)
        self.gpu.routes[("GET", "/api/upscale/by-client")] = httpx.ConnectError("offline")
        asyncio.run(queue.reconcile())  # expires even while the GPU is offline
        self.assertEqual(queue._row(job["upscale_id"])["state"], "failed")
        self.assertEqual(self.gpu.posts("/api/upscale/jobs"), 0)
        other = queue.create(self.completed_source())
        queue.park(other["upscale_id"], "ambiguous")
        self.assertEqual(queue.cancel(other["upscale_id"])["state"], "cancelled")
        queue.update(other["upscale_id"], "queued_gpu", remote_job_id="late")  # dispatcher never overwrites it
        self.assertEqual(queue._row(other["upscale_id"])["state"], "cancelled")
        with self.assertRaises(ValueError):
            queue.cancel(other["upscale_id"])
        with self.assertRaises(LookupError):
            queue.cancel("missing")

    def test_reconcile_adopts_receipt_found_by_client_id(self):
        queue = self.queue()
        job = queue.create(self.completed_source())
        queue.park(job["upscale_id"], "ambiguous")
        self.gpu.routes[("GET", "/api/upscale/by-client")] = httpx.Response(200, json={"job_id": "r9", "status": "queued"})
        self.assertTrue(asyncio.run(queue.reconcile()))
        row = queue._row(job["upscale_id"])
        self.assertEqual((row["state"], row["remote_job_id"]), ("queued_gpu", "r9"))

    def test_cancel_endpoint(self):
        from fastapi import FastAPI
        from local_studio.upscale import make_router
        queue = self.queue()
        job = queue.create(self.completed_source())
        queue.park(job["upscale_id"], "ambiguous")
        app = FastAPI()
        app.include_router(make_router(queue))

        async def call(uid):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                return await client.post(f"/api/upscale/jobs/{uid}/cancel")
        self.assertEqual(asyncio.run(call(job["upscale_id"])).json()["state"], "cancelled")
        self.assertEqual(asyncio.run(call(job["upscale_id"])).status_code, 409)
        self.assertEqual(asyncio.run(call("nope")).status_code, 404)


class AudioRulesTests(Base):
    def _audio(self, queue):
        uid = str(uuid.uuid4())
        project = {"mode": "audio", "output_format": "wav", "lines": [], "mix": {"music_prompt": "calm"}}
        with self.store.connect() as db:
            db.execute("INSERT INTO audio_jobs(audio_id,project_json,manifest_json,state) VALUES (?,?,?,'queued_local')",
                       (uid, json.dumps(project), "{}"))
        return uid

    def test_audio_lost_and_404_fail_and_gpu_restart_fails(self):
        queue = AudioQueue(self.store, "http://gpu")
        for answer in (httpx.Response(200, json={"status": "unknown", "lost": True}),
                       httpx.Response(200, json={"status": "needs_reconciliation", "error": "перезапуск"})):
            uid = self._audio(queue)
            queue.update(uid, "queued_gpu", remote_job_id="a1")
            self.gpu.routes[("GET", "/api/audio/jobs/a1")] = answer
            self.run1(queue)
            self.assertEqual(queue.get(uid)["state"], "failed")
        uid = self._audio(queue)
        queue.update(uid, "queued_gpu", remote_job_id="a2")
        for _ in range(side_jobs.UNKNOWN_POLLS_LIMIT):
            self.run1(queue)
        self.assertEqual(queue.get(uid)["state"], "failed")

    def test_audio_409_parks_once_and_expires(self):
        queue = AudioQueue(self.store, "http://gpu")
        uid = self._audio(queue)
        self.gpu.routes[("GET", "/api/audio/capabilities")] = httpx.Response(200, json={"music_generation": True})
        self.gpu.routes[("POST", "/api/audio/jobs")] = httpx.Response(409, json={"detail": "conflict"})
        self.run1(queue)
        self.assertEqual(queue.get(uid)["state"], "needs_reconciliation")
        self.age("audio_jobs", 3)
        asyncio.run(queue.reconcile())
        self.assertEqual(queue.get(uid)["state"], "failed")
        self.assertEqual(self.gpu.posts("/api/audio/jobs"), 1)


class DispatchTests(unittest.TestCase):
    def test_parked_side_job_does_not_take_every_pass(self):
        from local_studio.main import next_dispatch
        base = dict(h3_active=None, h3_pending=None, upscale=None, audio=None, paused=False)
        h3 = {"created_at": "2026-10-05 10:00:00"}
        for state in ("needs_reconciliation", "cancelled", "failed"):
            parked = {"created_at": "2026-10-05 09:00:00", "state": state}
            self.assertEqual(next_dispatch(**{**base, "h3_pending": h3, "audio": parked}), "h3", state)
        for state in ("uploading", "queued_gpu", "running", "downloading"):
            busy = {"created_at": "2026-10-05 11:00:00", "state": state}
            self.assertEqual(next_dispatch(**{**base, "h3_active": {"job_id": "a"}, "upscale": busy}), "upscale")

    def test_rate_limited_dispatcher_errors_are_logged(self):
        from local_studio.main import _RateLimitedErrors
        logger = unittest.mock.MagicMock()
        errors = _RateLimitedErrors(logger)
        for _ in range(5):
            try:
                raise RuntimeError("tunnel down")
            except RuntimeError:
                errors.report("Диспетчер")
        self.assertEqual(logger.exception.call_count, 1)
        errors._last = {key: value - 61 for key, value in errors._last.items()}
        try:
            raise RuntimeError("tunnel down")
        except RuntimeError:
            errors.report("Диспетчер")
        self.assertEqual(logger.exception.call_count, 2)
        self.assertIn("ещё 4", logger.exception.call_args.args[2])


class H3ReconciliationTests(unittest.TestCase):
    class Gpu:
        def __init__(self):
            self.remote, self.submissions, self.offline = {}, [], False

        async def lookup(self, job_id):
            if self.offline:
                raise httpx.ConnectError("offline")
            return self.remote.get(job_id)

        async def submit(self, job, store):
            self.submissions.append(job["job_id"])
            return {"job_id": f"remote-{job['job_id']}"}

        async def status(self, remote_job_id):
            return {"status": "queued"}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StudioStore(Path(self.temp.name))
        path = self.store.assets_dir / "a.png"
        path.write_bytes(b"test")
        self.asset = self.store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png", size=4,
                                          sha256=hashlib.sha256(b"test").hexdigest(), path=path)

    def tearDown(self):
        self.temp.cleanup()

    def job(self):
        return self.store.create_job(prompt="<Picture 1>", asset_ids=[self.asset["asset_id"]], settings={})

    def test_parked_job_does_not_block_fifo_and_is_never_resubmitted(self):
        gpu = self.Gpu()
        worker = QueueWorker(self.store, gpu)
        parked, later = self.job(), self.job()
        self.store.transition(parked["job_id"], "needs_reconciliation", error="timeout")
        self.assertIsNone(self.store.active_job())
        asyncio.run(worker.run_once())
        self.assertEqual(gpu.submissions, [later["job_id"]])
        self.assertEqual(self.store.get_job(later["job_id"])["state"], "queued_gpu")
        # Background reconciliation finds the parked job on the GPU by its client ID.
        gpu.remote[parked["job_id"]] = {"job_id": "remote-parked"}
        self.assertTrue(asyncio.run(worker.reconcile_once()))
        self.assertEqual(self.store.get_job(parked["job_id"])["state"], "queued_gpu")
        self.assertEqual(gpu.submissions, [later["job_id"]])

    def test_parked_job_expires_while_gpu_offline(self):
        gpu = self.Gpu()
        gpu.offline = True
        job = self.job()
        self.store.transition(job["job_id"], "needs_reconciliation", error="timeout")
        worker = QueueWorker(self.store, gpu)
        self.assertFalse(asyncio.run(worker.reconcile_once()))
        with closing(sqlite3.connect(self.store.db_path)) as db, db:
            db.execute("UPDATE jobs SET updated_at=datetime('now','-3 hours')")
        self.assertTrue(asyncio.run(worker.reconcile_once()))
        closed = self.store.get_job(job["job_id"])
        self.assertEqual(closed["state"], "failed")
        self.assertIn("GPU был недоступен", closed["error"])
        self.assertEqual(gpu.submissions, [])



class StalePartialsTests(unittest.TestCase):
    def test_startup_removes_only_partials(self):
        from local_studio.main import _remove_stale_partials
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            keep = [store.results_dir / "job.mp4", store.assets_dir / "ab" / "abcdef"]
            stale = [store.results_dir / ".job.part", store.results_dir / ".upscale-1.part",
                     store.results_dir / ".audio-music-1.part", store.assets_dir / "ab" / ".keyframe-1.part",
                     store.assets_dir / ".scene-frame-1.123.png"]
            for path in keep + stale:
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"x")
            self.assertEqual(_remove_stale_partials(store), len(stale))
            self.assertTrue(all(path.is_file() for path in keep))
            self.assertFalse(any(path.exists() for path in stale))


if __name__ == "__main__":
    unittest.main()
