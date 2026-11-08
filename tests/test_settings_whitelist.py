"""Audit 42.3C №12 and №32: unknown settings keys and a non-list guide_frames are 422s, not silent/500."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from local_studio.store import SETTINGS_KEYS, StudioStore

ROOT = Path(__file__).resolve().parents[1]


class SettingsWhitelistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StudioStore(Path(self.temp.name))
        path = self.store.assets_dir / "a.png"
        path.write_bytes(b"test")
        self.asset = self.store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png", size=4,
                                          sha256=hashlib.sha256(b"test").hexdigest(), path=path)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, settings):
        return self.store.create_job(prompt="<Picture 1> walks", asset_ids=[self.asset["asset_id"]], settings=settings)

    def test_unknown_key_is_rejected_with_its_name(self):
        with self.assertRaisesRegex(ValueError, "Неизвестные параметры генерации: durration"):
            self.create({"durration": 5})

    def test_saved_settings_can_be_resent_by_retry(self):
        studio = {"duration": 5, "aspect_ratio": "16:9", "quality": "custom", "mode": "realistic", "megapixels": 0.9,
                  "steps": 25, "ref_image_size": "max", "style_adapter": "none", "cache_mode": "safe",
                  "sampling_profile": "standard", "seed": None, "use_video_audio": False, "emotional_direction": True,
                  "camera_state": None, "guide_frames": [], "camera_optics": None, "director_config": None}
        saved = json.loads(self.create(studio)["settings_json"])
        self.assertLessEqual(set(saved), SETTINGS_KEYS)
        self.create(saved)  # «Повторить» resends settings_json as is

    def test_guide_frames_must_be_a_list(self):
        for value in (5, "1,0", {"1": 0}):
            with self.assertRaisesRegex(ValueError, "guide_frames"):
                self.create({"guide_frames": value})

    def test_every_settings_key_studio_sends_is_whitelisted(self):
        # settingsSnapshot() lives in app-generation.mjs since app.mjs was split into app-*.mjs parts.
        source = (ROOT / "preview/h3-director/app-generation.mjs").read_text()
        body = source[source.index("function settingsSnapshot()"):]
        body = body[body.index("return {"):body.index("};")]
        sent = set(re.findall(r"\b([a-z]+(?:_[a-z]+)*)\s*:", body)) - {"null", "auto", "none"}
        sent |= {"guide_frames", "camera_effect", "color_match", "color_grade", "film_look",
                 "allow_short_motion_reference", "character_refinement"}
        self.assertLessEqual(sent, SETTINGS_KEYS, sent - SETTINGS_KEYS)


if __name__ == "__main__":
    unittest.main()


def test_retry_of_an_old_job_drops_removed_feature_fields():
    from local_studio.store import LEGACY_SETTINGS_KEYS, SETTINGS_KEYS
    assert "pose_people" in LEGACY_SETTINGS_KEYS
    assert not (LEGACY_SETTINGS_KEYS & SETTINGS_KEYS)
