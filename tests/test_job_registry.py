import tempfile
import unittest
from pathlib import Path

from app.job_registry import JobRegistry


class JobRegistryTests(unittest.TestCase):
    def test_duplicate_is_not_queued_again_even_after_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "jobs.sqlite3"
            registry = JobRegistry(path)
            first, record = registry.accept("uuid-1", "hash-1")
            self.assertTrue(first)
            self.assertEqual(record["state"], "accepted")
            second, record = JobRegistry(path).accept("uuid-1", "hash-1")
            self.assertFalse(second)
            self.assertEqual(record["state"], "accepted")
            registry.queued("uuid-1", "comfy-1")
            self.assertTrue(registry.owns_remote("comfy-1"))
            self.assertFalse(registry.owns_remote("comfy-other"))
            third, record = JobRegistry(path).accept("uuid-1", "hash-1")
            self.assertFalse(third)
            self.assertEqual(record["remote_job_id"], "comfy-1")
            with self.assertRaises(ValueError):
                registry.accept("uuid-1", "other-hash")


if __name__ == "__main__":
    unittest.main()
