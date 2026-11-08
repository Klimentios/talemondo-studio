"""Versioned Mac <-> GPU contract: api_contract + director schema hash in /api/capabilities."""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.contract import API_CONTRACT, DIRECTOR_SCHEMA_PATH, director_schema_sha256, gpu_contract_problem
from local_studio.store import StudioStore
from local_studio.worker import CAPABILITIES_TTL_S, HttpGpuTransport, QueueWorker, SubmitNotSent
from tests.gpu_caps import CONTRACT_CAPS

try:
    import httpx
    from app import main as remote_main
except ImportError:  # pragma: no cover
    httpx = None
    remote_main = None


def _job(root: Path):
    store = StudioStore(root)
    path = store.assets_dir / "a.png"
    path.write_bytes(b"test")
    asset = store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png", size=4,
                            sha256=hashlib.sha256(b"test").hexdigest(), path=path)
    return store, store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]], settings={})


def _client_with(handler):
    original = httpx.AsyncClient

    class Scripted(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return handler(request)

    def client(*args, **kwargs):
        kwargs["transport"] = Scripted()
        return original(*args, **kwargs)
    return client


class ContractTests(unittest.TestCase):
    def test_schema_hash_and_problems(self):
        self.assertEqual(director_schema_sha256(), hashlib.sha256(DIRECTOR_SCHEMA_PATH.read_bytes()).hexdigest())
        self.assertIsNone(gpu_contract_problem(CONTRACT_CAPS))
        self.assertIn("обнови сервер", gpu_contract_problem({}).lower())
        self.assertIn("обнови сервер", gpu_contract_problem({**CONTRACT_CAPS, "api_contract": API_CONTRACT - 1}).lower())
        self.assertIn("Схема Director", gpu_contract_problem({**CONTRACT_CAPS, "director_schema_sha256": "x"}))
        # A newer GPU still serves an older client when the schema matches.
        self.assertIsNone(gpu_contract_problem({**CONTRACT_CAPS, "api_contract": API_CONTRACT + 1}))

    @unittest.skipIf(remote_main is None, "FastAPI not installed")
    def test_gpu_capabilities_publish_contract(self):
        caps = remote_main.capabilities_from_nodes({})
        self.assertEqual(caps["api_contract"], API_CONTRACT)
        self.assertEqual(caps["director_schema_sha256"], director_schema_sha256())

    @unittest.skipIf(remote_main is None, "FastAPI not installed")
    def test_gpu_refuses_a_newer_client_contract(self):
        async def exercise():
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/api/jobs", data={
                    "prompt": "<Picture 1> walks", "contract_version": str(API_CONTRACT + 1)})
                self.assertEqual(response.status_code, 422)
                self.assertIn("Обнови сервер", response.json()["detail"])
        asyncio.run(exercise())


@unittest.skipIf(httpx is None, "httpx not installed")
class WorkerCapabilitiesTests(unittest.TestCase):
    def test_capabilities_cached_per_process_for_a_minute(self):
        calls = []

        def handler(request):
            if request.url.path == "/api/capabilities":
                calls.append(request.url.path)
                return httpx.Response(200, json=CONTRACT_CAPS)
            return httpx.Response(202, json={"job_id": "remote-1"})

        with tempfile.TemporaryDirectory() as temp:
            store, job = _job(Path(temp))
            transport = HttpGpuTransport("http://gpu")
            with patch("httpx.AsyncClient", _client_with(handler)):
                with patch("local_studio.worker.time.monotonic", return_value=100.0):
                    asyncio.run(transport.submit(job, store))
                    asyncio.run(transport.submit(job, store))
                self.assertEqual(len(calls), 1)
                with patch("local_studio.worker.time.monotonic", return_value=100.0 + CAPABILITIES_TTL_S + 1):
                    asyncio.run(transport.submit(job, store))
                self.assertEqual(len(calls), 2)

    def test_capabilities_outage_is_not_cached_and_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            store, job = _job(Path(temp))
            transport = HttpGpuTransport("http://gpu")
            with patch("httpx.AsyncClient", _client_with(lambda r: httpx.Response(502, json={}))):
                with self.assertRaises(SubmitNotSent):
                    asyncio.run(transport.submit(job, store))
            self.assertIsNone(transport.cached_capabilities())

    def test_old_gpu_fails_the_job_with_update_message(self):
        posted = []

        def handler(request):
            if request.url.path == "/api/capabilities":
                return httpx.Response(200, json={"director_config_v1": True})
            if request.method == "POST":
                posted.append(request)
            return httpx.Response(404, json={"detail": "not found"})

        with tempfile.TemporaryDirectory() as temp:
            store, job = _job(Path(temp))
            with patch("httpx.AsyncClient", _client_with(handler)):
                asyncio.run(QueueWorker(store, HttpGpuTransport("http://gpu")).run_once())
            record = store.get_job(job["job_id"])
            self.assertEqual(record["state"], "failed")
            self.assertIn("Обнови сервер", record["error"])
            self.assertFalse(posted)


if __name__ == "__main__":
    unittest.main()
