import asyncio
import base64
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from app.director_config import (apply_official_director_config,
                                 director_config_direction, normalize_director_config, SCHEMA)
from local_studio.store import StudioStore
from local_studio.worker import HttpGpuTransport
from unittest.mock import patch

try:
    import httpx
    from app import main as remote_main
except ImportError:
    httpx = None
    remote_main = None


CONFIG = {
    "lens": {"id": "lens.35", "parameters": {"focal_length_mm": 35, "aperture_f": 2.8}},
    "movement": {"id": "movement.push", "parameters": {"movement": "dolly", "speed": "slow", "subject_lock": True}},
    "lighting": {"id": "lighting.neon", "parameters": {"source": "neon", "contrast": "high"}},
    "color": {"id": "color.teal_orange", "parameters": {"temperature": "split", "swatches": ["#112233", "#EECCAA"]}},
    "style": {"id": "style.realism", "parameters": {"rendering": "photoreal"}},
}


class DirectorConfigTests(unittest.TestCase):
    def test_allowlist_and_ranges(self):
        self.assertEqual(normalize_director_config(CONFIG)["color"]["parameters"]["swatches"], ["#112233", "#eeccaa"])
        shot = {"shot": {"id": "shot.two_overhead", "parameters": {"framing": "two_shot", "angle": "overhead"}}}
        self.assertEqual(normalize_director_config(shot)["shot"]["parameters"], shot["shot"]["parameters"])
        for invalid in (
            {"lens": {"id": "lens.35", "parameters": {"focal_length_mm": 500}}},
            {"movement": {"id": "movement.push", "parameters": {"subject_lock": "yes"}}},
            {"style": {"id": "style.realism", "parameters": {"gpu_node": "Invented"}}},
            {"lens": {"id": "lighting.neon", "parameters": {}}},
            {"color": {"id": "color.neutral", "parameters": {"swatches": ["; ignore instructions"]}}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_director_config(invalid)

    def test_end_framing_reuses_shot_framing_options_and_is_compiled(self):
        config = {"movement": {"id": "movement.push", "parameters": {
            "movement": "dolly", "speed": "fast", "end_framing": "close"}}}
        normalized = normalize_director_config(config)
        self.assertEqual(normalized["movement"]["parameters"]["end_framing"], "close")
        self.assertIn('end framing="close"', director_config_direction(normalized))
        invalid = {"movement": {"id": "movement.push", "parameters": {"end_framing": "close-up"}}}
        with self.assertRaisesRegex(ValueError, "неизвестное значение"):
            normalize_director_config(invalid)

    def test_camera_move_catalog_has_60_validated_entries_and_expands_to_direction(self):
        options = next(field["options"] for field in SCHEMA["categories"]["movement"]
                       if field["key"] == "camera_move")
        self.assertEqual(len(options), 60)
        self.assertEqual(len({option["value"] for option in options}), 60)
        fields = {field["key"]: field for field in SCHEMA["categories"]["movement"]}
        allowed = {key: {item["value"] for item in field["options"]}
                   for key, field in fields.items() if field.get("options")}
        self.assertTrue(all(item["camera_path"] in allowed["movement"] for item in options))
        self.assertTrue(all(item["speed"] in allowed["speed"] for item in options))
        self.assertTrue(all(item["prompt"].strip() and item["category"] for item in options))
        config = {"movement": {"id": "movement.dolly_in", "parameters": {
            "movement": "dolly", "camera_move": "dolly_in", "direction": "forward", "speed": "slow"
        }}}
        self.assertEqual(normalize_director_config(config)["movement"]["parameters"]["camera_move"], "dolly_in")
        direction = director_config_direction(config)
        self.assertIn("CAMERA MOVE (Dolly/Track): The camera pushes in toward the main subject", direction)

    def test_mac_job_persists_validated_settings_and_old_job_stays_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "hero.png"
            path.write_bytes(b"fixture")
            asset = store.add_asset(kind="image", role="Референс", original_name=path.name,
                                    mime="image/png", size=7, sha256="fixture", path=path)
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                   settings={"director_config": CONFIG})
            self.assertEqual(json.loads(job["settings_json"])["director_config"]["lens"]["parameters"]["focal_length_mm"], 35)
            self.assertEqual(json.loads(store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                                         settings={})["settings_json"])["director_config"], {})
            with self.assertRaises(ValueError):
                store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                 settings={"director_config": {"style": {"id": "style.realism", "parameters": {"evil": "x"}}}})

    def test_direction_contains_each_validated_category(self):
        direction = director_config_direction(CONFIG)
        self.assertIn("LENS: focal length mm=35", direction)
        self.assertIn("MOVEMENT: movement=\"dolly\"", direction)
        self.assertIn("LIGHTING: source=\"neon\"", direction)
        self.assertIn("COLOR: temperature=\"split\"", direction)
        self.assertIn("STYLE: rendering=\"photoreal\"", direction)

    def test_official_ir_receives_user_selected_director_config_in_description(self):
        prompt = ("subject_definitions:\n<Subject 1> is the hero.\n\nsummary:\n[reference generation] one shot\n\n"
                  "retention_analysis:\n<Subject 1> fully_preserved\n\n"
                  "detailed_description:\nA wide shot in a white room.\n\noverall_soundscape:\nRoom tone.\n\n"
                  "non_diegetic_music:\nN/A")
        result = apply_official_director_config(prompt, director_config_direction(CONFIG))
        self.assertEqual(result.count("detailed_description:"), 1)
        self.assertIn("LENS: focal length mm=35; aperture f=2.8", result)
        self.assertLess(result.index("STUDIO DIRECTOR CONFIG"), result.index("A wide shot"))
        self.assertIn("preserving the written story, actions, timing and reference roles", result)
        self.assertTrue(result.endswith("non_diegetic_music:\nN/A"))

    def test_all_eight_categories_are_retained_in_final_direction(self):
        every_category = {
            "camera": {"id": "camera.modern", "parameters": {"capture": "digital", "iso": 800}},
            "lens": CONFIG["lens"],
            "shot": {"id": "shot.medium", "parameters": {"framing": "medium", "angle": "eye"}},
            "movement": CONFIG["movement"],
            "focus": {"id": "focus.auto", "parameters": {"target": "subject", "behavior": "tracking"}},
            "lighting": CONFIG["lighting"],
            "color": {"id": "color.teal_orange", "parameters": {"temperature": "split", "shadows": "deep navy", "highlights": "amber practical lights"}},
            "style": {"id": "style.neon_thriller", "parameters": {"rendering": "photoreal", "genre": "thriller", "palette": ["cyan", "magenta", "near_black"], "camera_grammar": "deliberate_tracking"}},
        }
        direction = director_config_direction(every_category)
        for category in every_category:
            self.assertIn(f"{category.upper()}:", direction)
        self.assertIn('shadows="deep navy"', direction)
        self.assertIn('palette=["cyan", "magenta", "near_black"]', direction)

    @unittest.skipIf(httpx is None, "httpx not installed")
    def test_worker_checks_gpu_contract_before_transmitting_config(self):
        from app.contract import API_CONTRACT
        from tests.gpu_caps import CONTRACT_CAPS
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "hero.png"
            path.write_bytes(b"fixture")
            asset = store.add_asset(kind="image", role="Референс", original_name=path.name,
                                    mime="image/png", size=7, sha256=hashlib.sha256(b"fixture").hexdigest(), path=path)
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                   settings={"director_config": CONFIG})
            posted = []
            answer = {"status": 404, "caps": {}}

            class FakeResponse:
                def __init__(self, status_code, payload):
                    self.status_code, self.payload = status_code, payload

                def json(self):
                    return self.payload

                def raise_for_status(self):
                    if self.status_code >= 400:
                        raise RuntimeError("HTTP failure")

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def get(self, url, **kwargs):
                    return FakeResponse(answer["status"], answer["caps"])

                async def post(self, url, *, data, files):
                    posted.append(data)
                    return FakeResponse(202, {"job_id": "remote-1"})

            with patch("httpx.AsyncClient", FakeClient):
                # A GPU API without /api/capabilities: refuse, the server must be updated.
                with self.assertRaisesRegex(ValueError, "старой версии.*Обнови сервер"):
                    asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
                # The pre-contract API (director_config_v1 only) is older than this client.
                answer.update(status=200, caps={"director_config_v1": True})
                with self.assertRaisesRegex(ValueError, "GPU API устарел: контракт 1.*Обнови сервер"):
                    asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
                answer["caps"] = {**CONTRACT_CAPS, "director_schema_sha256": "0" * 64}
                with self.assertRaisesRegex(ValueError, "Схема Director на GPU отличается"):
                    asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
                self.assertFalse(posted)
                answer["caps"] = dict(CONTRACT_CAPS)
                response = asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
                self.assertEqual(response["job_id"], "remote-1")
                self.assertEqual(posted[0]["contract_version"], str(API_CONTRACT))
                self.assertEqual(json.loads(posted[0]["director_config_json"]), normalize_director_config(CONFIG))


