import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from local_studio.store import StudioStore
from local_studio.worker import HttpGpuTransport, QueueWorker


class CancelDuringPollGpu:
    """status() lets the user cancel while the worker awaits the GPU answer."""

    def __init__(self, store, answer):
        self.store, self.answer, self.job_id = store, answer, None

    async def status(self, remote_job_id):
        self.store.transition(self.job_id, "stopping")
        return self.answer


class WorkerCancelRaceTests(unittest.TestCase):
    def _queued(self, root):
        store = StudioStore(root)
        path = store.assets_dir / "a.png"
        path.write_bytes(b"test")
        asset = store.add_asset(kind="image", role="hero", original_name="a.png",
                                mime="image/png", size=4, sha256="test", path=path)
        job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
        store.transition(job["job_id"], "queued_gpu", remote_job_id="remote-1")
        return store, job["job_id"]

    def test_cancel_during_status_poll_is_not_overwritten(self):
        for answer in ({"status": "running"}, {"status": "success"}, {"status": "error", "error": "x"}):
            with tempfile.TemporaryDirectory() as temp:
                store, job_id = self._queued(Path(temp))
                gpu = CancelDuringPollGpu(store, answer)
                gpu.job_id = job_id
                asyncio.run(QueueWorker(store, gpu).run_once())
                self.assertEqual(store.get_job(job_id)["state"], "stopping", answer)

    def test_queue_tunnel_down_falls_back_to_gpu_api(self):
        def handler(request):
            if request.url.port == 18191:
                raise httpx.ConnectError("queue tunnel down", request=request)
            if request.url.path == "/api/capabilities":
                return httpx.Response(200, json={})
            if request.url.path == "/api/jobs/remote-1":
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        httpx.AsyncClient = client
        try:
            gpu = HttpGpuTransport("http://127.0.0.1:18189")
            self.assertEqual(gpu.queue_url, "http://127.0.0.1:18191")
            self.assertEqual(asyncio.run(gpu.status("remote-1")), {"status": "running"})
            self.assertIsNone(asyncio.run(gpu.lookup("client-1")))
        finally:
            httpx.AsyncClient = real


if __name__ == "__main__":
    unittest.main()
