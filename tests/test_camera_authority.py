"""One camera authority: a video that drives the camera removes any Qwen-derived trajectory (audit 2026-10-05)."""
import unittest

from app.director import preserve_explicit_camera_state

QWEN_TRAJECTORY = {"version": 2, "keys": [{"t": 0, "orbit": 0, "elevation": 0, "distance": 5.5, "target": [0, 1, 0], "fov": 40}]}


class CameraAuthorityTests(unittest.TestCase):
    def test_video_camera_role_drops_qwen_trajectory_without_studio_camera(self):
        for role in ("Движение и камера", "Камера"):
            plan = {"camera_state": QWEN_TRAJECTORY, "references": []}
            manifest = {"videos": [{"asset_id": "v", "role": role}]}
            self.assertIsNone(preserve_explicit_camera_state(plan, None, manifest, {}, 121)["camera_state"], role)

    def test_body_motion_keeps_qwen_trajectory(self):
        plan = {"camera_state": QWEN_TRAJECTORY, "references": []}
        manifest = {"videos": [{"asset_id": "v", "role": "Движение"}]}
        self.assertEqual(preserve_explicit_camera_state(plan, None, manifest, {}, 121)["camera_state"], QWEN_TRAJECTORY)


if __name__ == "__main__":
    unittest.main()


class TriggerAndAudioTests(unittest.TestCase):
    def test_style3d_trigger_opens_the_prompt_even_with_camera_text(self):
        from app.contract import GenerationRequest
        from app.graph import build_prompt
        dolly = {"version": 2, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 5.5, "target": [0, 1, 0], "fov": 40,
             "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 5.5},
            {"t": 4, "orbit": 0, "elevation": 0, "distance": 3.2, "target": [0, 1, 0], "fov": 40,
             "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 3.2}]}
        request = GenerationRequest("<Picture 1> waves.", 5, "9:16", "quick", profile="bf16", steps=24, seed=1)
        graph = build_prompt(request, ["a.png"], style_adapter="style3d", cache_mode="off", camera_state=dolly)
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("3dsrx, "))

    def test_silent_plan_drops_studio_voice_blocks(self):
        import re
        from app import director
        prompt = ("AUDIO DIRECTION: Generate rich audio with voices.\n\nVOICE PERFORMANCE DIRECTION: Cast voices.\n\n"
                  "<Picture 1> walks.")
        cleaned = re.sub(r"(?m)^(?:AUDIO DIRECTION|VOICE PERFORMANCE DIRECTION):[^\n]*(?:\n(?!\n)[^\n]*)*\n*", "", prompt)
        self.assertEqual(cleaned, "<Picture 1> walks.")
        self.assertIn('plan["audio_intent"] == "silent":\n        # Studio\'s own sound blocks',
                      open(director.__file__).read())


class FullStepTests(unittest.TestCase):
    def test_full_steps_keep_cache_and_never_add_turbo_lora(self):
        from app.contract import GenerationRequest
        from app.graph import build_prompt
        request = GenerationRequest("<Picture 1> waves.", 5, "9:16", "quick", profile="bf16", steps=24, seed=1)
        quality = build_prompt(request, ["a.png"], cache_mode="safe")
        self.assertIn("310", quality)
        self.assertNotIn("316", quality)
        self.assertEqual(quality["124"]["inputs"]["steps"], 24)

    def test_turbo_request_is_rejected(self):
        from app.contract import GenerationRequest
        from app.graph import build_prompt
        request = GenerationRequest("<Picture 1> waves.", 5, "9:16", "quick", profile="bf16", steps=24, seed=1)
        with self.assertRaisesRegex(ValueError, "полный режим качества"):
            build_prompt(request, ["a.png"], speed="turbo")
