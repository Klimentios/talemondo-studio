"""Qwen Director reliability: bounded plans, explicit token budget, one compact retry, call log.

Failures on the old server (Studio DB, 2026-09-30..10-04): finish_reason=length at 19 888 and
257 502 characters (whitespace runaway), unconstrained indented JSON (2073-8246 chars) and a
non-object answer.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from unittest.mock import patch

import pytest

from app import director as d
from app.director import (
    DIRECTOR_COMPACT_LIMITS, DIRECTOR_FULL_LIMITS, DIRECTOR_MAX_OUTPUT_TOKENS, LocalQwenDirector,
    director_output_budget, estimate_director_input, extract_director_json, fit_director_references,
    openai_director_payload, sanitize_director_plan,
)

def fake_jpeg(width: int, height: int) -> bytes:
    """Smallest byte string whose SOF0 header says width x height."""
    return (b"\xff\xd8\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
            + b"\x03" + b"\x00" * 9 + b"\xff\xd9")


def plan_for(asset_ids: list[str], *, evidence: list[str] | None = None, direction: str = "Hold the beat.") -> dict:
    return {
        "schema_version": 1,
        "references": [{"asset_id": asset_id, "role": "identity", "fidelity": "fully_preserved",
                        "visual_evidence": list(evidence or [])} for asset_id in asset_ids],
        "style": "realism", "additional_direction": direction,
        "control": {"type": "none", "source_asset_id": None, "people": 1, "strength": 1.0},
        "camera_state": None, "guide_frames": [], "quality_intent": "final",
        "audio_intent": "native_stereo_effects_music",
    }


def reply(content: str, finish: str = "stop", completion: int = 300) -> io.BytesIO:
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish, "message": {"content": content}}],
                                  "usage": {"prompt_tokens": 5000, "completion_tokens": completion}}).encode())


def run_plan(responder, references, asset_kinds, prompt="ADAM <Picture 1> laughs.", **kwargs):
    sent: list[dict] = []

    def fake_urlopen(request, timeout=0):
        body = json.loads(request.data)
        sent.append(body)
        return responder(len(sent), body, request)

    director = LocalQwenDirector("http://127.0.0.1:8000/v1")
    with patch("urllib.request.urlopen", fake_urlopen), \
         patch.object(LocalQwenDirector, "_is_sleeping", return_value=False), \
         patch.object(LocalQwenDirector, "_runtime_control", return_value=None) as control, \
         patch("app.director._save_director_raw"):
        try:
            result = director.plan(prompt, references, asset_kinds, 121, emotional_direction=True, **kwargs)
        except RuntimeError as exc:
            result = exc
    return result, sent, control


def image_parts(body: dict) -> int:
    return sum(1 for part in body["messages"][1]["content"] if part.get("type") == "image_url")


# --- Goal 1: a valid plan is small by construction ---------------------------------------------

def test_largest_valid_plan_fits_the_computed_output_budget():
    """Every free-text field at its schema maximum still fits max_tokens (2.5 chars per token)."""
    for assets, locked in ((1, True), (3, True), (9, False)):
        ids = [f"{index:08d}-0000-0000-0000-000000000000" for index in range(assets)]
        plan = plan_for(ids, evidence=["я" * DIRECTOR_FULL_LIMITS["evidence_chars"]] * DIRECTOR_FULL_LIMITS["evidence_items"],
                        direction="x" * DIRECTOR_FULL_LIMITS["direction_chars"])
        if not locked:
            plan["camera_state"] = {"version": 2, "keys": [
                {"t": 0.123456, "orbit": -123.456, "elevation": 12.345, "distance": 12.345,
                 "target": [0.123, 1.234, -0.123], "fov": 45.678, "focal_length_mm": 35.0,
                 "aperture_f": 2.8, "focus_distance_m": 12.345}] * 12}
        plan["guide_frames"] = [{"asset_id": ids[0], "frame_idx": 360}] * 9
        size = len(json.dumps(plan, ensure_ascii=False, separators=(",", ":")))
        budget = director_output_budget(assets, camera_locked=locked)
        assert size / 2.5 <= budget <= DIRECTOR_MAX_OUTPUT_TOKENS


def test_request_schema_bounds_every_free_text_field_and_retry_states_the_limits():
    refs = [{"asset_id": "hero", "kind": "image", "tag": "Picture 1", "frames": [b"x"]}]
    payload = openai_director_payload("A cat waits.", refs, model="q", frame_count=121, camera_locked=True)
    schema = payload["response_format"]["json_schema"]["schema"]
    evidence = schema["properties"]["references"]["items"]["properties"]["visual_evidence"]
    assert schema["properties"]["additional_direction"]["maxLength"] == DIRECTOR_FULL_LIMITS["direction_chars"]
    assert evidence["maxItems"] == 6 and evidence["items"]["maxLength"] == 180
    text = payload["messages"][1]["content"][0]["text"]
    # The full attempt keeps the golden wording; only the schema bounds the fields.
    assert "Limits:" not in text
    assert payload["max_tokens"] < DIRECTOR_MAX_OUTPUT_TOKENS
    compact = openai_director_payload("A cat waits.", refs, model="q", frame_count=121, camera_locked=True,
                                      limits=DIRECTOR_COMPACT_LIMITS)
    compact_schema = compact["response_format"]["json_schema"]["schema"]
    assert compact_schema["properties"]["additional_direction"]["maxLength"] == 800
    assert compact_schema["properties"]["references"]["items"]["properties"]["visual_evidence"]["maxItems"] == 3
    assert compact["max_tokens"] < payload["max_tokens"]
    assert "additional_direction at most 800 characters" in compact["messages"][1]["content"][0]["text"]
    assert d.PLAN_SCHEMA["properties"]["additional_direction"]["maxLength"] == 3000  # not mutated


def test_asset_without_preview_frames_is_announced_and_its_invented_evidence_dropped():
    refs = [{"asset_id": "clip", "kind": "video", "tag": "Video 1", "frames": []},
            {"asset_id": "hero", "kind": "image", "tag": "Picture 1", "frames": [b"x"]}]
    payload = openai_director_payload("Copy <Video 1>.", refs, model="q", frame_count=121)
    texts = [part["text"] for part in payload["messages"][1]["content"] if part["type"] == "text"]
    assert any("No visual sample of Video 1" in text for text in texts)
    plan = plan_for(["clip", "hero"], evidence=["red scarf"])
    sanitize_director_plan(plan, "Copy <Video 1>.", refs)
    assert plan["references"][0]["visual_evidence"] == []
    assert plan["references"][1]["visual_evidence"] == ["red scarf"]


def test_sanitize_clamps_evidence_and_drops_direction_that_echoes_the_brief():
    brief = ("0-3 s: ADAM enters the workshop and drops his heavy bag on the bench. "
             "3-5 s: MARTIN looks up and smiles tiredly.")
    refs = [{"asset_id": "hero", "kind": "image", "frames": [b"x"]}]
    long_item = "dark green wool coat with brass buttons " * 8
    plan = plan_for(["hero"], evidence=[long_item, " short  hair ", "short hair", "", 7] + [f"d{i}" for i in range(9)],
                    direction="0-3 s: ADAM enters the workshop and drops his heavy bag on the bench. "
                              "Let his shoulders drop before he speaks.")
    sanitize_director_plan(plan, brief, refs)
    evidence = plan["references"][0]["visual_evidence"]
    assert len(evidence) == 6 and all(len(item) <= 180 for item in evidence)
    assert evidence[1] == "short hair" and evidence.count("short hair") == 1
    assert plan["additional_direction"] == "Let his shoulders drop before he speaks."


def test_sanitize_drops_unfinished_short_visual_evidence():
    refs = [{"asset_id": "hero", "kind": "image", "frames": [b"x"]}]
    plan = plan_for(["hero"], evidence=["soft full lips in calm,", "short dark hair", "freckles and"])
    sanitize_director_plan(plan, "A woman dances.", refs)
    assert plan["references"][0]["visual_evidence"] == ["short dark hair"]


def test_input_budget_reduces_frames_step_by_step_before_dropping_them():
    big = fake_jpeg(1024, 1024)  # ~1026 vision tokens each
    refs = [{"asset_id": f"v{i}", "kind": "video", "tag": f"Video {i}", "frames": [big, big, big]} for i in range(3)]
    build = lambda r: openai_director_payload("Brief.", r, model="q", frame_count=121, camera_locked=True)
    shrunk = []

    def shrink(frame, edge):
        shrunk.append(edge)
        return fake_jpeg(640, 640)

    full = build(refs)
    need = estimate_director_input(full)["total"] + full["max_tokens"] + d.DIRECTOR_INPUT_MARGIN_TOKENS
    out, mode, _ = fit_director_references(refs, build, max_model_len=need, shrink=shrink)
    assert mode == "all_frames" and out == refs
    out, mode, _ = fit_director_references(refs, build, max_model_len=need - 4000, shrink=shrink)
    assert mode == "one_frame_per_video" and all(len(item["frames"]) == 1 for item in out)
    assert out[0]["frames"][0] is big  # the middle sample is kept
    out, mode, _ = fit_director_references(refs, build, max_model_len=need - 6400, shrink=shrink)
    assert mode == "frames_640px" and shrunk and set(shrunk) == {640}
    out, mode, _ = fit_director_references(refs, build, max_model_len=need - 8500, shrink=shrink)
    assert mode == "no_frames" and all(item["frames"] == [] for item in out)
    with pytest.raises(ValueError, match="не помещается"):
        fit_director_references(refs, build, max_model_len=1000, shrink=shrink)


def test_brief_that_cannot_fit_never_wakes_qwen():
    director = LocalQwenDirector("http://127.0.0.1:8000/v1")
    with patch.object(d, "DIRECTOR_MAX_MODEL_LEN", 2000), \
         patch.object(LocalQwenDirector, "_is_sleeping") as sleeping, \
         patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(RuntimeError, match="не помещается в контекст Qwen"):
            director.plan("word " * 20000, [], {}, 121, emotional_direction=True)
    sleeping.assert_not_called()
    urlopen.assert_not_called()


def test_golden_request_shape_is_only_bounded_not_rewritten():
    """The user's prompt still reaches Qwen verbatim; Qwen is told not to reproduce it."""
    prompt = "ADAM <Picture 1> laughs. " * 400  # ~10k characters, like real Studio prompts
    payload = openai_director_payload(prompt, [], model="q", frame_count=121, camera_locked=True)
    text = payload["messages"][1]["content"][0]["text"]
    assert prompt in text
    assert "Do not fill additional_direction with a repetition of the user's text." in text


