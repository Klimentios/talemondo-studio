import os
import unittest
import os
from unittest.mock import patch
from unittest.mock import patch

from app.camera_guide_vendor.camera import focal_length_to_fov, parse_state, sample
from app.camera_guide_vendor.camera_prompt import compile_projected
from app.contract import GenerationRequest
from app.graph import build_prompt


class CameraOpticsTests(unittest.TestCase):
    def test_end_framing_geometry_reaches_compiled_h3_prompt(self):
        state = {"version": 2, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 2.4,
             "target": [0, 1.3, 0], "fov": focal_length_to_fov(50),
             "focal_length_mm": 50, "aperture_f": 4, "focus_distance_m": 2.4},
            {"t": 2.75, "orbit": 0, "elevation": 0, "distance": 1.3,
             "target": [0, 1.6, 0], "fov": focal_length_to_fov(50),
             "focal_length_mm": 50, "aperture_f": 4, "focus_distance_m": 1.3},
        ]}
        graph = build_prompt(GenerationRequest("A woman notices the viewer.", 5, "9:16", "max"),
                             ["hero.png"], camera_state=state)
        prompt = graph["136"]["inputs"]["prompt"]
        self.assertIn("physically push in from distance 2.4 to 1.3", prompt)
        self.assertIn("from [0.0, 1.3, 0.0] to [0.0, 1.6, 0.0]", prompt)

    @patch.dict(os.environ, {"H3_CAMERA_GUIDE_VIDEO": "1"})
    def test_subject_focus_tracks_eyes_during_dolly_without_rack_focus(self):
        state = {"version": 2, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": focal_length_to_fov(50),
             "focal_length_mm": 50, "aperture_f": 2, "focus_distance_m": 4},
            {"t": 4, "orbit": 0, "elevation": 0, "distance": 3,
             "target": [0, 1, 0], "fov": focal_length_to_fov(50),
             "focal_length_mm": 50, "aperture_f": 2, "focus_distance_m": 3},
        ]}
        graph = build_prompt(GenerationRequest("Two cats keep their identities.", 5, "9:16", "max"),
                             ["vega.png", "orion.png"], camera_state=state,
                             camera_optics={"focus_mode": "subject_head"})
        prompt = graph["136"]["inputs"]["prompt"]
        self.assertNotIn("rack focus", prompt.lower())
        self.assertIn("follow focus keeps the whole intended character, face included, continuously sharp", prompt)
        self.assertIn("shallow depth of field", prompt)
        self.assertIn("physically push in", prompt)
        self.assertIn("f/2", prompt)
        with patch.dict(os.environ, {"H3_CAMERA_GUIDE_VIDEO": "1"}):
            guide_graph = build_prompt(GenerationRequest("Two cats keep their identities.", 5, "9:16", "max"),
                                       ["vega.png", "orion.png"], camera_state=state,
                                       camera_optics={"focus_mode": "subject_head"})
        self.assertEqual(guide_graph["330"]["inputs"]["aperture_f"], 2)

    def test_focal_length_maps_to_physical_vertical_fov(self):
        self.assertAlmostEqual(focal_length_to_fov(50), 26.991, places=2)
        self.assertGreater(focal_length_to_fov(24), focal_length_to_fov(85))

    def test_optics_and_focus_are_interpolated_on_camera_keyframes(self):
        state = parse_state({"version": 2, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": focal_length_to_fov(35),
             "focal_length_mm": 35, "aperture_f": 1.8, "focus_distance_m": 2},
            {"t": 4, "orbit": 20, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": focal_length_to_fov(85),
             "focal_length_mm": 85, "aperture_f": 1.8, "focus_distance_m": 5},
        ]})
        midpoint = sample(state, 2)
        self.assertAlmostEqual(midpoint["focal_length_mm"], 60)
        self.assertAlmostEqual(midpoint["fov"], focal_length_to_fov(35) +
                               (focal_length_to_fov(85) - focal_length_to_fov(35)) * 0.5)
        self.assertEqual(midpoint["aperture_f"], 1.8)
        self.assertAlmostEqual(midpoint["focus_distance_m"], 3.5)

    def test_model_prompt_receives_lens_aperture_and_rack_focus(self):
        state = {"version": 2, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": focal_length_to_fov(35),
             "focal_length_mm": 35, "aperture_f": 1.8, "focus_distance_m": 2},
            {"t": 4, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": focal_length_to_fov(85),
             "focal_length_mm": 85, "aperture_f": 1.8, "focus_distance_m": 5},
        ]}
        prompt = compile_projected(state, frames=97, fps=24)
        self.assertIn("35 mm", prompt)
        self.assertIn("85 mm", prompt)
        self.assertIn("change focal length", prompt)
        self.assertIn("f/1.8", prompt)
        self.assertIn("rack focus", prompt.lower())
        self.assertIn("2 m", prompt)
        self.assertIn("5 m", prompt)


if __name__ == "__main__":
    unittest.main()


class SubjectFocusWordingTests(unittest.TestCase):
    def test_close_shot_names_the_eyes_and_aperture_sets_depth_of_field(self):
        from app.camera_guide_vendor.camera_prompt import subject_focus_direction
        close = {"distance": 1.2, "fov": focal_length_to_fov(85), "aperture_f": 1.8}
        wide = {"distance": 4.5, "fov": focal_length_to_fov(35), "aperture_f": 8}
        self.assertIn("subjects' eyes continuously sharp", subject_focus_direction(close))
        self.assertIn("shallow depth of field", subject_focus_direction(close))
        self.assertIn("whole intended character", subject_focus_direction(wide))
        self.assertIn("deep focus", subject_focus_direction(wide))
        self.assertIn("no focus hunting", subject_focus_direction(wide))


def test_every_director_schema_sensor_format_is_accepted():
    import json
    import pytest
    from app.camera_optics import normalize_camera_optics
    from app.contract import DIRECTOR_SCHEMA_PATH

    def fields(node):
        if isinstance(node, dict):
            if node.get("key") == "sensor_format":
                yield node
            for value in node.values():
                yield from fields(value)
        elif isinstance(node, list):
            for value in node:
                yield from fields(value)

    values = {option["value"] for field in fields(json.loads(DIRECTOR_SCHEMA_PATH.read_text()))
              for option in field["options"]}
    assert {"full-frame", "large format", "super_35"} <= values
    state = {"version": 2, "keys": [{"t": 0, "orbit": 0, "elevation": 0, "distance": 4, "target": [0, 1, 0],
                                     "fov": 40, "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 4}]}
    expected = {"full-frame": "full_frame", "large format": "full_frame", "super_35": "super_35"}
    for value in values:
        result = normalize_camera_optics({"sensor_format": value}, state)
        assert result["sensor_format"] == expected.get(value, result["sensor_format"])
        assert result["sensor_format"] in {"full_frame", "super_35", "micro_four_thirds"}
    with pytest.raises(ValueError):
        normalize_camera_optics({"sensor_format": "imax_70mm"}, state)
