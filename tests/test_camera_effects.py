"""Official prompt embeddings: allow-listed camera_effect -> embedding:<name> in the prompt, file check."""
import asyncio
import base64
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.camera_effects import EFFECTS, REVISION, installed_effects, normalize_camera_effect, with_camera_effect
from app.contract import GenerationRequest
from app.graph import build_prompt
from local_studio.store import StudioStore
from local_studio.worker import gpu_form_fields

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")


class EffectTests(unittest.TestCase):
    def test_allow_list(self):
        self.assertEqual(normalize_camera_effect("bullet_time"), "bullet_time")
        for empty in (None, "", "none"):
            self.assertIsNone(normalize_camera_effect(empty))
        for bad in ("minimaxh3_bullet_time", "../x", 3):
            with self.assertRaisesRegex(ValueError, "camera_effect"):
                normalize_camera_effect(bad)
        self.assertEqual(installed_effects(["minimaxh3_bullet_time", "other"]), ["bullet_time"])

    def test_token_placement_follows_the_tokenizer_rule(self):
        self.assertEqual(with_camera_effect("<Picture 1> runs.", "bullet_time"),
                         "embedding:minimaxh3_bullet_time <Picture 1> runs.")
        ir = "subject_definitions:\nx\n\ndetailed_description:\nThe target video...\n"
        self.assertIn("detailed_description:\nembedding:minimaxh3_bullet_time The target", with_camera_effect(ir, "bullet_time"))
        self.assertTrue(with_camera_effect(ir, "bullet_time").startswith("subject_definitions:"))
        self.assertEqual(with_camera_effect("x", None), "x")

    def test_graph(self):
        request = GenerationRequest("<Picture 1> runs.", 5, "9:16", "compact", steps=24, seed=1)
        graph = build_prompt(request, ["hero.png"], camera_effect="kiss_camera")
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("embedding:minimaxh3_kiss_camera "))
        self.assertEqual(build_prompt(request, ["hero.png"]), build_prompt(request, ["hero.png"], camera_effect=None))

    def test_installer_pins_every_embedding_behind_the_flag(self):
        script = (ROOT / "scripts/install_h3_h200_overlay.sh").read_text()
        gate = script.index('if [[ "${H3_CAMERA_EFFECTS:-1}" == 1 ]]; then')
        end = script.index("\nfi\n", gate)
        for name, _size, sha in EFFECTS.values():
            at = script.index(f'"$comfy/models/embeddings/{name}.safetensors"')
            self.assertTrue(gate < at < end, name)
            self.assertIn(sha, script[at:at + 200])
        self.assertEqual(script[gate:end].count(REVISION), len(EFFECTS))
        self.assertNotIn('seedvr2_7b_fp16.safetensors', script)


class StoreAndWorkerTests(unittest.TestCase):
    def test_setting_is_validated_and_sent_only_when_set(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "hero.png"
            path.write_bytes(PNG)
            asset = store.add_asset(kind="image", role="Персонаж целиком", original_name="hero.png", mime="image/png",
                                    size=len(PNG), sha256=hashlib.sha256(PNG).hexdigest(), path=path)
            args = dict(prompt="<Picture 1> runs.", asset_ids=[asset["asset_id"]])
            job = store.create_job(**args, settings={"quality": "quick", "camera_effect": "bullet_time"})
            self.assertEqual(json.loads(job["settings_json"])["camera_effect"], "bullet_time")
            self.assertEqual(gpu_form_fields(job)["camera_effect"], "bullet_time")
            plain = store.create_job(**args, settings={"quality": "quick", "camera_effect": "none"})
            self.assertNotIn("camera_effect", json.loads(plain["settings_json"]))
            self.assertNotIn("camera_effect", gpu_form_fields(plain))
            with self.assertRaisesRegex(ValueError, "camera_effect"):
                store.create_job(**args, settings={"quality": "quick", "camera_effect": "warp"})

    def test_studio_exposes_only_effects_installed_on_the_gpu(self):
        from local_studio import main as mac_main
        with patch.object(mac_main.ctx, "gpu_url", "http://gpu"), patch.object(
            mac_main.ctx.gpu_transport, "capabilities", new=AsyncMock(return_value={
                "camera_effects_v1": {"available": ["bullet_time"]},
                "continuation_v1": True, "scene_guides": True,
            })
        ):
            result = TestClient(mac_main.app, base_url="http://127.0.0.1").get("/api/studio/capabilities")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["camera_effects_v1"]["available"], ["bullet_time"])
        self.assertTrue(result.json()["continuation_v1"])
        # an older GPU without the field: only the pair it always took
        self.assertEqual(result.json()["aspect_ratios"], ["16:9", "9:16"])

    def test_studio_reports_the_gpu_aspect_ratios(self):
        from local_studio import main as mac_main
        with patch.object(mac_main.ctx, "gpu_url", "http://gpu"), patch.object(
            mac_main.ctx.gpu_transport, "capabilities", new=AsyncMock(return_value={"aspect_ratios": ["16:9", "9:16", "4:3"]})
        ):
            result = TestClient(mac_main.app, base_url="http://127.0.0.1").get("/api/studio/capabilities")
        self.assertEqual(result.json()["aspect_ratios"], ["16:9", "9:16", "4:3"])


class GpuApiTests(unittest.TestCase):
    def test_missing_embedding_is_422_and_present_one_reaches_the_prompt(self):
        import httpx
        from app import main as remote_main
        from tests.test_continuation import FakeComfy

        class Comfy(FakeComfy):
            names: list = []

            async def embeddings(self):
                return self.names

        fake = Comfy()
        manifest = {"version": 1, "videos": [], "audios": [],
                    "images": [{"asset_id": "a", "sha256": hashlib.sha256(PNG).hexdigest()}]}

        async def post():
            form = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                    "prompt": "<Picture 1> runs.", "precompiled_prompt": "true", "duration": "5",
                    "quality": "quick", "seed": "1", "camera_effect": "bullet_time"}
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/jobs", data=form, files=[("images", ("hero.png", PNG, "image/png"))])

        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": f"{temp}/registry.sqlite3"}), \
                patch.object(remote_main, "comfy", fake):
            missing = asyncio.run(post())
            fake.names = ["minimaxh3_bullet_time"]
            ok = asyncio.run(post())
        self.assertEqual(missing.status_code, 422)
        self.assertIn("H3_CAMERA_EFFECTS", missing.json()["detail"])
        self.assertEqual(ok.status_code, 202, ok.text)
        self.assertTrue(fake.graph["136"]["inputs"]["prompt"].startswith("embedding:minimaxh3_bullet_time "))
        caps = remote_main.capabilities_from_nodes({}, ["minimaxh3_truman_show"])["camera_effects_v1"]
        self.assertEqual(caps["available"], ["truman_show"])


if __name__ == "__main__":
    unittest.main()
