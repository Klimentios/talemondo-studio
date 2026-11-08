import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.color_match import lab_stats_from_image, lab_stats_from_rgb, normalize_color_match
from app.contract import GenerationRequest
from app.graph import build_prompt
from local_studio.store import StudioStore
from local_studio.worker import gpu_form_fields

ROOT = Path(__file__).resolve().parents[1]


def request():
    return GenerationRequest("<Picture 1> walks into <Picture 2>.", 5, "16:9", "quick", steps=24, seed=1)


class ColorMatchGraphTests(unittest.TestCase):
    MATCH = {"picture": 2, "strength": 0.6, "method": "mkl", "target_mean": [55.5, 3.2, -4.1], "target_std": [21.0, 6.5, 9.9]}

    def test_clip_wide_grade_runs_first_then_the_preset_grade(self):
        graph = build_prompt(request(), ["hero.png", "frame.png"], color_match=self.MATCH,
                             color_grade={"target_mean": [50, 0, 0], "target_std": [20, 10, 10], "strength": 0.5})
        node = graph["339"]
        self.assertEqual(node["class_type"], "H3ColorGrade")
        self.assertEqual(node["inputs"], {"images": ["122", 0], "target_l_mean": 55.5, "target_a_mean": 3.2,
                                          "target_b_mean": -4.1, "target_l_std": 21.0, "target_a_std": 6.5,
                                          "target_b_std": 9.9, "strength": 0.6, "preserve_luminance": False})
        self.assertEqual(graph["340"]["class_type"], "H3ColorGrade")
        self.assertEqual(graph["340"]["inputs"]["images"], ["339", 0])  # preset grade follows the scene match
        self.assertEqual(graph["130"]["inputs"]["images"], ["340", 0])

    def test_ab_variant_keeps_colormatch_v2(self):
        graph = build_prompt(request(), ["hero.png", "frame.png"], color_match=self.MATCH, color_match_impl="colormatch_v2")
        node = graph["339"]
        self.assertEqual(node["class_type"], "ColorMatchV2")
        self.assertEqual(node["inputs"]["image_target"], ["122", 0])
        self.assertEqual(graph[node["inputs"]["image_ref"][0]]["inputs"]["image"], "frame.png")

    def test_statistics_are_required_for_the_grade(self):
        with self.assertRaisesRegex(ValueError, "Lab"):
            build_prompt(request(), ["hero.png", "frame.png"], color_match={"picture": 2, "strength": 0.6, "method": "mkl"})

    def test_without_setting_the_graph_is_unchanged(self):
        self.assertEqual(build_prompt(request(), ["hero.png", "frame.png"]),
                         build_prompt(request(), ["hero.png", "frame.png"], color_match=None))
        self.assertNotIn("339", build_prompt(request(), ["hero.png", "frame.png"]))

    def test_values_are_validated(self):
        for bad in ({"picture": 3}, {"picture": 1, "strength": 0}, {"picture": 1, "method": "magic"}, {"strength": 1}):
            with self.assertRaises(ValueError):
                normalize_color_match(bad, 2)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("node"), "ffmpeg and node required")
class LabStatisticsTests(unittest.TestCase):
    def test_same_numbers_as_the_studio_photo_analysis(self):
        pixels = bytes((i * 37 + c * 91) % 256 for i in range(4000) for c in range(3))
        rgba = [value for i in range(0, len(pixels), 3) for value in (*pixels[i:i + 3], 255)]
        script = ("import('./preview/h3-director/photo-analyze.mjs').then(m => console.log(JSON.stringify("
                  f"m.labStatsFromPixels({{data: {json.dumps(rgba)}, width: 4000, height: 1}}))))")
        studio = json.loads(subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                                           capture_output=True, text=True, check=True).stdout)
        ours = lab_stats_from_rgb(pixels)
        self.assertEqual(ours, {"target_mean": studio["mean"], "target_std": studio["std"]})

    def test_picture_is_decoded_and_measured(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "red.png")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=0xff0000:s=600x300",
                            "-frames:v", "1", str(path)], check=True)
            stats = lab_stats_from_image(path.read_bytes())
        for value, red in zip(stats["target_mean"], (53.2, 80.1, 67.2)):  # sRGB red in Lab (via yuv: ±2)
            self.assertAlmostEqual(value, red, delta=2)
        self.assertLess(max(stats["target_std"]), 1)
        with self.assertRaisesRegex(ValueError, "образец"):
            lab_stats_from_image(b"not a picture")


