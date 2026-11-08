"""Probe-all switch: read-only research probes on every Ref2VA render (app/probe_graph.py)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import probe_graph

GRAPH = {"1": {"class_type": "UNETLoader", "inputs": {}}, "2": {"class_type": "CLIPLoader", "inputs": {}},
         "5": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"clip": ["2", 0], "prompt": "hi"}},
         "6": {"class_type": "ApplyMiniMaxH3FirstBlockCache", "inputs": {"model": ["1", 0]}},
         "7": {"class_type": "BasicGuider", "inputs": {"model": ["6", 0], "conditioning": ["5", 0]}},
         "8": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "video/MiniMax_H3_Studio"}}}


class ProbeGraphTests(unittest.TestCase):
    def test_switch_off_returns_the_same_graph(self):
        with patch.object(probe_graph, "PROBE_FLAG", Path("/nonexistent/probe_all.json")):
            self.assertIs(probe_graph.attach_to_job(GRAPH, "job-1"), GRAPH)

    def test_switch_on_adds_read_only_probes_and_keeps_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            flag = Path(temp) / "probe_all.json"
            flag.write_text(json.dumps({"steps": "0,23", "blocks": "20", "queries": 128}))
            with patch.object(probe_graph, "PROBE_FLAG", flag):
                graph = probe_graph.attach_to_job(GRAPH, "3528f6c8-b1a2-446b-aceb-ae117f7402ea")
        kinds = {n["class_type"]: k for k, n in graph.items()}
        code, attn = graph[kinds["H3ProbeCode"]], graph[kinds["H3ProbeAttention"]]
        self.assertEqual(code["inputs"]["conditioning"], ["5", 0])
        self.assertEqual(graph["7"]["inputs"]["conditioning"], [kinds["H3ProbeCode"], 0])
        self.assertEqual(attn["inputs"]["model"], ["6", 0])  # after the cache patch, as the sampler saw it
        self.assertEqual(graph["7"]["inputs"]["model"], [kinds["H3ProbeAttention"], 0])
        self.assertEqual((attn["inputs"]["steps"], attn["inputs"]["blocks"], attn["inputs"]["queries"]), ("0,23", "20", 128))
        self.assertEqual(graph["8"]["inputs"]["filename_prefix"], "video/MiniMax_H3_Studio")
        self.assertNotIn("H3ProbeEditCode", kinds)  # probe-all never edits the code
        self.assertEqual(GRAPH["7"]["inputs"]["model"], ["6", 0])  # the caller's graph is not mutated

    def test_unusual_graph_or_missing_job_id_is_left_alone(self):
        with tempfile.TemporaryDirectory() as temp:
            flag = Path(temp) / "probe_all.json"
            flag.write_text("{}")
            with patch.object(probe_graph, "PROBE_FLAG", flag):
                no_r2v = {k: v for k, v in GRAPH.items() if k != "5"}
                self.assertIs(probe_graph.attach_to_job(no_r2v, "job-1"), no_r2v)
                self.assertIs(probe_graph.attach_to_job(GRAPH, ""), GRAPH)


if __name__ == "__main__":
    unittest.main()