# --- Goal 2: robust parsing and one compact retry ----------------------------------------------

def test_parser_tolerates_fences_and_prose_but_never_accepts_a_non_object():
    plan = {"schema_version": 1, "references": []}
    body = json.dumps(plan)
    assert extract_director_json(f"Sure!\n```json\n{body}\n```\nDone.") == plan
    assert extract_director_json(json.dumps(body)) == plan  # double-encoded string
    for bad in ("[" + body + "]", "42", '"just text"', "null", "Here: [1, 2]"):
        with pytest.raises(ValueError):
            extract_director_json(bad)
    with pytest.raises(ValueError):
        extract_director_json(None)  # type: ignore[arg-type]


def test_context_overflow_retries_once_without_pictures():
    refs = [{"asset_id": "hero", "kind": "image", "tag": "Picture 1", "frames": [fake_jpeg(512, 512)]}]

    def responder(count, body, request):
        if count == 1:
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(
                b'{"message":"This model\'s maximum context length is 32768 tokens"}'))
        return reply(json.dumps(plan_for(["hero"], evidence=["invented"])))

    result, sent, control = run_plan(responder, refs, {"hero": "image"})
    assert isinstance(result, dict), result
    assert len(sent) == 2 and image_parts(sent[0]) == 1 and image_parts(sent[1]) == 0
    assert result["references"][0]["visual_evidence"] == []  # Qwen saw no picture in the retry
    schema = sent[1]["structured_outputs"]["json"]
    assert schema["properties"]["references"]["items"]["properties"]["visual_evidence"]["maxItems"] == 0
    control.assert_called_once_with("sleep?level=1")


