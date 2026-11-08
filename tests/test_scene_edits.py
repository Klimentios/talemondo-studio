"""Scene Board edit proposals are validated and remain separate from rendering."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.scene_edits import SceneBoardEditRequest, make_router, validate_scene_board_edit, write_scene_board_edit
from local_studio.scene_planner import make_router as make_mac_router


def shot(title="Вход", duration=10):
    return {"title":title,"action":"The hero enters the room and closes the door.","duration":duration,"framing":"medium",
            "lens_mm":50,"camera_motion":"static","start_frame":"new_keyframe"}


class FakeDirector:
    model="qwen-test"

    def __init__(self, result): self.result=result; self.calls=[]
    def _is_sleeping(self): return True
    def _runtime_control(self, command): self.calls.append(command)
    def _post(self, body):
        self.calls.append(body)
        return {"choices":[{"message":{"content":json.dumps(self.result,ensure_ascii=False)},"finish_reason":"stop"}]},None,0.1


class SceneEditTests(unittest.TestCase):
    def test_validates_add_and_update_operations(self):
        result=validate_scene_board_edit({"summary":"Добавлена реакция","operations":[
            {"op":"update","shot_id":"cut-1","changes":{"camera_motion":"tracking"}},
            {"op":"add_after","after_shot_id":"cut-1","shot":shot("Реакция",10)}]}, {"cut-1"}, "cut-1")
        self.assertEqual(len(result["operations"]),2)

    def test_rejects_unknown_ids_invalid_fields_and_first_shot_continuation(self):
        with self.assertRaisesRegex(ValueError,"не найден"):
            validate_scene_board_edit({"summary":"Правка","operations":[{"op":"update","shot_id":"cut-9","changes":{"duration":8}}]}, {"cut-1"}, "cut-1")
        with self.assertRaisesRegex(ValueError,"недопустимые поля"):
            validate_scene_board_edit({"summary":"Правка","operations":[{"op":"update","shot_id":"cut-1","changes":{"prompt":"rewrite all"}}]}, {"cut-1"}, "cut-1")
        with self.assertRaisesRegex(ValueError,"Первый шот"):
            validate_scene_board_edit({"summary":"Правка","operations":[{"op":"update","shot_id":"cut-1","changes":{"start_frame":"previous_end"}}]}, {"cut-1"}, "cut-1")
        with self.assertRaises(ValueError):
            validate_scene_board_edit({"summary":"Правка","operations":[{"op":"add_after","after_shot_id":"cut-1","shot":shot("Реакция",5)}]}, {"cut-1"}, "cut-1")

    def test_local_qwen_receives_full_board_and_structured_schema(self):
        proposal={"summary":"Камера мягче","operations":[{"op":"update","shot_id":"cut-1","changes":{"camera_motion":"tracking"}}]}
        director=FakeDirector(proposal)
        payload=SceneBoardEditRequest(command="Сделай камеру плавнее",shots=[{"id":"cut-1","duration":5,"title":"Вход"}])
        result=write_scene_board_edit(payload,director)
        body=next(call for call in director.calls if isinstance(call,dict))
        self.assertEqual(result["summary"],"Камера мягче")
        self.assertEqual(body["structured_outputs"]["json"]["required"],["operations","summary"])
        self.assertEqual(body["messages"][1]["content"].find("cut-1")>=0,True)
        self.assertEqual(director.calls[-1],"sleep?level=1")

    def test_gpu_route_refuses_to_run_during_render(self):
        class Busy:
            async def release_models_if_idle(self): raise RuntimeError("busy")
        route=next(route for route in make_router(Busy()).routes if route.path=="/api/scene-board-edits")
        payload=SceneBoardEditRequest(command="Сделай камеру плавнее",shots=[{"id":"cut-1","duration":5}])
        with self.assertRaises(HTTPException) as caught: asyncio.run(route.endpoint(payload))
        self.assertEqual(caught.exception.status_code,409)

    def test_mac_board_edit_comes_from_the_agent_and_a_bad_answer_is_refused(self):
        from local_studio import agent_tasks
        route=next(route for route in make_mac_router("").routes if route.path=="/api/scene-board-edits")
        payload=SceneBoardEditRequest(command="Сделай камеру плавнее",shots=[{"id":"cut-1","duration":5}])
        async def invented_id(prompt,images):
            return json.dumps({"summary":"x","operations":[{"op":"update","shot_id":"cut-9","changes":{"duration":6}}]})
        with patch.object(agent_tasks,"_ask",invented_id):
            with self.assertRaises(HTTPException) as caught: asyncio.run(route.endpoint(payload))
        self.assertEqual(caught.exception.status_code,422)  # the old validator still guards the board


if __name__=="__main__": unittest.main()
