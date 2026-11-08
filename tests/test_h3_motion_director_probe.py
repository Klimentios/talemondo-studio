from scripts.h3_motion_director_probe import probe


def test_motion_director_requires_live_mixed_contract():
    required = {key: ["STRING", {}] for key in (
        "model", "video_vae", "audio_vae", "clip", "global_prompt", "timeline_data",
        "frame_rate", "width", "height", "total_frames")}
    required["task_type"] = [["t2v", "mixed"], {}]
    optional = {key: ["BOOLEAN", {}] for key in ("motion_context_enabled", "audio_context_enabled", "context_length", "steps")}
    info = {"MiniMaxH3MotionDirector": {"input": {"required": required, "optional": optional}}}
    assert probe(info)["api_compatible"] is True
    required.pop("timeline_data")
    assert probe(info)["api_compatible"] is False
    assert "timeline_data" in probe(info)["missing_required"]
