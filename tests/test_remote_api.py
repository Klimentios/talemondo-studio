import asyncio
import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
import uuid
import wave
from unittest.mock import patch

try:
    import httpx
    from app import main as remote_main
except ImportError:
    httpx = None
    remote_main = None


class FakeComfy:
    def __init__(self):
        self.queue_count = 0
        self.graph = None

    async def release_models_if_idle(self):
        return None

    async def upload_file(self, filename, data, content_type):
        return filename

    async def queue(self, graph, prompt_id=None):
        self.queue_count += 1
        self.graph = graph
        return "comfy-job-1"

    async def object_info(self):
        return {"H3CameraGuide": {}, "H3OperatorCamera": {}, "ApplyMiniMaxH3FirstBlockCache": {}}


@unittest.skipIf(httpx is None, "FastAPI/httpx runtime dependencies not installed")
class RemoteApiTests(unittest.TestCase):
    @patch.dict(os.environ, {"H3_CAMERA_GUIDE_VIDEO": "1"})
    def test_explicit_optics_require_operator_node_before_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            old_registry = os.environ.get("H3_JOB_REGISTRY_PATH")
            old_comfy = remote_main.comfy
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            fake = FakeComfy()
            remote_main.comfy = fake
            picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
            manifest = {"version": 1, "images": [{"asset_id": "asset-1",
                        "sha256": hashlib.sha256(picture).hexdigest()}], "videos": [], "audios": []}
            state = {"version": 2, "keys": [{"t": t, "orbit": orbit, "elevation": 0,
                    "distance": 4, "target": [0, 1, 0], "fov": 39,
                    "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 4}
                    for t, orbit in ((0, 0), (4, 20))]}
            data = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                    "prompt": "<Picture 1> walks", "precompiled_prompt": "true",
                    "control_type": "none", "duration": "5", "quality": "quick",
                    "camera_state_json": json.dumps(state),
                    "camera_optics_json": json.dumps({"sensor_format": "super_35", "iso": 800})}

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/api/jobs", data=data,
                        files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(fake.graph["330"]["class_type"], "H3OperatorCamera")
                    self.assertEqual(fake.graph["330"]["inputs"]["iso"], 800.0)
                    fake.object_info = lambda: _missing_operator_node()
                    data["client_job_id"] = str(uuid.uuid4())
                    response = await client.post("/api/jobs", data=data,
                        files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 503, response.text)
                    self.assertIn("H3OperatorCamera", response.text)
                    self.assertEqual(fake.queue_count, 1)

            async def _missing_operator_node():
                return {"H3CameraGuide": {}, "ApplyMiniMaxH3FirstBlockCache": {}}

            try:
                asyncio.run(exercise())
            finally:
                remote_main.comfy = old_comfy
                if old_registry is None:
                    os.environ.pop("H3_JOB_REGISTRY_PATH", None)
                else:
                    os.environ["H3_JOB_REGISTRY_PATH"] = old_registry

    @patch.dict(os.environ, {"H3_CAMERA_GUIDE_VIDEO": "1"})
    def test_client_uuid_queues_only_once_and_validates_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            old_registry = os.environ.get("H3_JOB_REGISTRY_PATH")
            old_comfy = remote_main.comfy
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            fake = FakeComfy()
            remote_main.comfy = fake
            picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
            client_id = str(uuid.uuid4())
            manifest = {"version": 1, "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}],
                        "videos": [], "audios": []}
            data = {"client_job_id": client_id, "manifest_json": json.dumps(manifest),
                    "prompt": "<Picture 1> waves", "duration": "5", "quality": "quick",
                    "steps": "24", "seed": "42", "ref_image_size": "match",
                    "emotional_direction": "true",
                    "camera_state_json": json.dumps({"version": 1, "keys": [
                        {"t": 0, "orbit": 15, "elevation": 0, "distance": 4,
                         "target": [0, 1, 0], "fov": 45},
                        {"t": 4, "orbit": 35, "elevation": 0, "distance": 4,
                         "target": [0, 1, 0], "fov": 45}]})}

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    plan = {"schema_version": 1,
                            "references": [{"asset_id": "asset-1", "role": "identity",
                                            "fidelity": "fully_preserved", "visual_evidence": []}],
                            "style": "realism", "additional_direction": "",
                            "control": {"type": "none", "source_asset_id": None,
                                        "people": 1, "strength": 1.0},
                            "camera_state": None, "guide_frames": [],
                            "quality_intent": "final", "audio_intent": "native_stereo_effects_music"}
                    with patch.object(remote_main, "LocalQwenDirector") as director:
                        director.return_value.plan.return_value = plan
                        for _ in range(2):
                            response = await client.post("/api/jobs", data=data,
                                                         files=[("images", ("hero.png", picture, "image/png"))])
                            self.assertEqual(response.status_code, 202, response.text)
                            self.assertEqual(response.json()["job_id"], "comfy-job-1")
                    self.assertEqual(fake.queue_count, 1)
                    director.return_value.plan.assert_called_once()
                    self.assertTrue(director.return_value.plan.call_args.args[-1])
                    self.assertEqual(fake.graph["136"]["inputs"]["width"], 1216)
                    self.assertEqual(fake.graph["136"]["inputs"]["ref_image_size"], "match")
                    self.assertEqual(fake.graph["330"]["class_type"], "H3CameraGuide")
                    self.assertEqual(json.loads(fake.graph["330"]["inputs"]["camera_state"])
                                     ["keys"][0]["orbit"], 15)
                    bad = dict(data, prompt="<Picture 9> waves", client_job_id=str(uuid.uuid4()))
                    response = await client.post("/api/jobs", data=bad,
                                                 files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(fake.queue_count, 1)
                    lookup = await client.get(f"/api/jobs/by-client/{client_id}")
                    self.assertEqual(lookup.json()["job_id"], "comfy-job-1")

            try:
                asyncio.run(exercise())
            finally:
                remote_main.comfy = old_comfy
                if old_registry is None:
                    os.environ.pop("H3_JOB_REGISTRY_PATH", None)
                else:
                    os.environ["H3_JOB_REGISTRY_PATH"] = old_registry

    def test_precompiled_studio_prompt_queues_without_waking_director(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            old_registry = os.environ.get("H3_JOB_REGISTRY_PATH")
            old_comfy = remote_main.comfy
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            fake = FakeComfy()
            remote_main.comfy = fake
            picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
            audio_buffer = io.BytesIO()
            with wave.open(audio_buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(48000)
                wav.writeframes(b"\0\0" * 48000)
            audio = audio_buffer.getvalue()
            client_id = str(uuid.uuid4())
            manifest = {"version": 1,
                        "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}],
                        "videos": [], "audios": [{"asset_id": "asset-2", "sha256": hashlib.sha256(audio).hexdigest()}]}
            prompt = "Studio visual direction and scene prompt <Picture 1> with music <Audio 1>"
            data = {"client_job_id": client_id, "manifest_json": json.dumps(manifest),
                    "prompt": prompt, "precompiled_prompt": "true", "mode": "realistic",
                    "control_type": "none", "duration": "5", "quality": "quick",
                    "steps": "24", "seed": "42", "ref_image_size": "match"}

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    with patch.object(remote_main, "LocalQwenDirector", side_effect=AssertionError("Director must stay asleep")):
                        response = await client.post("/api/jobs", data=data,
                            files=[("images", ("hero.png", picture, "image/png")),
                                   ("audios", ("suno-track.wav", audio, "audio/wav"))])
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(fake.queue_count, 1)
                    self.assertIn(prompt, fake.graph["136"]["inputs"]["prompt"])
                    self.assertIn("<Audio 1>", fake.graph["136"]["inputs"]["prompt"])

            try:
                asyncio.run(exercise())
            finally:
                remote_main.comfy = old_comfy
                if old_registry is None:
                    os.environ.pop("H3_JOB_REGISTRY_PATH", None)
                else:
                    os.environ["H3_JOB_REGISTRY_PATH"] = old_registry

    def test_directed_job_without_installed_qwen_keeps_h3_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            old_registry = os.environ.get("H3_JOB_REGISTRY_PATH")
            old_comfy = remote_main.comfy
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            fake = FakeComfy()

            async def no_unload():
                raise AssertionError("H3 weights must stay loaded when Qwen is not installed")
            fake.release_models_if_idle = no_unload
            remote_main.comfy = fake
            picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
            manifest = {"version": 1, "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}],
                        "videos": [], "audios": []}
            data = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                    "prompt": "<Picture 1> waves", "duration": "5", "quality": "quick",
                    "steps": "24", "seed": "42", "ref_image_size": "match", "emotional_direction": "true"}

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    with patch.object(remote_main, "director_installed", return_value=False), \
                         patch.object(remote_main, "LocalQwenDirector", side_effect=AssertionError("no Qwen to call")):
                        response = await client.post("/api/jobs", data=data,
                                                     files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(fake.queue_count, 1)
                    self.assertIn("<Picture 1> waves", fake.graph["136"]["inputs"]["prompt"])

            try:
                asyncio.run(exercise())
            finally:
                remote_main.comfy = old_comfy
                if old_registry is None:
                    os.environ.pop("H3_JOB_REGISTRY_PATH", None)
                else:
                    os.environ["H3_JOB_REGISTRY_PATH"] = old_registry

    def test_removed_structural_control_is_rejected_with_clear_message(self):
        picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
        manifest = {"version": 1, "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}],
                    "videos": [], "audios": []}
        old_comfy = remote_main.comfy
        fake = FakeComfy()
        remote_main.comfy = fake

        async def exercise():
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                for control in ("pose", "canny"):
                    response = await client.post("/api/jobs", data={
                        "manifest_json": json.dumps(manifest), "prompt": "<Picture 1> walks",
                        "precompiled_prompt": "true", "control_type": control, "quality": "quick"},
                        files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertIn("ControlNet", response.json()["detail"])
                self.assertEqual(fake.queue_count, 0)

        try:
            asyncio.run(exercise())
        finally:
            remote_main.comfy = old_comfy


if __name__ == "__main__":
    unittest.main()
