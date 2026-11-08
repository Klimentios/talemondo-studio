"""/api/codex-frames: Codex draws one still from library pictures; the result is an ordinary image asset."""
import asyncio
import hashlib

import httpx
from fastapi import FastAPI

from local_studio import codex_frames, scene_frames
from local_studio.store import StudioStore

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def test_codex_frame_becomes_an_asset_and_failures_are_reported(tmp_path, monkeypatch):
    store = StudioStore(tmp_path)
    path = store.assets_dir / "place.png"
    path.write_bytes(PNG)
    place = store.add_asset(kind="image", role="Окружение", original_name="place.png", mime="image/png",
                            size=len(PNG), sha256=hashlib.sha256(PNG).hexdigest(), path=path)["asset_id"]
    asked = []

    async def draw(request, references, pictures, feedback=""):
        asked.append((request["prompt"], [ref["role"] for ref in references], [p.read_bytes() for p in pictures]))
        return PNG + b"world"

    monkeypatch.setattr(scene_frames, "draw", draw)
    app = FastAPI()
    app.include_router(codex_frames.make_router(store))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            job = (await client.post("/api/codex-frames", json={"prompt": "a candy world", "asset_ids": [place],
                                                                "role": "Окружение"})).json()
            assert job["state"] == "running"
            for _ in range(50):
                await asyncio.sleep(0.01)
                done = (await client.get(f"/api/codex-frames/{job['frame_id']}")).json()
                if done["state"] != "running":
                    break
            missing = (await client.post("/api/codex-frames", json={"prompt": "x", "asset_ids": ["nope"]})).json()
            await asyncio.sleep(0.05)
            failed = (await client.get(f"/api/codex-frames/{missing['frame_id']}")).json()
            lost = (await client.get("/api/codex-frames/codex-unknown")).json()
            return done, failed, lost

    done, failed, lost = asyncio.run(scenario())
    assert done["state"] == "completed" and store.get_asset(done["asset_id"])["role"] == "Окружение"
    assert asked == [("a candy world", ["Окружение"], [PNG])]
    assert failed["state"] == "failed" and "не найдена" in failed["error"]
    assert lost["state"] == "failed" and "перезапуском" in lost["error"]
