"""prompt_blocks="none": the agent's text reaches H3 without Studio's generic blocks (audit/probe 05.11)."""
import json
import tempfile
import unittest
from pathlib import Path

from local_studio.store import StudioStore
from local_studio.worker import gpu_form_fields

GENERIC = ("AUDIO DIRECTION", "VOICE PERFORMANCE DIRECTION", "VISUAL DIRECTION", "HERO FACE FOCUS",
           "VIDEO PERFORMANCE DIRECTION", "VIDEO BODY DIRECTION", "CLEAN FRAME", "REFERENCE ROLE CONTRACT")
PROMPT = "<Picture 1> crosses the room following <Video 1>."


def _job(store: StudioStore, settings: dict, roles=("Лицо героя", "Движение")) -> dict:
    ids = []
    for name, kind in (("hero.png", "image"), ("scene.mp4", "video")):
        path = store.assets_dir / name
        path.write_bytes(b"fixture")
        ids.append(store.add_asset(kind=kind, role="Лицо героя" if kind == "image" else "Исходное видео",
                                   original_name=name, mime="image/png" if kind == "image" else "video/mp4",
                                   size=7, sha256=name, path=path)["asset_id"])
    return store.create_job(prompt=PROMPT, asset_ids=ids, settings=settings,
                            reference_numbers={ids[0]: 1, ids[1]: 1},
                            reference_roles={ids[0]: roles[0], ids[1]: roles[1]})


class CleanPromptTests(unittest.TestCase):
    def test_studio_route_is_unchanged_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            job = _job(StudioStore(Path(temp)), {"mode": "realistic"})
            self.assertIn("REFERENCE ROLE CONTRACT", job["compiled_prompt"])
            self.assertIn("VOICE PERFORMANCE DIRECTION", job["compiled_prompt"])
            self.assertNotIn("prompt_blocks", json.loads(job["settings_json"]))

    def test_clean_route_keeps_agent_text_first_and_only_factual_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            job = _job(StudioStore(Path(temp)), {"mode": "realistic", "prompt_blocks": "none"})
            compiled = job["compiled_prompt"]
            self.assertTrue(compiled.startswith(PROMPT), compiled[:200])
            for block in GENERIC:
                self.assertNotIn(block, compiled)
            self.assertIn("Reference roles: <Picture 1> defines this character's face", compiled)
            settings = json.loads(job["settings_json"])
            self.assertEqual(settings["prompt_blocks"], "none")
            self.assertFalse(settings["emotional_direction"])
            self.assertIsNone(settings.get("director_config"))
            self.assertIsNone(settings.get("camera_state"))
            self.assertEqual(gpu_form_fields(job)["prompt_blocks"], "none")

    def test_unknown_value_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "prompt_blocks"):
                _job(StudioStore(Path(temp)), {"mode": "realistic", "prompt_blocks": "some"})

    def test_job_without_emotional_flag_skips_the_gpu_qwen_director(self):
        with tempfile.TemporaryDirectory() as temp:
            job = _job(StudioStore(Path(temp)), {"mode": "realistic"})
            self.assertFalse(json.loads(job["settings_json"])["emotional_direction"])
            self.assertEqual(gpu_form_fields(job)["precompiled_prompt"], "true")


if __name__ == "__main__":
    unittest.main()