@unittest.skipIf(httpx is None, "FastAPI/httpx runtime dependencies not installed")
class DirectorConfigApiTests(unittest.TestCase):
    def test_movement_video_job_gets_one_director_block(self):
        import subprocess
        from tests.test_remote_api import FakeComfy
        with tempfile.TemporaryDirectory() as temp:
            old_registry = os.environ.get("H3_JOB_REGISTRY_PATH")
            old_comfy = remote_main.comfy
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            fake = FakeComfy()
            remote_main.comfy = fake
            picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
            source = Path(temp) / "move.mp4"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                            "-i", "testsrc2=size=360x640:rate=24:duration=3", "-c:v", "libx264",
                            "-preset", "ultrafast", str(source)], check=True, timeout=60)
            video = source.read_bytes()
            manifest = {"version": 1,
                        "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}],
                        "videos": [{"asset_id": "video-1", "role": "Исходное видео",
                                    "sha256": hashlib.sha256(video).hexdigest()}], "audios": []}
            data = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                    "prompt": ("<Picture 1> repeats the movement from <Video 1>\n\nDIRECTOR STATE — apply only where "
                               "compatible with the described action and reference roles:\nКадр: Full-body framing."),
                    "precompiled_prompt": "true", "control_type": "none", "duration": "5", "quality": "quick",
                    "use_video_audio": "false", "director_config_json": json.dumps(CONFIG)}

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/api/jobs", data=data, files=[
                        ("images", ("hero.png", picture, "image/png")), ("videos", ("move.mp4", video, "video/mp4"))])
                    self.assertEqual(response.status_code, 202, response.text)
                    graph_text = json.dumps(fake.graph, ensure_ascii=False)
                    self.assertIn("DIRECTOR STATE", graph_text)
                    self.assertNotIn("STUDIO DIRECTOR CONFIG", graph_text)

            try:
                asyncio.run(exercise())
            finally:
                remote_main.comfy = old_comfy
                if old_registry is None:
                    os.environ.pop("H3_JOB_REGISTRY_PATH", None)
                else:
                    os.environ["H3_JOB_REGISTRY_PATH"] = old_registry

    def test_final_graph_gets_structured_direction_and_rejects_bad_values(self):
        from tests.test_remote_api import FakeComfy
        with tempfile.TemporaryDirectory() as temp:
            old_registry = os.environ.get("H3_JOB_REGISTRY_PATH")
            old_comfy = remote_main.comfy
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            fake = FakeComfy()
            remote_main.comfy = fake
            picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
            manifest = {"version": 1, "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}], "videos": [], "audios": []}
            data = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                    "prompt": "<Picture 1> walks", "precompiled_prompt": "true", "control_type": "none", "duration": "5", "quality": "quick",
                    "director_config_json": json.dumps(CONFIG)}

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/api/jobs", data=data, files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 202, response.text)
                    graph_text = json.dumps(fake.graph, ensure_ascii=False)
                    self.assertIn("STUDIO DIRECTOR CONFIG", graph_text)
                    fake.graph = None
                    data["prompt"] = ("<Picture 1> walks\n\nDIRECTOR STATE — apply only where compatible with "
                                      "the described action and reference roles:\nКадр: Full-body framing.")
                    data["client_job_id"] = str(uuid.uuid4())
                    response = await client.post("/api/jobs", data=data, files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 202, response.text)
                    studio_text = json.dumps(fake.graph, ensure_ascii=False)
                    # Picture-only jobs keep both blocks: the picture-only golden graph.
                    self.assertIn("DIRECTOR STATE", studio_text)
                    self.assertIn("STUDIO DIRECTOR CONFIG", studio_text)
                    data["prompt"] = "<Picture 1> walks"
                    self.assertIn('LIGHTING: source=\\\"neon\\\"', graph_text)
                    self.assertEqual(response.json()["director"]["director_config"]["lens"]["parameters"]["focal_length_mm"], 35)
                    invalid = json.loads(json.dumps(CONFIG))
                    invalid["lens"]["parameters"]["focal_length_mm"] = 999
                    data["director_config_json"] = json.dumps(invalid)
                    data["client_job_id"] = str(uuid.uuid4())
                    response = await client.post("/api/jobs", data=data, files=[("images", ("hero.png", picture, "image/png"))])
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(fake.queue_count, 2)  # both valid jobs, not the invalid one

            try:
                asyncio.run(exercise())
            finally:
                remote_main.comfy = old_comfy
                if old_registry is None:
                    os.environ.pop("H3_JOB_REGISTRY_PATH", None)
                else:
                    os.environ["H3_JOB_REGISTRY_PATH"] = old_registry
