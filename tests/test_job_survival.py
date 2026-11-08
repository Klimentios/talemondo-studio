"""Jobs survive GPU API restarts; a ComfyUI restart becomes a clear terminal error on the Mac."""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
from app.comfy_client import ComfyClient
from app.job_registry import JobRegistry, recover_accepted
from local_studio.store import StudioStore
from local_studio.worker import GpuBackoff, QueueWorker

VIDEO = {"130": {"images": [{"filename": "h3_00001.mp4", "subfolder": "", "type": "output"}]}}


class FakeComfyUI:
    """ComfyUI HTTP surface: /history/{id} and /queue, with a hook between reads."""

    def __init__(self) -> None:
        self.history: dict = {}
        self.running: list = []
        self.pending: list = []
        self.on_queue_read = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/history/"):
            prompt_id = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={k: v for k, v in self.history.items() if k == prompt_id})
        if path == "/queue":
            body = {"queue_running": list(self.running), "queue_pending": list(self.pending)}
            if self.on_queue_read:
                self.on_queue_read()
            return httpx.Response(200, json=body)
        return httpx.Response(404)


def comfy_status(fake: FakeComfyUI, prompt_id: str) -> dict:
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real(*args, **kwargs)

    with patch("httpx.AsyncClient", client):
        return asyncio.run(ComfyClient("http://comfy").status(prompt_id))


def test_job_finishing_between_history_and_queue_reads_is_not_lost():
    """Evidence: job ed63781d ran 10.5 min, then "GPU потерял ID задания" at the moment it
    finished: /history was read just before completion, /queue just after."""
    fake = FakeComfyUI()  # first /history read: nothing yet; /queue read: no longer running
    reads = {"queue": 0}

    def finish_during_queue_read():
        reads["queue"] += 1
        fake.history["p1"] = {"status": {"status_str": "success", "messages": []}, "outputs": VIDEO}

    fake.on_queue_read = finish_during_queue_read
    result = comfy_status(fake, "p1")
    assert result["status"] == "success" and reads["queue"] == 1


def test_finished_without_mp4_and_interrupted_are_terminal_errors():
    fake = FakeComfyUI()
    fake.history["p2"] = {"status": {"status_str": "success", "messages": []}, "outputs": {}}
    fake.history["p3"] = {"status": {"status_str": "error", "messages": [
        ["execution_interrupted", {"prompt_id": "p3"}]]}, "outputs": {}}
    assert comfy_status(fake, "p2")["status"] == "error"
    assert comfy_status(fake, "p3") == {"status": "error", "error": "Генерация прервана на GPU"}
    assert comfy_status(fake, "missing") == {"status": "unknown"}


class StatusComfy:
    def __init__(self, answers: dict) -> None:
        self.answers = answers

    async def status(self, prompt_id):
        answer = self.answers.get(prompt_id, {"status": "unknown"})
        if isinstance(answer, BaseException):
            raise answer
        return answer


def test_accepted_row_is_recovered_from_comfy_by_planned_prompt_id(tmp_path):
    registry = JobRegistry(tmp_path / "r.sqlite3")
    planned = str(uuid.uuid4())
    registry.accept("c1", "h", planned_remote_id=planned, director_json='{"style": "x"}')
    record = registry.get("c1")
    # ComfyUI unreachable or the id unknown: the ambiguous receipt stays untouched.
    assert asyncio.run(recover_accepted(registry, StatusComfy({planned: OSError("down")}), record))["state"] == "accepted"
    assert asyncio.run(recover_accepted(registry, StatusComfy({}), record))["state"] == "accepted"
    recovered = asyncio.run(recover_accepted(registry, StatusComfy({planned: {"status": "running"}}), record))
    assert recovered["state"] == "queued" and recovered["remote_job_id"] == planned
    assert recovered["director_json"] == '{"style": "x"}'
    assert registry.owns_remote(planned)


