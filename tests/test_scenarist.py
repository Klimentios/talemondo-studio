import asyncio
import unittest
from unittest.mock import patch

from app import scenarist
from app.scenarist import ScenaristRequest, write_ir
from tests.test_h3_ir import good_ir


class FakeDirector:
    model = "qwen"
    base_url = "http://127.0.0.1:8000/v1"

    def __init__(self):
        self.calls = []

    def _is_sleeping(self):
        return True

    def _runtime_control(self, endpoint):
        self.calls.append(endpoint)


def request():
    return ScenaristRequest(
        brief="Адам спит на диване, Мартин ходит по ночной квартире и шепчет «Тише».",
        references=[{"kind": "image", "number": 1, "role": "Персонаж целиком", "owner": "ADAM"},
                    {"kind": "image", "number": 2, "role": "Лицо героя", "owner": "MARTIN"},
                    {"kind": "image", "number": 3, "role": "Окружение"}],
        cut_durations=[7, 8], speaking_owners=["MARTIN"])


class ScenaristTests(unittest.TestCase):
    def test_valid_answer_and_qwen_sleeps_again(self):
        director = FakeDirector()
        payload = request()
        from app.h3_ir import Reference, build_skeleton, shot_starts
        sk = build_skeleton([Reference(r.kind, r.number, r.role, r.owner) for r in payload.references],
                            speaking_owners=["MARTIN"])
        with patch.object(scenarist, "_chat", return_value=good_ir(sk, shot_starts([7, 8]))) as chat:
            result = write_ir(payload, director)
        self.assertEqual(result["problems"], [])
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(director.calls, ["wake_up", "sleep?level=1"])
        self.assertIn("<Subject 2> is MARTIN", result["skeleton"][1])

    def test_one_correction_round_then_flags(self):
        director = FakeDirector()
        with patch.object(scenarist, "_chat", side_effect=["summary:\nбез формата", "still wrong"]) as chat:
            result = write_ir(request(), director)
        self.assertEqual(chat.call_count, 2)
        self.assertTrue(result["problems"])
        self.assertIn("Fix these format problems", chat.call_args_list[1].args[1][-1]["content"])
        self.assertEqual(director.calls[-1], "sleep?level=1")

    def test_sleeps_even_when_qwen_fails(self):
        director = FakeDirector()
        with patch.object(scenarist, "_chat", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                write_ir(request(), director)
        self.assertEqual(director.calls[-1], "sleep?level=1")

    def test_busy_render_is_409(self):
        from fastapi import HTTPException

        class BusyComfy:
            async def release_models_if_idle(self):
                raise RuntimeError("busy")

        route = next(r for r in scenarist.make_router(BusyComfy()).routes if r.path == "/api/scenarist")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(route.endpoint(request()))
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
