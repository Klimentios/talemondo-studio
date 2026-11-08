import unittest
from unittest.mock import patch

import numpy as np

from app.camera_guide_vendor import renderer


class CameraRendererTests(unittest.TestCase):
    def test_locked_camera_renders_once_and_repeats_exact_frames(self):
        state = {"version": 1, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45, "focal_length_mm": 50,
             "aperture_f": 2, "focus_distance_m": 4},
            {"t": 2, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45, "focal_length_mm": 50,
             "aperture_f": 2, "focus_distance_m": 4},
        ]}
        progress = []
        with patch.object(renderer, "render_frame", wraps=renderer.render_frame) as render:
            frames = renderer.render_sequence(state, 24, 32, 4, 2, progress.append)
        self.assertEqual(render.call_count, 1)
        self.assertEqual(progress, [1, 2, 3, 4])
        self.assertEqual(frames.shape, (4, 32, 24, 3))
        self.assertTrue(np.array_equal(frames[0], frames[1]))
        self.assertTrue(np.array_equal(frames[1], frames[3]))

    def test_moving_camera_still_renders_every_frame(self):
        state = {"version": 1, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45, "focal_length_mm": 50,
             "aperture_f": 2, "focus_distance_m": 4},
            {"t": 2, "orbit": 25, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45, "focal_length_mm": 50,
             "aperture_f": 2, "focus_distance_m": 4},
        ]}
        with patch.object(renderer, "render_frame", wraps=renderer.render_frame) as render:
            frames = renderer.render_sequence(state, 24, 32, 4, 2)
        self.assertEqual(render.call_count, 4)
        self.assertEqual(frames.shape, (4, 32, 24, 3))


if __name__ == "__main__":
    unittest.main()
