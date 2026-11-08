"""Plan vs result timing of finished takes and the gate that reads it."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from local_studio import take_timing
from local_studio.store import StudioStore


class PlanTests(unittest.TestCase):
    def test_range_and_at_formats(self):
        self.assertEqual(take_timing.plan("[Shot 1: 0.00–2.16s] a [Shot 2: 2.16–3.86s] b", 7.0),
                         [(1, 0.0, 2.16), (2, 2.16, 3.86)])
        self.assertEqual(take_timing.plan("[Shot 1] a [Shot 2] At 00:03.500 b", 5.0),
                         [(1, 0.0, 3.5), (2, 3.5, 5.0)])

    def test_compare_drift_missing_extra_short(self):
        shots = [(1, 0.0, 0.4), (2, 0.4, 3.0), (3, 3.0, 5.0)]
        result = take_timing.compare(shots, [2.9, 4.1])
        self.assertEqual(result["drift"], [[0.4, 2.5], [3.0, 1.1]])
        self.assertEqual(result["short"], [[1, 0.4]])
        self.assertEqual(take_timing.compare(shots, [0.42, 3.05, 4.0])["extra"], [4.0])
        self.assertTrue(take_timing.problems(result))
        self.assertEqual(take_timing.problems(take_timing.compare(shots, [0.42, 3.05])), [])


class GateTests(unittest.TestCase):
    def _job(self, store, job_id, source, when):
        manifest = {"images": [], "videos": [{"asset_id": source}], "audios": []}
        with store.connect() as db:
            db.execute("INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,"
                       "mapping_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (job_id, "completed", "p", "p", "{}", json.dumps(manifest), "{}", when, when))
        return manifest

    def _timing(self, store, job_id, drift):
        path = Path(store.root) / "take_timing" / (hashlib.sha256(job_id.encode()).hexdigest() + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"drift": drift, "extra": []}))

    def test_gate_reads_last_measured_take_of_the_same_source(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            manifest = self._job(store, "old", "src-1", "2026-11-06 01:00:00")
            self._timing(store, "old", [[2.0, 1.4]])
            with store.connect() as db:
                message = take_timing.previous_part_drift(store, db, manifest, {})
                self.assertIn("не выполнил план шотов", message)
                self.assertIsNone(take_timing.previous_part_drift(store, db, manifest, {"confirm_after_rejects": True}))
                other = {"images": [], "videos": [{"asset_id": "src-2"}], "audios": []}
                self.assertIsNone(take_timing.previous_part_drift(store, db, other, {}))
            self._job(store, "new", "src-1", "2026-11-06 02:00:00")
            self._timing(store, "new", [[2.0, 0.1]])
            with store.connect() as db:
                self.assertIsNone(take_timing.previous_part_drift(store, db, manifest, {}))

    def test_no_measurement_means_no_opinion(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            manifest = self._job(store, "x", "src-1", "2026-11-06 01:00:00")
            with store.connect() as db:
                self.assertIsNone(take_timing.previous_part_drift(store, db, manifest, {}))


if __name__ == "__main__":
    unittest.main()
