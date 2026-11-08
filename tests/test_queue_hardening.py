"""Regression tests for the 2026-10-03 queue hardening."""
import asyncio
import hashlib
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from local_studio.store import StaleTransition, StudioStore
from tests.gpu_caps import CONTRACT_CAPS, serving_caps
from local_studio.worker import (
    HttpGpuTransport, PreEnqueueRejected, QueueWorker, SubmitNotSent,
)


class ScriptedGpu:
    def __init__(self, submit_error=None):
        self.submit_error = submit_error
        self.submissions = []
        self.remote = {}
        self.download_error = None

    async def lookup(self, job_id):
        return self.remote.get(job_id)

    async def submit(self, job, store):
        self.submissions.append(job["job_id"])
        if self.submit_error:
            raise self.submit_error
        return {"job_id": "remote-1"}

    async def status(self, remote_job_id):
        return {"status": "queued"}

    async def download(self, remote_job_id, destination):
        destination.write_bytes(b"partial")
        if self.download_error:
            raise self.download_error


def setup_store(root: Path):
    store = StudioStore(root)
    path = store.assets_dir / "a.png"
    path.write_bytes(b"test")
    asset = store.add_asset(kind="image", role="hero", original_name="a.png",
                            mime="image/png", size=4, sha256=hashlib.sha256(b"test").hexdigest(),
                            path=path)
    return store, asset


