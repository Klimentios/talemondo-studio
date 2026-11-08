import unittest
from unittest.mock import patch

from app.comfy_client import ComfyClient, _node_phase


class ComfyTimingTests(unittest.TestCase):
    def test_node_phases(self):
        self.assertEqual(_node_phase("125"), "sampling")
        self.assertEqual(_node_phase("136"), "reference_and_text")
        self.assertEqual(_node_phase("140"), "inputs")
        self.assertEqual(_node_phase("321"), "scene_guides")

    def test_executing_events_accumulate_and_summarize(self):
        client = ComfyClient()
        clock = iter([0.0, 10.0, 70.0, 75.0])
        with patch("app.comfy_client.time.monotonic", lambda: next(clock)), \
                patch("builtins.print"):
            client._record_executing("p1", "127")   # load starts at 0
            client._record_executing("p1", "125")   # sampling starts at 10
            client._record_executing("p1", "122")   # decode starts at 70
            client._record_executing("p1", None)    # finished at 75
            entry = {"status": {"messages": [
                ["execution_start", {"timestamp": 1_000}],
                ["execution_success", {"timestamp": 81_000}],
            ]}}
            timing = client._finish_timing("p1", entry)
        self.assertEqual(timing["total_seconds"], 80.0)
        self.assertEqual(timing["phases"], {"load": 10.0, "sampling": 60.0, "decode": 5.0})
        self.assertIs(client._finish_timing("p1", entry), timing)


if __name__ == "__main__":
    unittest.main()