def test_non_object_answer_retries_once_with_one_frame_per_video_then_falls_back_precisely():
    frame = fake_jpeg(1024, 576)
    refs = [{"asset_id": "clip", "kind": "video", "tag": "Video 1", "frames": [frame, frame, frame]}]
    result, sent, _ = run_plan(lambda count, body, request: reply('["not", "a", "plan"]'),
                               refs, {"clip": "video"}, prompt="Copy <Video 1>.")
    assert isinstance(result, RuntimeError)
    assert len(sent) == 2 and image_parts(sent[0]) == 3 and image_parts(sent[1]) == 1
    message = str(result)
    assert message.startswith("Qwen Director без плана — full: finish_reason=stop, ответ Qwen — JSON list, а не объект плана")
    assert "| compact: " in message


def test_server_errors_do_not_trigger_a_generated_retry():
    def responder(count, body, request):
        raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, io.BytesIO(b"busy"))

    result, sent, _ = run_plan(responder, [], {})
    assert isinstance(result, RuntimeError) and "HTTP 503" in str(result)
    assert len(sent) == 1


# --- Goal 4: one compact log line per call -------------------------------------------------------

def test_each_director_call_logs_size_budget_tokens_finish_and_time(caplog):
    answers = iter([reply('{"schema_version":1', finish="length", completion=3000),
                    reply(json.dumps(plan_for([])))])
    with caplog.at_level(logging.INFO, logger="app.director"):
        result, sent, _ = run_plan(lambda *args: next(answers), [], {})
    assert isinstance(result, dict), result
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("Director call")]
    assert len(lines) == 2
    assert "attempt=full" in lines[0] and "finish=length" in lines[0] and "output_tokens=3000" in lines[0]
    assert f"max_tokens={sent[0]['max_tokens']}" in lines[0] and "outcome=rejected" in lines[0]
    assert "attempt=compact" in lines[1] and "outcome=ok" in lines[1] and "seconds=" in lines[1]
    assert "input~" in lines[1] and "prompt_tokens=5000" in lines[1]
