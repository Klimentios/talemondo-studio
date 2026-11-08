"""Production gates: broken shot plans and repeated renders after rejections."""
import json
import sqlite3
import unittest
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from pathlib import Path

from local_studio.production_gates import check, rejected_streak_problem, shot_timeline_problems

CLEAN = """[Shot 1: 0.00–0.86s] ADAM, eyes closed, runs a hand through his hair.
[Shot 2: 0.86–1.83s] the glass on the island edge; VEGA's paw nudges it.
[Shot 3: 1.83–7.00s] VEGA stares at ADAM, unimpressed."""


class TimelineTest(unittest.TestCase):
    def test_clean_plan_passes(self):
        self.assertEqual(shot_timeline_problems(CLEAN, 7), [])

    def test_duplicate_tail_shot_from_slicing(self):
        # prompt_9765_p2.txt.v1: the next part's first shot leaked in as shot 8, over shot 7
        broken = CLEAN + "\n[Shot 4: 6.86–7.00s] the glass tips off the island edge."
        self.assertTrue(any("накладываются" in p for p in shot_timeline_problems(broken, 7)))

    def test_repeated_shot_numbers(self):
        broken = CLEAN.replace("[Shot 3:", "[Shot 2:")
        self.assertTrue(any("не подряд" in p for p in shot_timeline_problems(broken, 7)))

    def test_shot_past_the_take(self):
        self.assertTrue(any("дубль 6" in p for p in shot_timeline_problems(CLEAN, 6)))

    def test_time_reference_from_another_part(self):
        broken = CLEAN.replace("unimpressed.", "unimpressed; at 9.7 s he leaps onto ADAM's back.")
        self.assertTrue(any("соседней части" in p for p in shot_timeline_problems(broken, 7)))

    def test_decade_is_not_a_time(self):
        self.assertEqual(shot_timeline_problems(CLEAN.replace("unimpressed.", "unimpressed, 1950s poster behind."), 7), [])

    def test_official_ir_shot_one_without_at(self):
        ir = "[Shot 1] ADAM yawns.\n[Shot 2] At 00:03.000 VEGA pushes the glass.\n[Shot 3] At 00:05.500 it falls."
        self.assertEqual(shot_timeline_problems(ir, 8), [])
        self.assertTrue(shot_timeline_problems(ir.replace("00:05.500", "00:02.000"), 8))


class RejectStreakTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = SimpleNamespace(root=Path(self.tmp.name))
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE jobs(job_id TEXT, state TEXT, manifest_json TEXT, updated_at TEXT)")
        self.manifest = {"videos": [{"asset_id": "src-9800-p2", "role": "Исходное видео"}]}

    def tearDown(self):
        self.tmp.cleanup()

    def render(self, job_id, at, verdict):
        self.db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (job_id, "completed", json.dumps(self.manifest), at))
        if verdict:
            from local_studio.production_review import review_file
            path = review_file(self.store, job_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([{"verdict": verdict}]))

    def test_two_rejects_stop_the_third(self):
        # two takes of one part both rejected: the same idea must not run a third time
        self.render("fbca0f7f", "2026-11-06 01:48", "rejected")
        self.render("28b2eeaf", "2026-11-06 02:14", "rejected")
        self.assertIn("отклонены", rejected_streak_problem(self.store, self.db, self.manifest, {}))
        self.assertIsNone(rejected_streak_problem(self.store, self.db, self.manifest, {"confirm_after_rejects": True}))
        with self.assertRaises(ValueError):
            check(self.store, self.db, prompt=CLEAN, settings={"duration": 7}, manifest=self.manifest)

    def test_drifted_previous_take_stops_the_next(self):
        # planned cut at 3.0 s came 2.4 s late; the same part must not go again as is
        from local_studio.take_timing import _file
        self.render("183bdd44", "2026-11-06 02:27", None)
        path = _file(self.store.root, "183bdd44")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"drift": [[3.0, 2.4]], "extra": []}))
        with self.assertRaises(ValueError) as caught:
            check(self.store, self.db, prompt=CLEAN, settings={"duration": 7}, manifest=self.manifest)
        self.assertIn("183bdd44", str(caught.exception))
        check(self.store, self.db, prompt=CLEAN, settings={"duration": 7, "confirm_after_rejects": True},
              manifest=self.manifest)

    def test_accepted_or_unreviewed_render_breaks_the_streak(self):
        self.render("a", "2026-11-06 01:00", "rejected")
        self.render("b", "2026-11-06 02:00", "creative_accepted")
        self.assertIsNone(rejected_streak_problem(self.store, self.db, self.manifest, {}))
        self.render("c", "2026-11-06 03:00", None)
        self.assertIsNone(rejected_streak_problem(self.store, self.db, self.manifest, {}))


if __name__ == "__main__":
    unittest.main()


def test_range_cuts_are_rewritten_to_minimax_at_form():
    # 06.11: H3 cut 0.5–1.75 s late on «[Shot 2: a–b s]»; Studio now writes the official «At» itself.
    from local_studio.production_gates import shot_cuts_to_at
    prompt = ("Kitchen, morning.\n[Shot 1: 0-1.63s] Wide shot, VEGA on the sofa.\n"
              "[Shot 2: 1.63–3.40s] Close-up of VEGA's eyes.\n[Shot 3: 3.4 - 6 s] ADAM drops the cup.")
    assert shot_cuts_to_at(prompt, 6) == (
        "Kitchen, morning.\n[Shot 1] Wide shot, VEGA on the sofa.\n"
        "[Shot 2] At 00:01.630, the shot cuts to a close-up of VEGA's eyes.\n"
        "[Shot 3] At 00:03.400, the shot cuts: ADAM drops the cup.")


def test_broken_range_plan_is_left_for_the_timeline_gate():
    from local_studio.production_gates import shot_cuts_to_at, shot_timeline_problems
    overlap = "[Shot 1: 0-3s] A.\n[Shot 2: 2-4s] B."
    assert shot_cuts_to_at(overlap, 4) == overlap and shot_timeline_problems(overlap, 4)
    too_long = "[Shot 1: 0-3s] A.\n[Shot 2: 3-9s] B."
    assert shot_cuts_to_at(too_long, 6) == too_long
    official = "[Shot 1] A.\n[Shot 2] At 00:03.000, the shot cuts to a wide shot."
    assert shot_cuts_to_at(official, 6) == official
