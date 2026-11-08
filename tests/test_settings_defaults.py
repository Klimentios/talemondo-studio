"""One source of job defaults (app/contract.py DEFAULTS) for Studio UI, Mac store, worker and GPU form."""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import tempfile
import unittest
from pathlib import Path

from app.contract import ABSENT_EMOTIONAL_DIRECTION, DEFAULTS, GenerationRequest
from local_studio.store import StudioStore
from local_studio.worker import gpu_director_submission_options

ROOT = Path(__file__).resolve().parents[1]
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fe"
    "a7d6a4b00000000049454e44ae426082"
)


def _asset(store: StudioStore) -> dict:
    path = store.assets_dir / "one.png"
    path.write_bytes(PNG)
    return store.add_asset(kind="image", role="Референс", original_name="one.png", mime="image/png",
                           size=len(PNG), sha256=hashlib.sha256(PNG).hexdigest(), path=path)


class DefaultsTests(unittest.TestCase):
    def test_studio_ui_sends_the_contract_defaults(self):
        source = (ROOT / "preview/h3-director/model.mjs").read_text()
        project = source[source.index("export function createProject()"):]
        ui = {
            "mode": re.search(r"mode: '([^']+)'", project).group(1),
            "quality": re.search(r"quality: '([^']+)'", project).group(1),
            "megapixels": float(re.search(r"megapixels: ([0-9.]+)", project).group(1)),
            "steps": int(re.search(r"steps: (\d+)", project).group(1)),
            "ref_image_size": re.search(r"refImageSize: '([^']+)'", project).group(1),
            "cache_mode": re.search(r"cacheMode: '([^']+)'", project).group(1),
        }
        for key, value in ui.items():
            self.assertEqual(DEFAULTS[key], value, key)
        app = "\n".join(path.read_text() for path in sorted((ROOT / "preview/h3-director").glob("app*.mjs")))
        # Studio sends an agent-authored prompt directly; since 05.11 the contract default matches it
        # (DECISIONS 22.10: no GPU Qwen director), so a client that omits the field is not rewritten either.
        self.assertIn("emotional_direction: false", app)
        self.assertIs(DEFAULTS["emotional_direction"], False)

    def test_mac_store_fills_missing_settings_from_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            asset = _asset(store)
            job = store.create_job(prompt="<Picture 1> waves", asset_ids=[asset["asset_id"]], settings={})
            settings = json.loads(job["settings_json"])
            for key in ("quality", "megapixels", "mode", "steps", "cache_mode", "ref_image_size", "emotional_direction"):
                self.assertEqual(settings[key], DEFAULTS[key], key)
            expected = GenerationRequest("x", 5, "9:16", DEFAULTS["quality"], megapixels=DEFAULTS["megapixels"])
            self.assertEqual((settings["width"], settings["height"]), (expected.width, expected.height))

    def test_worker_and_gpu_form_use_the_same_defaults(self):
        source = (ROOT / "local_studio/worker.py").read_text()
        self.assertIn('settings.get("mode", DEFAULTS["mode"])', source)
        self.assertIn('settings.get("cache_mode", DEFAULTS["cache_mode"])', source)
        self.assertEqual(gpu_director_submission_options({})["emotional_direction"],
                         str(ABSENT_EMOTIONAL_DIRECTION).lower())
        try:
            from app import main as remote_main
        except ImportError:  # pragma: no cover
            self.skipTest("FastAPI not installed")
        params = inspect.signature(remote_main.create_job).parameters
        for key in ("quality", "mode", "steps", "cache_mode", "ref_image_size"):
            self.assertEqual(params[key].default.default, DEFAULTS[key], key)
        self.assertIs(params["emotional_direction"].default.default, ABSENT_EMOTIONAL_DIRECTION)


if __name__ == "__main__":
    unittest.main()
