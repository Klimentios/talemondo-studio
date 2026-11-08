import os
import unittest
from unittest.mock import patch

from app.contract import GenerationRequest
from app.graph import STATIC_FRAMING_LOCK, build_prompt


def _state(distances):
    return {"version": 2, "keys": [{"t": t, "orbit": 0, "elevation": 0, "distance": d, "target": [0, 1.0, 0],
            "fov": 27, "focal_length_mm": 50, "aperture_f": 2, "focus_distance_m": d}
            for t, d in zip((0, 7.25), distances)]}


class FramingLockTests(unittest.TestCase):
    @patch.dict(os.environ, {"H3_FRAMING_LOCK": "1"})
    def test_static_camera_text_locks_the_shot_size_for_the_whole_clip(self):
        request = GenerationRequest("<Picture 1> dances like <Video 1>", 7, "9:16", "custom", steps=24, megapixels=0.67)
        graph = build_prompt(request, ["hero.png"], reference_videos=["dance.mp4"], camera_state=_state((4.2, 4.2)))
        self.assertIn(STATIC_FRAMING_LOCK, graph["136"]["inputs"]["prompt"])

    def test_moving_camera_is_not_locked(self):
        request = GenerationRequest("<Picture 1> walks", 7, "9:16", "custom", steps=24, megapixels=0.67)
        graph = build_prompt(request, ["hero.png"], camera_state=_state((4.5, 3.1)))
        self.assertNotIn(STATIC_FRAMING_LOCK, graph["136"]["inputs"]["prompt"])


    def test_framing_lock_is_off_by_default(self):
        request = GenerationRequest("<Picture 1> dances", 7, "9:16", "custom", steps=24, megapixels=0.67)
        with patch.dict(os.environ, {"H3_FRAMING_LOCK": ""}):
            graph = build_prompt(request, ["hero.png"], camera_state=_state((4.2, 4.2)))
        self.assertNotIn(STATIC_FRAMING_LOCK, graph["136"]["inputs"]["prompt"])
