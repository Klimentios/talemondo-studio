"""Official-graph A/B variants: 768p, FirstBlockCache off, camera words, ColorMatchV2, SeedVR2 7B."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from app.ab_variant import apply_ab_variant, parse_ab_variant
from app.contract import GenerationRequest
from app.graph import build_prompt
from app.upscale_graph import build_upscale_graph, required_weights, seedvr2_models_available
from tests.test_camera_words import DOLLY

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("h3_ab", ROOT / "scripts/h3_ab.py")
h3_ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h3_ab)


def variant(**fields):
    return parse_ab_variant("ab", "", "", "", "", "", **fields)[0]


def graph(aspect="9:16"):
    request = GenerationRequest("<Picture 1> walks.", 5, aspect, "custom", steps=25, seed=1, megapixels=0.7)
    return build_prompt(request, ["hero.png"], cache_mode="fast", camera_state=DOLLY)


class VariantTests(unittest.TestCase):
    def test_validation_and_absent_fields(self):
        self.assertEqual(parse_ab_variant("", "", "", "", "", ""), (None, None))
        for fields in ({"resolution": "1080p"}, {"cache": "fast"}, {"camera_words": "yes"}, {"color_match": "mkl"}):
            with self.assertRaises(ValueError):
                variant(**fields)
        self.assertNotIn("resolution", variant().describe())  # old A/B fingerprints keep their shape

    def test_768p_native_matching_aspect(self):
        for aspect, size in (("9:16", (768, 1344)), ("16:9", (1344, 768))):
            ref = apply_ab_variant(graph(aspect), variant(resolution="768p"))["136"]["inputs"]
            self.assertEqual((ref["width"], ref["height"]), size)

    def test_cache_off_removes_first_block_cache(self):
        base = graph()
        self.assertIn("310", base)
        result = apply_ab_variant(base, variant(cache="off"))
        self.assertNotIn("310", result)
        self.assertEqual(result["124"]["inputs"]["model"], ["127", 0])
        self.assertEqual(result["126"]["inputs"]["model"], ["127", 0])

    def test_build_overrides(self):
        self.assertEqual(variant(camera_words="on").build_overrides(), {"camera_words": True})
        cm = variant(color_match="colormatch_v2")
        self.assertEqual(cm.build_overrides(), {"color_match_impl": "colormatch_v2"})
        self.assertIn("ColorMatchV2", cm.required_nodes())
        self.assertEqual(variant(resolution="768p").build_overrides(), {})


class SeedVR2Tests(unittest.TestCase):
    def test_7b_graph_and_weights(self):
        self.assertEqual(build_upscale_graph("a.mp4", "j", 24.0)["7"]["inputs"]["unet_name"], "seedvr2_3b_fp16.safetensors")
        self.assertEqual(build_upscale_graph("a.mp4", "j", 24.0, model="7b")["7"]["inputs"]["unet_name"],
                         "seedvr2_7b_fp16.safetensors")
        self.assertIn(("models/diffusion_models/seedvr2_7b_fp16.safetensors", "SeedVR2"), required_weights("upscale", "7b"))
        with self.assertRaises(ValueError):
            build_upscale_graph("a.mp4", "j", 24.0, model="13b")
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp, "models/diffusion_models")
            models.mkdir(parents=True)
            (models / "seedvr2_3b_fp16.safetensors").write_bytes(b"x")
            self.assertEqual(seedvr2_models_available(temp), ["3b"])
            (models / "seedvr2_7b_fp16.safetensors").write_bytes(b"x")
            self.assertEqual(seedvr2_models_available(temp), ["3b", "7b"])


class HarnessTests(unittest.TestCase):
    def test_variants_send_only_their_option(self):
        job, _ = h3_ab.load_golden_job()
        for name, expected in (("768p", {"ab_resolution": "768p"}), ("cache-off", {"ab_cache": "off"}),
                               ("camera-words", {"ab_camera_words": "on"}),
                               ("colormatch-v2", {"ab_color_match": "colormatch_v2"})):
            fields = h3_ab.variant_fields(job, name, None)
            self.assertEqual({k: v for k, v in fields.items() if k.startswith("ab_")}, {"ab_variant": name, **expected})

    def test_upscale_needs_a_finished_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            h3_ab.save(Path(temp), {"created": "t", "tag": "", "gpu_url": "u", "source": "golden", "settings": {},
                                    "variants": [{"index": 1, "name": "baseline", "status": "error"}]})
            with self.assertRaisesRegex(SystemExit, "baseline"):
                h3_ab.main(["upscale", temp, "--models", "3b,7b"])
            with self.assertRaisesRegex(SystemExit, "SeedVR2"):
                h3_ab.main(["upscale", temp, "--models", "13b"])


if __name__ == "__main__":
    unittest.main()


class GpuApiTests(unittest.TestCase):
    def test_camera_words_variant_keeps_the_base_fingerprint(self):
        import asyncio, base64, hashlib, json, os, uuid
        from unittest.mock import patch
        import httpx
        from app import main as remote_main
        from tests.test_continuation import FakeComfy
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
        manifest = {"version": 1, "videos": [], "audios": [],
                    "images": [{"asset_id": "a", "sha256": hashlib.sha256(png).hexdigest()}]}
        class Comfy(FakeComfy):
            async def object_info(self):
                return {"H3CameraGuide": {}, "ApplyMiniMaxH3FirstBlockCache": {}}

            def track_vram(self, prompt_id):
                return None

        fake = Comfy()

        async def post(**extra):
            form = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                    "prompt": "<Picture 1> walks.", "precompiled_prompt": "true", "duration": "5",
                    "quality": "quick", "seed": "1", "camera_state_json": json.dumps(DOLLY), **extra}
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/jobs", data=form, files=[("images", ("hero.png", png, "image/png"))])

        with tempfile.TemporaryDirectory() as temp, \
                patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": f"{temp}/registry.sqlite3"}), \
                patch.object(remote_main, "comfy", fake):
            first = asyncio.run(post(ab_variant="baseline"))
            self.assertEqual(first.status_code, 202, first.text)
            base = first.json()["director"]["ab"]
            words = asyncio.run(post(ab_variant="camera-words", ab_camera_words="on", ab_resolution="768p"))
            bad = asyncio.run(post(ab_variant="cm", ab_color_match="h3colorgrade"))
        self.assertEqual(words.status_code, 202, words.text)
        ab = words.json()["director"]["ab"]
        self.assertEqual(ab["base_prompt_sha256"], base["base_prompt_sha256"])
        self.assertTrue(ab["prompt"].startswith("The camera pushes in."))
        self.assertEqual((fake.graph["136"]["inputs"]["width"], fake.graph["136"]["inputs"]["height"]), (1344, 768))
        self.assertEqual(bad.status_code, 422)