class ColorMatchStoreTests(unittest.TestCase):
    def _store(self, temp, frame_role):
        store = StudioStore(Path(temp))
        ids = []
        for name, role in (("hero.png", "Персонаж целиком"), ("frame.png", frame_role)):
            path = store.assets_dir / name
            path.write_bytes(name.encode())
            ids.append(store.add_asset(kind="image", role=role, original_name=name, mime="image/png",
                                       size=len(name), sha256=hashlib.sha256(name.encode()).hexdigest(),
                                       path=path)["asset_id"])
        return store, ids

    def test_scene_frame_is_accepted_and_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids = self._store(temp, "Кадр сцены")
            job = store.create_job(prompt="<Picture 1> walks. <Picture 2> is the opening frame.", asset_ids=ids,
                                   reference_roles={ids[0]: "Персонаж целиком", ids[1]: "Кадр сцены"},
                                   settings={"quality": "quick", "color_match": {"picture": 2, "strength": 0.6}})
            settings = json.loads(job["settings_json"])
            self.assertEqual(settings["color_match"], {"picture": 2, "strength": 0.6, "method": "mkl"})
            self.assertEqual(json.loads(gpu_form_fields(job)["color_match_json"])["picture"], 2)

    def test_a_hero_photo_cannot_be_the_colour_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids = self._store(temp, "Лицо героя")
            with self.assertRaisesRegex(ValueError, "Образец цвета"):
                store.create_job(prompt="<Picture 1> walks. <Picture 2> face.", asset_ids=ids,
                                 reference_roles={ids[0]: "Персонаж целиком", ids[1]: "Лицо героя"},
                                 settings={"quality": "quick", "color_match": {"picture": 2}})

    def test_jobs_without_it_carry_no_field(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids = self._store(temp, "Окружение")
            job = store.create_job(prompt="<Picture 1> walks in <Picture 2>.", asset_ids=ids,
                                   reference_roles={ids[0]: "Персонаж целиком", ids[1]: "Окружение"},
                                   settings={"quality": "quick"})
            self.assertNotIn("color_match", json.loads(job["settings_json"]))
            self.assertNotIn("color_match_json", gpu_form_fields(job))


if __name__ == "__main__":
    unittest.main()


class ColorMatchCapabilityTests(unittest.TestCase):
    def test_gpu_without_the_node_refuses_before_upload(self):
        import asyncio
        from unittest.mock import patch
        import httpx
        from local_studio.worker import HttpGpuTransport, QueueWorker
        from tests.gpu_caps import CONTRACT_CAPS
        from tests.test_gpu_contract import _client_with
        posted = []

        def handler(request):
            if request.url.path == "/api/capabilities":
                return httpx.Response(200, json={**CONTRACT_CAPS, "color_match_v1": False})
            if request.method == "POST":
                posted.append(request)
            return httpx.Response(404, json={"detail": "not found"})

        with tempfile.TemporaryDirectory() as temp:
            store, ids = ColorMatchStoreTests()._store(temp, "Кадр сцены")
            job = store.create_job(prompt="<Picture 1> walks. <Picture 2> opens.", asset_ids=ids,
                                   reference_roles={ids[0]: "Персонаж целиком", ids[1]: "Кадр сцены"},
                                   settings={"quality": "quick", "color_match": {"picture": 2}})
            with patch("httpx.AsyncClient", _client_with(handler)):
                asyncio.run(QueueWorker(store, HttpGpuTransport("http://gpu")).run_once())
            record = store.get_job(job["job_id"])
            self.assertEqual(record["state"], "failed")
            self.assertIn("H3ColorGrade", record["error"])
            self.assertFalse(posted)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class ColorMatchGpuApiTests(unittest.TestCase):
    def test_gpu_api_measures_the_reference_and_queues_h3colorgrade(self):
        import asyncio
        import os
        import uuid
        from unittest.mock import patch
        import httpx
        from app import main as remote_main
        from tests.test_continuation import FakeComfy

        class Comfy(FakeComfy):
            async def object_info(self):
                return {"H3ColorGrade": {}, "ApplyMiniMaxH3FirstBlockCache": {}}

        with tempfile.TemporaryDirectory() as temp:
            hero, frame = Path(temp, "hero.png"), Path(temp, "frame.png")
            for path, color in ((hero, "gray"), (frame, "0x2040a0")):
                subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"color={color}:s=320x180",
                                "-frames:v", "1", str(path)], check=True)
            pictures = [hero.read_bytes(), frame.read_bytes()]
            manifest = {"version": 1, "videos": [], "audios": [], "images": [
                {"asset_id": f"a{i}", "sha256": hashlib.sha256(data).hexdigest()} for i, data in enumerate(pictures)]}
            form = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                    "prompt": "<Picture 1> walks. <Picture 2> opens.", "precompiled_prompt": "true",
                    "duration": "5", "quality": "quick", "seed": "1",
                    "color_match_json": json.dumps({"picture": 2, "strength": 0.6})}
            fake = Comfy()

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    return await client.post("/api/jobs", data=form, files=[
                        ("images", (f"p{i}.png", data, "image/png")) for i, data in enumerate(pictures)])

            with patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": f"{temp}/registry.sqlite3"}), \
                    patch.object(remote_main, "comfy", fake):
                response = asyncio.run(exercise())
        self.assertEqual(response.status_code, 202, response.text)
        node = fake.graph["339"]
        self.assertEqual(node["class_type"], "H3ColorGrade")
        self.assertEqual(node["inputs"]["strength"], 0.6)
        self.assertLess(node["inputs"]["target_b_mean"], -20)  # the blue reference frame, not the grey hero
        self.assertEqual(response.json()["director"]["color_match"]["picture"], 2)
