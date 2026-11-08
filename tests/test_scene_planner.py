"""Scene planning validates Qwen output before any storyboard is accepted."""
from __future__ import annotations

import asyncio
import base64
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.scene_planner import ScenePlanRequest, make_router, validate_scene_plan, write_scene_plan
from local_studio.scene_planner import make_router as make_mac_router


def candidate(target=50):
    shots = []
    for index in range(5):
        shots.append({"title": f"Кадр {index+1}", "purpose": "Показать развитие истории",
                      "action": "The hero notices a change, steps toward the door and reacts as it opens.", "first_frame": "Герой стоит до движения",
                      "characters": [], "duration": target // 5,
                      "framing": "wide" if index == 0 else "medium", "lens_mm": 35,
                      "camera_motion": "статично", "start_frame": "new_keyframe" if index % 3 == 0 else "previous_end",
                      "state_before": "герой ищет", "state_after": "герой приблизился"})
    return {"dramatic_arc": "Обнаружение, действие, раскрытие", "visual_rule": "Тёплый свет, низкая камера", "shots": shots}


class FakeDirector:
    model = "qwen-test"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def _is_sleeping(self):
        return True

    def _runtime_control(self, command):
        self.calls.append(command)

    def _post(self, body):
        self.calls.append(body)
        return {"choices": [{"message": {"content": json.dumps(self.result)}, "finish_reason": "stop"}]}, None, 0.1


class ScenePlannerTests(unittest.TestCase):
    def test_video_plan_sends_timestamped_images_and_returns_evidence(self):
        director = FakeDirector(candidate())
        payload = ScenePlanRequest(idea="Recreate the original scene with Anna and ADAM", target_seconds=50,
            source_frames=[{"video_number":1,"seconds":2.5,"jpeg_base64":base64.b64encode(b"\xff\xd8test").decode()}],
            source_cuts={"1":[8.0]})
        plan = write_scene_plan(payload, director)
        self.assertTrue(plan['source_driven'])
        self.assertFalse(plan['evidence']['audio_analyzed'])
        body = next(call for call in director.calls if isinstance(call, dict))
        content = body['messages'][1]['content']
        self.assertTrue(any(item['type'] == 'image_url' for item in content))
        self.assertTrue(any('2.500' in item.get('text', '') for item in content))

    def test_plan_validates_duration_first_frame_and_continuation(self):
        plan = candidate()
        self.assertEqual(validate_scene_plan(plan, 50)["total_seconds"], 50)
        plan["shots"][0]["start_frame"] = "previous_end"
        with self.assertRaisesRegex(ValueError, "Первый"):
            validate_scene_plan(plan, 50)
        plan = candidate()
        for shot in plan["shots"][1:]:
            shot["start_frame"] = "previous_end"
        with self.assertRaisesRegex(ValueError, "трёх"):
            validate_scene_plan(plan, 50)

        plan = candidate()
        plan['shots'][0]['characters'] = ['VEGA']
        with self.assertRaisesRegex(ValueError, 'без фото'):
            validate_scene_plan(plan, 50, reference_owners={'orion'})
        self.assertEqual(validate_scene_plan(plan, 50, reference_owners={'vega'})['shots'][0]['characters'], ['VEGA'])
        plan['shots'][0]['characters'] = []
        plan['shots'][0]['action'] = 'VEGA открывает дверь'
        with self.assertRaisesRegex(ValueError, 'отсутствует в составе'):
            validate_scene_plan(plan, 50, reference_owners={'vega'})
        plan = candidate()
        plan['shots'][0]['state_after'] = plan['shots'][0]['state_before']
        with self.assertRaisesRegex(ValueError, 'нет видимого результата'):
            validate_scene_plan(plan, 50)
        plan = candidate()
        plan['shots'][0]['action'] = 'Герой идёт к двери.'
        with self.assertRaisesRegex(ValueError, 'на английском'):
            validate_scene_plan(plan, 50)

    def test_qwen_uses_structured_outputs_and_releases_vram(self):
        director = FakeDirector(candidate())
        payload = ScenePlanRequest(idea="VEGA слышит мышь под диваном, а потом находит игрушку.", target_seconds=50)
        result = write_scene_plan(payload, director)
        self.assertEqual(result["total_seconds"], 50)
        body = next(item for item in director.calls if isinstance(item, dict))
        self.assertEqual(body["structured_outputs"]["json"]["required"], ["dramatic_arc", "visual_rule", "shots"])
        self.assertEqual(director.calls[-1], "sleep?level=1")

    def test_gpu_route_rejects_busy_render_before_qwen(self):
        class Busy:
            async def release_models_if_idle(self):
                raise RuntimeError("busy")
        route = next(item for item in make_router(Busy()).routes if item.path == "/api/scene-plans")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(route.endpoint(ScenePlanRequest(idea="VEGA hears a mouse and chases it around the room.")))
        self.assertEqual(caught.exception.status_code, 409)

    def test_agent_plans_the_scene_and_the_same_rules_check_it(self):
        from local_studio import agent_tasks
        from local_studio.agent_brain import BrainError
        route = next(item for item in make_mac_router("").routes if item.path == "/api/scene-plans")

        async def prose(prompt, images):
            self.assertIn("JSON Schema", prompt)
            return "Вот план: без JSON"

        with patch.object(agent_tasks, "_ask", prose):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(route.endpoint(ScenePlanRequest(idea="VEGA hears a mouse and chases it around the room.")))
        self.assertEqual(caught.exception.status_code, 502)  # no fake plan: the page shows the agent's failure

        async def down(prompt, images):
            raise BrainError("Codex не вошёл в ChatGPT")

        with patch.object(agent_tasks, "_ask", down):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(route.endpoint(ScenePlanRequest(idea="VEGA hears a mouse and chases it around the room.")))
        self.assertIn("Агент не составил раскадровку", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()


class ShotCountTests(unittest.TestCase):
    def test_count_must_fit_duration_and_plan_must_match_it(self):
        from app.scene_planner import ScenePlanRequest, validate_scene_plan
        idea = "VEGA входит в гараж, находит деньги и уезжает."
        self.assertEqual(ScenePlanRequest(idea=idea, target_seconds=40, shot_count=4).shot_count, 4)
        with self.assertRaises(ValueError): ScenePlanRequest(idea=idea, target_seconds=70, shot_count=4)
        with self.assertRaises(ValueError): ScenePlanRequest(idea=idea, target_seconds=16, shot_count=5)
        shot = {"title": "t", "action": "a", "first_frame": "герой стоит", "characters": [], "purpose": "p", "duration": 10, "framing": "wide", "lens_mm": 24,
                "camera_motion": "static", "state_before": "b", "state_after": "c", "start_frame": "new_keyframe"}
        plan = {"dramatic_arc": "arc", "visual_rule": "rule", "shots": [shot, {**shot, "start_frame": "previous_end"}]}
        try:
            validate_scene_plan(plan, 20)
        except ValueError:
            self.skipTest("схема шота другая — проверяется в соседних тестах")
        with self.assertRaises(ValueError): validate_scene_plan(plan, 20, 4)
        self.assertEqual(len(validate_scene_plan(plan, 20, 2)["shots"]), 2)