class QueueHardeningTests(unittest.TestCase):
    def test_connection_refused_keeps_job_queued_and_backs_off(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            gpu = ScriptedGpu(SubmitNotSent("refused"))
            worker = QueueWorker(store, gpu)
            asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "queued_local")
            asyncio.run(worker.run_once())  # inside back-off window: no second attempt
            self.assertEqual(len(gpu.submissions), 1)

    def test_validation_rejection_fails_job_and_unblocks_fifo(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            first = store.create_job(prompt="<Picture 1> a", asset_ids=[asset["asset_id"]], settings={})
            second = store.create_job(prompt="<Picture 1> b", asset_ids=[asset["asset_id"]], settings={})
            gpu = ScriptedGpu(PreEnqueueRejected("GPU API HTTP 422: bad"))
            worker = QueueWorker(store, gpu)
            asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(first["job_id"])["state"], "failed")
            gpu.submit_error = None
            asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(second["job_id"])["state"], "queued_gpu")

    def test_temporary_gpu_refusal_requeues_without_failing(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            gpu = ScriptedGpu(PreEnqueueRejected("Qwen not ready", retry_after=60))
            asyncio.run(QueueWorker(store, gpu).run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "queued_local")

    def test_old_reconciliation_without_server_record_is_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            store.transition(job["job_id"], "needs_reconciliation", error="timeout")
            worker = QueueWorker(store, ScriptedGpu())
            asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "needs_reconciliation")
            with closing(sqlite3.connect(store.db_path)) as db, db:
                db.execute("UPDATE jobs SET updated_at=datetime('now','-3 hours') WHERE job_id=?",
                           (job["job_id"],))
            asyncio.run(worker.run_once())
            closed = store.get_job(job["job_id"])
            self.assertEqual(closed["state"], "failed")
            self.assertIn("Автоповтор отключён", closed["error"])

    def test_broken_download_retries_and_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            store.transition(job["job_id"], "downloading", remote_job_id="remote-1")
            gpu = ScriptedGpu()
            gpu.download_error = ValueError("MP4 не читается")
            worker = QueueWorker(store, gpu)
            for _ in range(4):
                asyncio.run(worker.run_once())
                self.assertEqual(store.get_job(job["job_id"])["state"], "downloading")
            self.assertEqual(list(store.results_dir.glob(".*.part")), [])
            asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "failed")

    def test_tunnel_outage_during_download_waits_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            store.transition(job["job_id"], "downloading", remote_job_id="remote-1")
            gpu = ScriptedGpu()
            gpu.download_error = httpx.ConnectError("tunnel dropped")
            worker = QueueWorker(store, gpu)
            with patch("local_studio.worker.time.monotonic", return_value=1000.0):
                for _ in range(20):
                    asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "downloading")
            self.assertEqual(list(store.results_dir.glob(".*.part")), [])
            with patch("local_studio.worker.time.monotonic", return_value=1000.0 + 1800):
                asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "failed")

    def _submit_with(self, store, job, handler):
        class Scripted(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return handler(request)

        original_client = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = Scripted()
            return original_client(*args, **kwargs)

        with patch("httpx.AsyncClient", client):
            return asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))

    def test_gpu_validation_refusal_fails_only_this_job(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]], settings={})
            with self.assertRaises(PreEnqueueRejected):
                self._submit_with(store, job, serving_caps(
                    lambda r: httpx.Response(422, json={"detail": "Guide-кадр вне ролика"})))
            gpu = ScriptedGpu(PreEnqueueRejected("GPU API HTTP 422: Guide-кадр вне ролика"))
            asyncio.run(QueueWorker(store, gpu).run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "failed")

    def test_capabilities_outage_retries_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            from tests.test_director_config import CONFIG
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                   settings={"director_config": CONFIG})
            with self.assertRaises(SubmitNotSent):
                self._submit_with(store, job, lambda r: httpx.Response(503, json={"detail": "ComfyUI starting"}))

    def test_submit_without_connection_is_not_ambiguous(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]], settings={})

            class Refused(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    raise httpx.ConnectError("Connection refused")

            original_client = httpx.AsyncClient

            def client(*args, **kwargs):
                kwargs["transport"] = Refused()
                return original_client(*args, **kwargs)

            with patch("httpx.AsyncClient", client):
                with self.assertRaises(SubmitNotSent):
                    asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))

    def test_cancel_wins_over_concurrent_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            store.cancel_queued(job["job_id"])
            with self.assertRaises(StaleTransition):
                store.transition(job["job_id"], "uploading", expected_states=("queued_local",))
            self.assertEqual(store.get_job(job["job_id"])["state"], "cancelled")

    def test_legacy_precompiled_job_sends_compiled_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = setup_store(Path(temp))
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                   settings={"emotional_direction": False})
            self.assertNotEqual(job["compiled_prompt"], job["original_prompt"])
            sent = {}

            class Capture(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    if request.url.path == "/api/capabilities":
                        return httpx.Response(200, json=CONTRACT_CAPS)
                    sent["body"] = (await request.aread()).decode("utf-8", "replace")
                    return httpx.Response(200, json={"job_id": "remote-1"})

            original_client = httpx.AsyncClient

            def client(*args, **kwargs):
                kwargs["transport"] = Capture()
                return original_client(*args, **kwargs)

            with patch("httpx.AsyncClient", client):
                asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
            self.assertIn(job["compiled_prompt"].splitlines()[0], sent["body"])
            self.assertIn('name="precompiled_prompt"\r\n\r\ntrue', sent["body"])

    def test_emotional_director_receives_character_and_reference_contracts(self):
        with tempfile.TemporaryDirectory() as temp:
            store, first = setup_store(Path(temp))
            path = store.assets_dir / "orion.png"
            path.write_bytes(b"second")
            second = store.add_asset(kind="image", role="Персонаж целиком", original_name=path.name,
                                     mime="image/png", size=6,
                                     sha256=hashlib.sha256(b"second").hexdigest(), path=path)
            ids = [first["asset_id"], second["asset_id"]]
            story = "<Picture 1> defines VEGA. <Picture 2> defines ORION. ORION moves the key."
            job = store.create_job(prompt=story, asset_ids=ids,
                                   settings={"emotional_direction": True},
                                   reference_roles={key: "Персонаж целиком" for key in ids},
                                   reference_owners=dict(zip(ids, ("VEGA", "ORION"))))
            sent = {}

            class Capture(httpx.AsyncBaseTransport):
                async def handle_async_request(self, request):
                    if request.url.path == "/api/capabilities":
                        return httpx.Response(200, json=CONTRACT_CAPS)
                    sent["body"] = (await request.aread()).decode("utf-8", "replace")
                    return httpx.Response(200, json={"job_id": "remote-1"})

            original_client = httpx.AsyncClient

            def client(*args, **kwargs):
                return original_client(*args, transport=Capture(), **kwargs)

            with patch("httpx.AsyncClient", client):
                asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
            self.assertIn("<Subject 1> is VEGA", sent["body"])
            self.assertIn("<Subject 2> is ORION", sent["body"])
            self.assertIn("<Picture 2> belongs to ORION only", sent["body"])
            self.assertIn(story, sent["body"])
            self.assertIn('name="precompiled_prompt"\r\n\r\nfalse', sent["body"])

    def test_schema_migration_backs_up_existing_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store, asset = setup_store(root)
            store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            with closing(sqlite3.connect(store.db_path)) as db, db:
                db.execute("ALTER TABLE jobs DROP COLUMN sampling_max")
            StudioStore(root)
            backups = list((root / "code-backups").glob("studio-pre-migrate-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
            StudioStore(root)  # already migrated: no second backup
            self.assertEqual(len(list((root / "code-backups").glob("*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