def test_registry_migrates_old_table(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE client_jobs (client_job_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('accepted', 'queued')), remote_job_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("INSERT INTO client_jobs(client_job_id,request_hash,state,remote_job_id) VALUES ('a','h','queued','r')")
    db.commit()
    db.close()
    registry = JobRegistry(path)
    assert registry.get("a")["planned_remote_id"] is None
    assert registry.owns_remote("r")


def test_gpu_api_restart_mid_submit_and_comfy_restart(tmp_path, monkeypatch):
    from app import main as remote_main
    monkeypatch.setenv("H3_JOB_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    planned = str(uuid.uuid4())
    client_id = str(uuid.uuid4())
    # The old process accepted the job and posted it, then died before writing remote_job_id.
    remote_main._registry().accept(client_id, "hash", planned_remote_id=planned)
    fake = StatusComfy({planned: {"status": "running", "sampling_progress": None}})
    monkeypatch.setattr(remote_main, "comfy", fake)

    async def exercise():
        transport = httpx.ASGITransport(app=remote_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            receipt = (await client.get(f"/api/jobs/by-client/{client_id}")).json()
            assert receipt["job_id"] == planned and receipt["status"] == "queued"
            status = (await client.get(f"/api/jobs/{planned}")).json()
            assert status["status"] == "running"
            # ComfyUI restarted: its in-memory queue/history is gone.
            fake.answers.clear()
            status = (await client.get(f"/api/jobs/{planned}")).json()
            assert status["status"] == "unknown" and status["lost"] is True
            assert "ComfyUI" in status["error"]
            # A prompt id this registry never queued is not claimed as lost.
            other = (await client.get(f"/api/jobs/{uuid.uuid4()}")).json()
            assert "lost" not in other
            live = await client.get("/api/live")
            assert live.status_code == 200

    asyncio.run(exercise())


class PollGpu:
    def __init__(self, answers: list) -> None:
        self.answers = answers
        self.calls = 0

    async def status(self, remote_job_id):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def lookup(self, job_id):
        return None


def _running_job(root: Path) -> tuple[StudioStore, str]:
    store = StudioStore(root)
    path = store.assets_dir / "a.png"
    path.write_bytes(b"test")
    asset = store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png",
                            size=4, sha256="test", path=path)
    job = store.create_job(prompt="<Picture 1> scene", asset_ids=[asset["asset_id"]],
                           settings={})
    store.transition(job["job_id"], "uploading", expected_states=("queued_local",))
    store.transition(job["job_id"], "queued_gpu", remote_job_id="remote-1")
    return store, job["job_id"]


def test_mac_maps_lost_to_interrupted_with_gpu_message():
    with tempfile.TemporaryDirectory() as temp:
        store, job_id = _running_job(Path(temp))
        gpu = PollGpu([{"status": "unknown", "lost": True, "error": "ComfyUI перезапускался"}])
        asyncio.run(QueueWorker(store, gpu).run_once())
        job = store.get_job(job_id)
        assert job["state"] == "interrupted" and job["error"] == "ComfyUI перезапускался"


def test_mac_waits_for_repeated_unknown_from_old_api_and_resets_on_progress():
    with tempfile.TemporaryDirectory() as temp:
        store, job_id = _running_job(Path(temp))
        gpu = PollGpu([{"status": "unknown"}, {"status": "running"}, {"status": "unknown"},
                       {"status": "unknown"}, {"status": "unknown"}])
        worker = QueueWorker(store, gpu)
        for expected in ("queued_gpu", "running", "running", "running", "interrupted"):
            asyncio.run(worker.run_once())
            assert store.get_job(job_id)["state"] == expected


def test_backoff_caps_at_30_s_and_wakes_when_gpu_answers():
    with tempfile.TemporaryDirectory() as temp:
        store, job_id = _running_job(Path(temp))
        backoff = GpuBackoff()
        gpu = PollGpu([httpx.ConnectError("tunnel down")] * 8 + [{"status": "running"}])
        worker = QueueWorker(store, gpu, backoff)
        delays = []
        for _ in range(8):
            asyncio.run(worker.run_once())
            delays.append(backoff.delay())
        assert delays[:4] == [6.0, 12.0, 24.0, 30.0] and max(delays) == 30.0
        assert store.get_job(job_id)["state"] == "queued_gpu"  # remote id kept during outage
        asyncio.run(worker.run_once())
        assert backoff.delay() == 3.0 and store.get_job(job_id)["state"] == "running"


def test_backoff_wait_returns_early_on_ok():
    backoff = GpuBackoff()
    for _ in range(5):
        backoff.failed()

    async def exercise():
        loop = asyncio.get_running_loop()
        started = loop.time()
        loop.call_later(0.05, backoff.ok)
        await backoff.wait()
        return loop.time() - started

    assert asyncio.run(exercise()) < 5
