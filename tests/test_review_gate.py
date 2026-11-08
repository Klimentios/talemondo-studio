"""QC gate: the queue holds when finished takes pile up without a review."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from local_studio import review_gate
from local_studio.store import StudioStore


def _completed(store: StudioStore, job_id: str, when: str) -> None:
    with store.connect() as db:
        db.execute("INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,"
                   "mapping_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                   (job_id, "completed", "p", "p", "{}", '{"images":[],"videos":[],"audios":[]}', "{}", when, when))


class ReviewGateTests(unittest.TestCase):
    def test_hold_after_limit_and_acknowledge_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            review_gate._write(store, {"ack_at": "2026-11-06 00:00:00"})
            for i in range(2):
                _completed(store, f"job-{i}", f"2026-11-06 01:0{i}:00")
            self.assertIsNone(review_gate.review_hold(store))
            _completed(store, "job-2", "2026-11-06 01:02:00")
            self.assertEqual(review_gate.review_hold(store)["unreviewed"], 3)
            review_gate.acknowledge(store)
            self.assertIsNone(review_gate.review_hold(store))

    def test_reviewed_or_hidden_takes_do_not_count(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            review_gate._write(store, {"ack_at": "2026-11-06 00:00:00"})
            for i in range(3):
                _completed(store, f"job-{i}", f"2026-11-06 01:0{i}:00")
            reviews = Path(store.root) / "production_reviews"
            reviews.mkdir(parents=True, exist_ok=True)
            (reviews / (hashlib.sha256(b"job-0").hexdigest() + ".json")).write_text("{}")
            with store.connect() as db:
                db.execute("UPDATE jobs SET hidden=1 WHERE job_id='job-1'")
            self.assertEqual(review_gate.unreviewed_takes(store), ["job-2"])
            self.assertIsNone(review_gate.review_hold(store))

    def test_history_is_not_debt_and_limit_zero_disables(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            for i in range(5):
                _completed(store, f"old-{i}", f"2020-01-01 00:0{i}:00")
            self.assertIsNone(review_gate.review_hold(store))
            review_gate._write(store, {"ack_at": "2019-01-01 00:00:00", "limit": 0})
            self.assertIsNone(review_gate.review_hold(store))


class QueueRouteWiringTests(unittest.TestCase):
    def test_queue_routes_use_the_gate(self):
        source = (Path(__file__).resolve().parents[1] / "local_studio/routes/jobs.py").read_text()
        self.assertIn("review_gate.review_hold(ctx.store)", source)
        self.assertIn("review_gate.acknowledge(ctx.store)", source)


if __name__ == "__main__":
    unittest.main()
