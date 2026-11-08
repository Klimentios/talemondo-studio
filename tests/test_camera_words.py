"""Official camera vocabulary (app/camera_words.py), the scenarist CAMERA line and IR prompts in graph.py."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import scenarist
from app.camera_words import camera_phrase, camera_phrase_from_state
from app.contract import GenerationRequest
from app.graph import build_prompt
from app.scenarist import ScenaristRequest, camera_lines, write_ir
from tests.test_scenarist import FakeDirector

DOLLY = {"version": 2, "keys": [
    {"t": 0, "orbit": 0, "elevation": 0, "distance": 5.5, "target": [0, 1, 0], "fov": 40,
     "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 5.5},
    {"t": 4.5, "orbit": 0, "elevation": 0, "distance": 3.2, "target": [0, 1, 0], "fov": 40,
     "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 3.2},
]}
IR = ("subject_definitions:\n<Subject 1> is the hero in <Picture 1>.\n\nsummary:\n[reference generation] x\n\n"
      "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - x\n\n"
      "detailed_description:\nThe target video is live action.\n[Shot 1] The camera pushes in.\n\n"
      "overall_soundscape:\nRoom tone.\n\nnon_diegetic_music:\nN/A")


class CameraPhraseTests(unittest.TestCase):
    def test_official_words_amplitude_and_speed(self):
        self.assertEqual(camera_phrase("push", "in", "slow", "small"),
                         "The camera pushes in with small amplitude at slow speed.")
        self.assertEqual(camera_phrase("pan", "right", "fast", "large"),
                         "The camera pans right with large amplitude at fast speed.")
        self.assertEqual(camera_phrase("truck", "left"), "The camera trucks left.")
        self.assertEqual(camera_phrase("pedestal", "up", "normal", "medium"), "The camera pedestals up.")
        self.assertEqual(camera_phrase("static", speed="fast"), "The camera holds a static shot.")
        self.assertEqual(camera_phrase("shake", "strong"), "The camera shakes strongly.")
        self.assertEqual(camera_phrase("roll", "clockwise"), "The camera rolls clockwise.")
        self.assertEqual(camera_phrase("zoom_in", amplitude="large"), "The camera zooms in with large amplitude.")
        self.assertIn("tracking shot", camera_phrase("tracking"))
        self.assertIn("arc", camera_phrase("arc", "left"))

    def test_unknown_values_are_refused_in_russian(self):
        for args in (("fly",), ("pan", "up"), ("push", "in", "warp"), ("tilt", "up", "", "huge")):
            with self.assertRaisesRegex(ValueError, "camera"):
                camera_phrase(*args)

    def test_trajectory_becomes_words(self):
        self.assertEqual(camera_phrase_from_state(DOLLY, 124),
                         "The camera pushes in.")
        orbit = {"version": 1, "keys": [{"t": 0, "orbit": 0, "elevation": 0, "distance": 4, "target": [0, 1, 0], "fov": 45},
                                        {"t": 5, "orbit": -20, "elevation": 12, "distance": 4, "target": [0, 1, 0], "fov": 45}]}
        text = camera_phrase_from_state(orbit, 124)
        self.assertIn("arcs left", text)
        self.assertIn("pedestals up", text)
        self.assertEqual(camera_phrase_from_state({"keys": [DOLLY["keys"][0]]}, 124), "The camera holds a static shot.")


class ScenaristCameraTests(unittest.TestCase):
    def test_camera_line_reaches_qwen_only_when_sent(self):
        payload = ScenaristRequest(brief="Вега идёт по крыше", references=[], cut_durations=[5, 5],
                                   camera=[{"movement": "push", "direction": "in", "speed": "slow",
                                            "amplitude": "small", "shot": 2}])
        with patch.object(scenarist, "_chat", return_value="x") as chat:
            write_ir(payload, FakeDirector())
        user = chat.call_args.args[1][1]["content"]
        self.assertIn("CAMERA: [Shot 2] The camera pushes in with small amplitude at slow speed.", user)
        with patch.object(scenarist, "_chat", return_value="x") as chat:
            write_ir(ScenaristRequest(brief="Вега идёт по крыше"), FakeDirector())
        self.assertNotIn("CAMERA", chat.call_args.args[1][1]["content"])

    def test_bad_camera_is_422_before_qwen_wakes(self):
        from fastapi import HTTPException

        class Comfy:
            async def release_models_if_idle(self):
                raise AssertionError("must not touch the GPU")

        route = next(r for r in scenarist.make_router(Comfy()).routes if r.path == "/api/scenarist")
        for camera in ({"movement": "fly"}, {"movement": "pan", "direction": "left", "shot": 3}):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(route.endpoint(ScenaristRequest(brief="Вега идёт", cut_durations=[5, 5], camera=camera)))
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(camera_lines(None, 1), [])

    def test_scenarist_camera_reaches_the_agent_task(self):
        from local_studio import agent_tasks
        from local_studio.scenarist import SceneBrief, make_router
        from local_studio.store import StudioStore
        asked = []

        async def fake_ask(prompt, images):
            asked.append(prompt)
            return ""

        with tempfile.TemporaryDirectory() as temp:
            route = next(r for r in make_router(StudioStore(Path(temp)), "").routes if r.path == "/api/scenarist")
            with patch.object(agent_tasks, "_ask", fake_ask):
                asyncio.run(route.endpoint(SceneBrief(brief="Вега идёт", camera={"movement": "static"})))
                self.assertIn("CAMERA:", asked[0])
                asked.clear()
                asyncio.run(route.endpoint(SceneBrief(brief="Вега идёт")))
                self.assertNotIn("CAMERA:", asked[0])


class GraphCameraTextTests(unittest.TestCase):
    def test_official_prompt_is_never_prefixed_with_camera_text(self):
        graph = build_prompt(GenerationRequest(IR, 5, "9:16", "compact", steps=24), ["hero.png"], camera_state=DOLLY)
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("subject_definitions:"))
        self.assertNotIn("330", graph)

    def test_legacy_prompt_keeps_numeric_text_and_ab_can_use_words(self):
        request = GenerationRequest("<Picture 1> walks to the window.", 5, "9:16", "compact", steps=24)
        legacy = build_prompt(request, ["hero.png"], camera_state=DOLLY)["136"]["inputs"]["prompt"]
        words = build_prompt(request, ["hero.png"], camera_state=DOLLY, camera_words=True)["136"]["inputs"]["prompt"]
        self.assertNotIn("The camera pushes in", legacy)
        self.assertTrue(words.startswith("The camera pushes in."))
        self.assertTrue(words.endswith("<Picture 1> walks to the window."))


if __name__ == "__main__":
    unittest.main()


def test_capabilities_expose_the_camera_words_vocabulary():
    from app.camera_words import camera_phrase
    from app.main import capabilities_from_nodes
    vocabulary = capabilities_from_nodes({})["camera_words_v1"]
    assert vocabulary["movements"]["pan"] == ["left", "right"]
    assert vocabulary["aliases"]["dolly_in"] == ["push", "in"]
    for move, directions in vocabulary["movements"].items():
        for direction in directions:
            for speed in vocabulary["speeds"]:
                for amplitude in vocabulary["amplitudes"]:
                    assert camera_phrase(move, direction, speed, amplitude).startswith("The camera ")
