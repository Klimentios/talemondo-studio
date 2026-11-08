"""The cache mode the user picked is the one the graph uses — also with a movement video (2026-10-05)."""
import json
import unittest

from app.contract import GenerationRequest
from app.graph import build_prompt, effective_cache_mode
from local_studio.worker import director_summary_json


class EffectiveCacheTests(unittest.TestCase):
    def test_graph_keeps_the_chosen_mode_with_and_without_video(self):
        request = GenerationRequest("<Picture 1> follows <Video 1>", 5, "16:9", "quick", steps=24)
        video = build_prompt(request, ["a.png"], reference_videos=["v.mp4"], use_video_audio=False, cache_mode="fast")
        self.assertEqual(effective_cache_mode("fast", True), "fast")
        self.assertEqual(video["310"]["inputs"]["mode"], "H3 Fast — 0.10 / max 2")
        safe = build_prompt(request, ["a.png"], reference_videos=["v.mp4"], use_video_audio=False, cache_mode="safe")
        self.assertTrue(safe["310"]["inputs"]["temporal_guard"])

    def test_summary_is_stored_as_reported(self):
        video_job = {"manifest_json": json.dumps({"images": [], "videos": [{"asset_id": "v"}], "audios": []})}
        director = {"cache_mode": "fast", "steps": 25}
        self.assertEqual(json.loads(director_summary_json(director, video_job)), director)
        self.assertIsNone(director_summary_json(None, video_job))


if __name__ == "__main__":
    unittest.main()


def test_custom_cache_reaches_node_and_rejects_invalid_configuration():
    import pytest
    from app.cache_policy import normalize_cache_config, cache_from_graph
    config = dict(threshold=0.035, start_percent=0.2, end_percent=0.8,
                  max_consecutive_hits=1, temporal_guard=True)
    request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
    graph = build_prompt(request, ["a.png"], cache_mode="custom", cache_config=config)
    assert graph["310"]["inputs"] == {"model": ["127", 0], "mode": "Custom — manual values", **config}
    assert cache_from_graph(graph)["effective"] == config
    for bad in ({**config, "threshold": float("nan")}, {**config, "start_percent": 0.9},
                {**config, "temporal_guard": "false"}, {**config, "max_consecutive_hits": True}):
        with pytest.raises(ValueError):
            normalize_cache_config("custom", bad)
    with pytest.raises(ValueError):
        normalize_cache_config("safe", config)

def test_fast_record_reports_preset_instead_of_ignored_manual_threshold():
    from app.cache_policy import cache_from_graph
    request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
    graph = build_prompt(request, ["a.png"], cache_mode="fast")
    report = cache_from_graph(graph)
    assert graph["310"]["inputs"]["threshold"] == 0.08  # approved historical graph
    assert report["effective"]["threshold"] == 0.10
    assert report["effective"]["temporal_guard"] is False
    assert cache_from_graph(build_prompt(request, ["a.png"], cache_mode="off"))["enabled"] is False
