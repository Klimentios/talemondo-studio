import asyncio
import tempfile
import unittest
from pathlib import Path

try:
    import httpx
    from local_studio import main as local_main
except ImportError:
    httpx = None
    local_main = None

from local_studio.store import StudioStore


@unittest.skipIf(httpx is None, "FastAPI/httpx runtime dependencies not installed")
class LocalApiTests(unittest.TestCase):
    def test_rejects_foreign_host_and_origin_with_403(self):
        async def exercise():
            transport = httpx.ASGITransport(app=local_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                for headers in ({"Host": "evil.example"}, {"Origin": "http://evil.example"}):
                    response = await client.get("/api/health", headers=headers)
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertIn("detail", response.json())

        asyncio.run(exercise())

    def test_import_draft_queue_and_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            original = local_main.ctx.store
            local_main.ctx.store = StudioStore(Path(temp))

            async def exercise():
                transport = httpx.ASGITransport(app=local_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                    response = await client.post("/api/assets", files={"file": ("hero.png", b"\x89PNG\r\n\x1a\n" + b"x" * 100, "image/png")})
                    self.assertEqual(response.status_code, 201, response.text)
                    asset = response.json()
                    self.assertEqual(asset["number"], 1)
                    audio_bytes = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"x" * 100
                    audio_response = await client.post("/api/assets", files={
                        "file": ("suno-track.wav", audio_bytes, "audio/wav")
                    })
                    self.assertEqual(audio_response.status_code, 201, audio_response.text)
                    audio = audio_response.json()
                    response = await client.put("/api/draft", json={"project": {"prompt": "draft"}, "expected_revision": 0})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual((await client.get("/api/draft")).json()["project"]["prompt"], "draft")
                    response = await client.post("/api/jobs", json={
                        "prompt": "<Picture 1> waves", "asset_ids": [asset["asset_id"]],
                        "reference_numbers": {asset["asset_id"]: 1},
                        "settings": {"quality": "quick"},
                    })
                    self.assertEqual(response.status_code, 202, response.text)
                    job = response.json()
                    self.assertEqual(job["state"], "queued_local")
                    receipt_payload = {"prompt": "<Picture 1> waves", "asset_ids": [asset["asset_id"]],
                                       "settings": {}, "client_request_id": "scene-receipt-test"}
                    accepted = await client.post("/api/jobs", json=receipt_payload)
                    repeated = await client.post("/api/jobs", json=receipt_payload)
                    self.assertEqual(accepted.status_code, 202, accepted.text)
                    self.assertEqual(accepted.json()["job_id"], repeated.json()["job_id"])
                    receipt = await client.get("/api/job-receipts/scene-receipt-test")
                    self.assertEqual(receipt.json()["job_id"], accepted.json()["job_id"])
                    conflict = await client.post("/api/jobs", json={**receipt_payload, "prompt": "<Picture 1> sits"})
                    self.assertEqual(conflict.status_code, 422)
                    self.assertEqual(len((await client.get("/api/jobs")).json()), 2)
                    audio_job = await client.post("/api/jobs", json={
                        "prompt": "<Picture 1> with music from <Audio 1>",
                        "asset_ids": [asset["asset_id"], audio["asset_id"]],
                        "reference_numbers": {asset["asset_id"]: 1, audio["asset_id"]: 1},
                        "reference_roles": {audio["asset_id"]: "Музыка"},
                        "settings": {"quality": "quick"},
                    })
                    self.assertEqual(audio_job.status_code, 202, audio_job.text)
                    self.assertIn("<Audio 1>", audio_job.json()["compiled_prompt"])
                    page = await client.get("/")
                    self.assertEqual(page.status_code, 200)
                    self.assertNotIn("voiceRights", page.text)
                    missing = await client.post("/api/jobs", json={
                        "prompt": "<Picture 9>", "asset_ids": [asset["asset_id"]],
                        "reference_numbers": {asset["asset_id"]: 1}, "settings": {},
                    })
                    self.assertEqual(missing.status_code, 422)
                    await client.put("/api/draft", json={"project": {"references": [], "cuts": [{"prompt": ""}]}, "expected_revision": 1})
                    self.assertEqual(len((await client.get("/api/jobs")).json()), 3)

            try:
                asyncio.run(exercise())
                self.assertEqual(StudioStore(Path(temp)).list_jobs()[0]["state"], "queued_local")
            finally:
                local_main.ctx.store = original

    def test_draft_rejects_stale_writer_and_preserves_saved_project(self):
        with tempfile.TemporaryDirectory() as temp:
            original = local_main.ctx.store
            local_main.ctx.store = StudioStore(Path(temp))

            async def exercise():
                transport = httpx.ASGITransport(app=local_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                    first_read = (await client.get("/api/draft")).json()
                    second_read = (await client.get("/api/draft")).json()
                    self.assertEqual(first_read["revision"], second_read["revision"])
                    first = await client.put("/api/draft", json={"project": {"prompt": "first"}, "expected_revision": first_read["revision"]})
                    self.assertEqual(first.status_code, 200, first.text)
                    second = await client.put("/api/draft", json={"project": {"prompt": "second"}, "expected_revision": second_read["revision"]})
                    self.assertEqual(second.status_code, 409, second.text)
                    saved = (await client.get("/api/draft")).json()
                    self.assertEqual(saved["project"]["prompt"], "first")
                    self.assertEqual(saved["revision"], first.json()["revision"])

            try:
                asyncio.run(exercise())
            finally:
                local_main.ctx.store = original

    def test_agent_shot_proposal_updates_saved_draft_once(self):
        with tempfile.TemporaryDirectory() as temp:
            original = local_main.ctx.store
            local_main.ctx.store = StudioStore(Path(temp))

            async def exercise():
                transport = httpx.ASGITransport(app=local_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                    project = {"activeSceneId": "s1", "cuts": [{"id": "c1", "prompt": "old"}],
                               "scenes": [{"id": "s1", "cuts": [{"id": "c1", "prompt": "old"}]}]}
                    saved = await client.put("/api/draft", json={"project": project, "expected_revision": 0})
                    self.assertEqual(saved.status_code, 200, saved.text)
                    path = "/api/draft/scenes/s1/shots/c1/proposals"
                    proposal = {"event_id": "evt-1", "agent_role": "director", "base_revision": 1,
                                "patch": {"prompt": "new"}}
                    first = await client.post(path, json=proposal)
                    self.assertEqual(first.status_code, 200, first.text)
                    self.assertEqual(first.json(), {"revision": 2, "replayed": False})
                    local_main.ctx.store = StudioStore(Path(temp))
                    replay = await client.post(path, json=proposal)
                    self.assertEqual(replay.json(), {"revision": 2, "replayed": True})
                    self.assertEqual((await client.post(path, json={**proposal, "event_id": "evt-2"})).status_code, 409)
                    self.assertEqual((await client.post(path, json={**proposal, "event_id": "evt-1",
                                                                 "patch": {"prompt": "other"}})).status_code, 409)
                    self.assertEqual((await client.post(path, json={**proposal, "event_id": "evt-3",
                                                                 "agent_role": "viewer", "base_revision": 2})).status_code, 403)
                    self.assertEqual((await client.post(path, json={**proposal, "event_id": "evt-4",
                                                                 "base_revision": 2, "patch": {"shotJobId": "x"}})).status_code, 422)
                    missing = await client.post("/api/draft/scenes/s1/shots/missing/proposals",
                                                json={**proposal, "event_id": "evt-5", "base_revision": 2})
                    self.assertEqual(missing.status_code, 422)
                    draft = (await client.get("/api/draft")).json()
                    self.assertEqual(draft["revision"], 2)
                    self.assertEqual(draft["project"]["cuts"][0]["prompt"], "new")
                    self.assertEqual(draft["project"]["scenes"][0]["cuts"][0]["prompt"], "new")
                    replaced = (await client.get("/api/draft/history/1")).json()["project"]
                    self.assertEqual(replaced["scenes"][0]["cuts"][0]["prompt"], "old")  # the agent edit keeps it
                    with local_main.ctx.store.connect() as db:
                        self.assertIsNone(db.execute("SELECT 1 FROM agent_event_receipts WHERE event_id='evt-5'").fetchone())
                    self.assertEqual((await client.put("/api/draft", json={"project": project,
                                                                           "expected_revision": 1})).status_code, 409)

            try:
                asyncio.run(exercise())
            finally:
                local_main.ctx.store = original



if __name__ == "__main__":
    unittest.main()
