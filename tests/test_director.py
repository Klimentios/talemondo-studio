import unittest
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.director import (
    DIRECTOR_MAX_OUTPUT_TOKENS, DIRECTOR_ROLE_MAP, apply_cinematography_policy, camera_state_from_prompt,
    cinematography_direction, compile_director_plan, emotional_performance_contract,
    missing_model_files, remove_unbacked_reference_data,
    openai_director_payload, should_use_image_reference_plan,
    preserve_explicit_camera_state,
    required_nodes_for_plan, resolved_image_only_role, validate_director_plan,
    validate_camera_or_keep_prompt,
    _video_sample_jpegs,
)


def valid_plan():
    return {
        "schema_version": 1,
        "references": [
            {"asset_id": "hero", "role": "identity", "fidelity": "fully_preserved",
             "visual_evidence": ["Large green eyes, short dark hair, small pointed nose", "Black shirt with yellow emblem"]},
            {"asset_id": "dance", "role": "motion", "fidelity": "partly_preserved", "visual_evidence": ["Quick crouch, pause, then two short steps"]},
        ],
        "style": "animation_3d",
        "additional_direction": "Keep both characters separate and preserve their silhouettes.",
        "control": {"type": "none", "source_asset_id": None, "people": 1, "strength": 1.0},
        "camera_state": None,
        "guide_frames": [],
        "quality_intent": "final",
        "audio_intent": "native_stereo_effects_music",
    }


class DirectorPlanTests(unittest.TestCase):
    def test_standalone_audio_is_numbered_after_video_soundtracks(self):
        plan = valid_plan()
        plan["references"].append({"asset_id": "voice", "role": "voice", "fidelity": "weak_inspiration",
                                    "visual_evidence": []})
        kinds = {"hero": "image", "dance": "video", "voice": "audio"}
        with_track = compile_director_plan("A hero dances.", plan, kinds, duration=5, frame_count=124,
                                           video_audio_count=1)["prompt"]
        self.assertIn("<Audio 2> provides voice characteristics only", with_track)
        self.assertNotIn("<Audio 1> provides voice", with_track)
        without_track = compile_director_plan("A hero dances.", plan, kinds, duration=5, frame_count=124)["prompt"]
        self.assertIn("<Audio 1> provides voice characteristics only", without_track)

    def test_explicit_studio_camera_overrides_qwen_camera_but_keeps_other_plan_fields(self):
        plan = valid_plan()
        plan["camera_state"] = {"version": 1, "keys": [
            {"t": 0, "orbit": 90, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45}]}
        studio_state = {"version": 1, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45},
            {"t": 4, "orbit": 25, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45}]}
        manifest = {"videos": [{"asset_id": "dance", "role": "Движение"}]}
        selected = preserve_explicit_camera_state(
            plan, studio_state, manifest, {"hero": "image", "dance": "video"}, 124)
        self.assertEqual(selected["camera_state"], studio_state)
        self.assertEqual(selected["control"], plan["control"])
        self.assertIsNot(selected, plan)

    def test_explicit_camera_rejects_second_source_camera_and_invalid_time(self):
        plan = valid_plan()
        state = {"version": 1, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45}]}
        kinds = {"hero": "image", "dance": "video"}
        with self.assertRaisesRegex(ValueError, "конфликтует"):
            preserve_explicit_camera_state(
                plan, state, {"videos": [{"asset_id": "dance", "role": "Камера"}]}, kinds, 124)
        camera_plan = valid_plan()
        camera_plan["references"][1]["role"] = "camera"
        with self.assertRaisesRegex(ValueError, "конфликтует"):
            preserve_explicit_camera_state(camera_plan, state, {"videos": []}, kinds, 124)
        bad_state = {"version": 1, "keys": [{**state["keys"][0], "t": 20}]}
        with self.assertRaisesRegex(ValueError, "камер"):
            preserve_explicit_camera_state(plan, bad_state, {"videos": []}, kinds, 124)

    def test_emotion_director_instruction_is_style_agnostic_and_preserves_prompt(self):
        payload = openai_director_payload(
            "The kitten notices the falling mug and panics.", [], model="qwen", frame_count=124,
            requested_style="animation_3d", emotional_direction=True,
        )
        request = payload["messages"][1]["content"][0]["text"]
        self.assertIn("emotion", request.lower())
        self.assertIn("baseline", request.lower())
        self.assertIn("peak", request.lower())
        self.assertIn("2D", request)
        self.assertIn("3D", request)
        self.assertIn("replacement", request.lower())
        self.assertIn("The kitten notices the falling mug and panics.", request)
        # max_tokens follows the schema bounds (a valid plan always fits) and never exceeds the cap.
        from app.director import director_output_budget
        self.assertEqual(payload["max_tokens"], director_output_budget(0, camera_locked=False))
        self.assertLessEqual(payload["max_tokens"], DIRECTOR_MAX_OUTPUT_TOKENS)
        contract = emotional_performance_contract()
        self.assertIn("anticipation", contract)
        self.assertIn("whiskers", contract)
        self.assertIn("2D", contract)
        self.assertIn("3D", contract)
        self.assertIn("identity", contract)

    def test_image_only_shortcut_is_disabled_when_emotion_direction_is_enabled(self):
        refs = [{"asset_id": "hero", "kind": "image", "role_override": "identity"}]
        self.assertFalse(should_use_image_reference_plan(
            refs, "realism", emotional_direction=True,
        ))
        self.assertTrue(should_use_image_reference_plan(
            refs, "realism", emotional_direction=False,
        ))

    def test_text_only_qwen_plan_cannot_invent_reference_ids_or_guides(self):
        plan = valid_plan()
        remove_unbacked_reference_data(plan, {})
        self.assertEqual(plan["references"], [])
        self.assertEqual(plan["guide_frames"], [])

    def test_source_video_camera_does_not_get_conflicting_synthetic_path(self):
        plan = valid_plan()
        plan["camera_state"] = {"version": 1, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45}]}
        result = apply_cinematography_policy(
            plan, "Use <Video 1> as the primary source camera reference.", 362)
        self.assertIsNone(result["camera_state"])

    def test_identity_photo_cannot_become_scene_guide(self):
        plan = valid_plan()
        plan["guide_frames"] = [{"asset_id": "hero", "frame_idx": 50}]
        result = apply_cinematography_policy(plan, "Use <Video 1> for motion.", 362)
        self.assertEqual(result["guide_frames"], [])

    def test_long_qwen_direction_keeps_camera_policy_within_contract(self):
        plan = valid_plan()
        plan["additional_direction"] = "Keep the source performance. " * 100
        result = apply_cinematography_policy(plan, "Use <Video 1> as source camera.", 362)
        self.assertLessEqual(len(result["additional_direction"]), 3000)
        self.assertIn("CINEMATOGRAPHY CONTROL", result["additional_direction"])

    def test_invalid_qwen_camera_keeps_valid_reference_plan(self):
        plan = valid_plan()
        plan["camera_state"] = {"version": 1, "keys": [
            {"t": 20, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45}]}
        result = validate_camera_or_keep_prompt(
            plan, {"hero": "image", "dance": "video"}, 362, {})
        self.assertIsNone(result["camera_state"])
        self.assertEqual(len(result["references"]), 2)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg tools unavailable")
    def test_video_samples_from_nonseekable_uploaded_mp4(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=64x64:r=5:d=2", "-c:v", "mpeg4",
                            str(source)], check=True, timeout=20)
            frames = _video_sample_jpegs(source.read_bytes(), source.name)
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(frame.startswith(b"\xff\xd8") for frame in frames))

    def test_single_continuous_dolly_gets_conservative_camera_guide(self):
        state = camera_state_from_prompt(
            "One continuous shot. The camera slowly dollies in toward ADAM.", 124,
        )
        self.assertIsNotNone(state)
        self.assertEqual(len(state["keys"]), 2)
        self.assertGreater(state["keys"][0]["distance"], state["keys"][1]["distance"])
        self.assertEqual(state["keys"][0]["fov"], state["keys"][1]["fov"])
        self.assertEqual(state["version"], 2)
        self.assertGreater(state["keys"][0]["focus_distance_m"],
                           state["keys"][1]["focus_distance_m"])

    def test_multi_shot_plan_does_not_misuse_single_trajectory_guide(self):
        prompt = "[Shot 1] Wide shot. Dolly in.\n[Shot 2] Close-up after a hard cut."
        plan = valid_plan()
        plan["camera_state"] = {"version": 1, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45},
        ]}
        result = apply_cinematography_policy(plan, prompt, 124)
        self.assertIsNone(result["camera_state"])
        self.assertIn("separate edit", result["additional_direction"])
        self.assertIn("focus stable within each beat", result["additional_direction"])

    def test_optical_policy_forbids_focus_pumping_and_lens_morphing(self):
        direction = cinematography_direction("Medium shot of VEGA waiting by the door.")
        self.assertIn("never morph the lens", direction)
        self.assertIn("no pumping", direction)
        self.assertIn("do not default to Subject 1", direction)
        self.assertIn("Choose the focus target shot by shot", direction)

    def test_image_only_auto_role_defaults_to_identity_while_scene_stays_explicit(self):
        self.assertEqual(resolved_image_only_role(None), "identity")
        self.assertEqual(resolved_image_only_role("scene"), "scene")

    def test_auto_reference_selection_does_not_override_qwen_role_inference(self):
        self.assertIsNone(DIRECTOR_ROLE_MAP["Авто — определить по содержимому"])

    def test_selected_nodes_and_model_files_must_exist_before_graph_submission(self):
        self.assertIn("H3CameraGuide", required_nodes_for_plan(True, False, "none"))
        self.assertEqual(required_nodes_for_plan(True, False, "none", True),
                         {"H3OperatorCamera"})
        self.assertIn("MiniMaxH3AddGuide", required_nodes_for_plan(False, True, "none"))
        self.assertEqual(required_nodes_for_plan(False, False, "style3d"), {"LoraLoaderModelOnly"})
        self.assertEqual(missing_model_files("style3d", {}), [
            "3dsrx_minimax_h3_rank16-step00001250.safetensors",
        ])
        self.assertEqual(missing_model_files("none", {}), [])

    def test_plan_must_cover_each_reference_once_with_a_compatible_role(self):
        plan = validate_director_plan(valid_plan(), {"hero": "image", "dance": "video"}, 124)
        self.assertEqual(len(plan["references"]), 2)
        self.assertEqual(plan["control"]["type"], "none")

    def test_structural_control_in_plan_is_rejected(self):
        plan = valid_plan()
        plan["control"] = {"type": "pose", "source_asset_id": "dance", "people": 2, "strength": 0.7}
        with self.assertRaisesRegex(ValueError, "ControlNet"):
            validate_director_plan(plan, {"hero": "image", "dance": "video"}, 124)

    def test_plan_cannot_invent_reference_asset_ids(self):
        plan = valid_plan()
        plan["references"][0]["asset_id"] = "not-uploaded"
        with self.assertRaisesRegex(ValueError, "не относится к заданию"):
            validate_director_plan(plan, {"hero": "image", "dance": "video"}, 124)

    def test_plan_compilation_preserves_user_prompt_and_maps_to_real_controls(self):
        result = compile_director_plan(
            "Two characters cross the room.", valid_plan(), {"hero": "image", "dance": "video"},
            duration=5, frame_count=124, requested_steps=25, requested_cache="safe",
        )
        self.assertIn("Two characters cross the room.", result["prompt"])
        self.assertIn("<Picture 1> locks the same visible face", result["prompt"])
        self.assertIn("Large green eyes, short dark hair, small pointed nose", result["prompt"])
        self.assertIn("hands, costume and carried accessories", result["prompt"])
        self.assertEqual(result["visual_evidence"]["<Picture 1>"][0], "Large green eyes, short dark hair, small pointed nose")
        self.assertIn("Polished 3D animated feature film", result["prompt"])
        self.assertIn("synchronized stereo", result["prompt"])
        self.assertEqual(result["style_adapter"], "none")
        self.assertNotIn("control_type", result)
        self.assertEqual(result["steps"], 25)
        self.assertEqual(result["cache_mode"], "safe")

    def test_identity_face_does_not_claim_costume_when_matching_wardrobe_is_separate(self):
        result = compile_director_plan(
            "A hero walks.", valid_plan(), {"hero": "image", "dance": "video"},
            duration=5, frame_count=124, identity_with_wardrobe={"hero"},
        )
        identity_line = next(line for line in result["prompt"].splitlines() if "<Picture 1> locks" in line)
        self.assertIn("same visible face", identity_line)
        self.assertNotIn("silhouette, hands, costume", identity_line)
        self.assertIn("costume and carried accessories come only from their separately assigned wardrobe reference", identity_line)

    def test_description_copied_onto_two_different_pictures_is_dropped(self):
        plan = valid_plan()
        hero = plan["references"][0]
        copied = ["fur: short, brown tabby, white chest"]
        plan["references"] = [{**hero, "asset_id": "vega", "visual_evidence": copied},
                              {**hero, "asset_id": "orion", "visual_evidence": list(copied)},
                              plan["references"][1]]
        result = compile_director_plan(
            "Two cats run.", plan, {"vega": "image", "orion": "image", "dance": "video"},
            duration=5, frame_count=124,
        )
        self.assertNotIn("brown tabby", result["prompt"])
        self.assertNotIn("<Picture 1>", result["visual_evidence"])
        self.assertIn("<Picture 2> locks the same visible face", result["prompt"])

    def test_reference_labels_follow_upload_order_even_if_qwen_reorders_its_role_rows(self):
        plan = valid_plan()
        plan["references"].reverse()
        result = compile_director_plan(
            "Two characters cross the room.", plan, {"hero": "image", "dance": "video"},
            duration=5, frame_count=124,
        )
        self.assertIn("<Picture 1> locks the same visible face", result["prompt"])
        self.assertIn("<Video 1> provides body movement", result["prompt"])

    def test_only_enabled_style_adapter_is_added(self):
        result = compile_director_plan(
            "A stylized animated scene.", valid_plan(), {"hero": "image", "dance": "video"},
            duration=5, frame_count=124, available_adapters={"style3d"},
        )
        self.assertEqual(result["style_adapter"], "style3d")

    def test_explicit_reference_role_cannot_be_overridden(self):
        plan = valid_plan()
        plan["references"][0]["role"] = "style"
        with self.assertRaisesRegex(ValueError, "изменил явно выбранную роль"):
            validate_director_plan(plan, {"hero": "image", "dance": "video"}, 124,
                                   {"hero": "identity"})

    def test_camera_keys_and_guide_frames_are_shape_checked(self):
        plan = valid_plan()
        plan["camera_state"] = {"version": 1, "keys": ["bad"]}
        with self.assertRaisesRegex(ValueError, "ключ камеры"):
            validate_director_plan(plan, {"hero": "image", "dance": "video"}, 124)

    def test_style_reference_locks_art_direction_without_importing_its_characters(self):
        plan = valid_plan()
        plan["references"].append({
            "asset_id": "look", "role": "style", "fidelity": "fully_preserved",
            "visual_evidence": ["Thin dark contour lines", "Warm amber and teal palette", "Soft painted shadows"],
        })
        asset_kinds = {"hero": "image", "dance": "video", "look": "image"}
        result = compile_director_plan("Animate a new scene.", plan, asset_kinds, duration=5, frame_count=124)
        self.assertIn("do not copy its characters or composition", result["prompt"])
        self.assertIn("Thin dark contour lines; Warm amber and teal palette; Soft painted shadows", result["prompt"])

    def test_audio_reference_cannot_contain_visual_observations(self):
        plan = valid_plan()
        plan["references"].append({
            "asset_id": "audio", "role": "music", "fidelity": "fully_preserved",
            "visual_evidence": ["blue lighting"],
        })
        with self.assertRaisesRegex(ValueError, "Аудио не должно"):
            validate_director_plan(plan, {"hero": "image", "dance": "video", "audio": "audio"}, 124)
        plan = valid_plan()
        plan["guide_frames"] = [None]
        with self.assertRaisesRegex(ValueError, "guide frame"):
            validate_director_plan(plan, {"hero": "image", "dance": "video"}, 124)


if __name__ == "__main__":
    unittest.main()


def test_director_json_survives_markdown_fences_and_prose():
    from app.director import extract_director_json
    plan = {"schema_version": 1, "references": [], "style": "realism"}
    import json as _json
    body = _json.dumps(plan)
    assert extract_director_json(body) == plan
    assert extract_director_json(f"```json\n{body}\n```") == plan
    assert extract_director_json(f"Here is the plan:\n{body}\nThat is all.") == plan
    import pytest
    with pytest.raises(ValueError):
        extract_director_json("no json here")


def test_director_falls_back_only_when_native_contract_is_rejected_before_generation():
    """HTTP unsupported is cheap; do not repeat a generated invalid answer."""
    import io, json as _json
    from unittest.mock import patch
    from app.director import LocalQwenDirector
    plan = valid_plan()
    plan["references"] = [{"asset_id": "hero", "role": "identity", "fidelity": "fully_preserved", "visual_evidence": []}]
    plan["control"] = {"type": "none", "source_asset_id": None, "people": 1, "strength": 1.0}
    sent = []

    def fake_urlopen(request, timeout=0):
        body = _json.loads(request.data)
        sent.append("structured_outputs" if "structured_outputs" in body else "response_format" if "response_format" in body else "other")
        if len(sent) == 1:
            import urllib.error
            raise urllib.error.HTTPError(request.full_url, 400, 'Unsupported field', {}, None)
        content = _json.dumps(plan)
        return io.BytesIO(_json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode())

    director = LocalQwenDirector("http://127.0.0.1:8000/v1")
    with patch("urllib.request.urlopen", fake_urlopen), \
         patch.object(LocalQwenDirector, "_is_sleeping", return_value=False), \
         patch.object(LocalQwenDirector, "_runtime_control", return_value=None):
        result = director.plan("<Picture 1> walks.", [{"asset_id": "hero", "kind": "image", "tag": "<Picture 1>", "frames": []}],
                               {"hero": "image"}, 121, emotional_direction=True)
    assert sent == ["structured_outputs", "response_format"]
    assert result["references"][0]["asset_id"] == "hero"


def test_director_retries_once_with_a_compact_request_after_a_plan_contract_failure():
    import io, json as _json
    from unittest.mock import patch
    from app.director import LocalQwenDirector
    plan = valid_plan()
    plan["references"] = [{"asset_id": "hero", "role": "identity", "fidelity": "fully_preserved", "visual_evidence": []}]
    plan["control"] = {"type": "none", "source_asset_id": None, "people": 1, "strength": 1.0}
    sent = []

    def fake_urlopen(request, timeout=0):
        body = _json.loads(request.data)
        sent.append("structured_outputs" if "structured_outputs" in body else "response_format")
        content = _json.dumps({"unrelated": True} if len(sent) == 1 else plan)
        return io.BytesIO(_json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode())

    director = LocalQwenDirector("http://127.0.0.1:8000/v1")
    with patch("urllib.request.urlopen", fake_urlopen), \
         patch.object(LocalQwenDirector, "_is_sleeping", return_value=False), \
         patch.object(LocalQwenDirector, "_runtime_control", return_value=None):
        result = director.plan("<Picture 1> walks.", [{"asset_id": "hero", "kind": "image", "tag": "<Picture 1>", "frames": []}],
                               {"hero": "image"}, 121, emotional_direction=True)
    # Exactly one generated retry, on the same native transport, then the plan is accepted.
    assert sent == ["structured_outputs", "structured_outputs"]
    assert result["references"][0]["asset_id"] == "hero"


class DirectorAudioIntentTests(unittest.TestCase):
    def test_every_clip_has_sound_unless_silence_is_asked(self):
        # User rule 2026-10-08: Qwen's "silent" no longer mutes a clip whose prompt never mentioned sound.
        from app.director import prompt_requests_silence
        self.assertFalse(prompt_requests_silence("A cat walks across the kitchen."))
        self.assertFalse(prompt_requests_silence("A cat walks.\nAUDIO DIRECTION: Silent video; no speech."))
        self.assertTrue(prompt_requests_silence("Silent video. The cat walks."))
        self.assertTrue(prompt_requests_silence("Кот идёт, без звука"))
        source = open(__import__("app.director", fromlist=["x"]).__file__).read()
        self.assertIn('if plan["audio_intent"] == "silent" and not prompt_requests_silence(prompt):', source)

    def test_sound_detection_ignores_generated_audio_lines_and_respects_silence(self):
        from app.director import prompt_requests_sound
        self.assertTrue(prompt_requests_sound('ORION says: "Not tonight."'))
        self.assertTrue(prompt_requests_sound("Марк говорит тихо, звучит музыка"))
        self.assertFalse(prompt_requests_sound("A cat walks.\nAUDIO DIRECTION: Generate rich audio with music."))
        self.assertFalse(prompt_requests_sound("Silent video. The cat says nothing."))
        self.assertFalse(prompt_requests_sound("Кот идёт, без звука"))
        self.assertTrue(prompt_requests_sound('Very low muted bass pulse. VEGA says "Delivery door."'))
        self.assertTrue(prompt_requests_sound("No music yet. Paper softly slides; distant traffic sound."))
        self.assertFalse(prompt_requests_sound("A cat walks in the rain. No sound."))


def test_compact_plan_schema_binds_assets_and_keeps_studio_camera_out_of_model_output():
    from app.director import openai_director_payload, PLAN_SCHEMA
    refs = [{"asset_id":"hero", "kind":"image", "tag":"<Picture 1>", "frames":[]}]
    original = '0–3 seconds: <Picture 1> pauses. 3–5 seconds: says "Stay".'
    payload = openai_director_payload(original, refs, model='qwen', frame_count=121,
        camera_locked=True, emotional_direction=True)
    schema = payload['response_format']['json_schema']['schema']
    assert schema['properties']['camera_state'] == {'type':'null'}
    assert schema['properties']['references']['maxItems'] == 1
    assert schema['properties']['references']['items']['properties']['asset_id']['enum'] == ['hero']
    assert schema['properties']['control']['properties']['type']['enum'] == ['none']
    assert original in payload['messages'][1]['content'][0]['text']
    assert 'Do not rewrite' in payload['messages'][1]['content'][0]['text']
    assert 'anyOf' in PLAN_SCHEMA['properties']['camera_state']  # no global schema mutation
    unlocked = openai_director_payload(original, refs, model='qwen', frame_count=121)
    assert unlocked['response_format']['json_schema']['schema']['properties']['control']['properties']['type']['enum'] == ['none']
    assert 'anyOf' in unlocked['response_format']['json_schema']['schema']['properties']['camera_state']


def test_truncated_qwen_output_gets_one_compact_retry_and_always_sleeps():
    import io, json
    import pytest
    from unittest.mock import patch
    from app.director import LocalQwenDirector
    sent = []
    def response(request, timeout=0):
        sent.append(json.loads(request.data))
        return io.BytesIO(json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{"broken":'}}]}).encode())
    d = LocalQwenDirector()
    with patch('urllib.request.urlopen', response), patch.object(d,'_is_sleeping',return_value=False), \
         patch.object(d,'_runtime_control') as control, patch('app.director._save_director_raw'):
        with pytest.raises(RuntimeError, match='обрезан лимитом max_tokens'):
            d.plan('A cat reacts.',[],{},121,emotional_direction=True)
    # One compact retry with a smaller output budget, never more.
    assert len(sent) == 2
    assert sent[1]['max_tokens'] < sent[0]['max_tokens']
    assert all(body['structured_outputs']['disable_any_whitespace'] is True for body in sent)
    assert 'guided_json' not in sent[0]
    control.assert_called_once_with('sleep?level=1')


class StrayControlSourceTests(unittest.TestCase):
    def test_control_off_with_named_motion_video_is_cleaned_not_rejected(self):
        plan = valid_plan()
        plan["control"] = {**plan["control"], "source_asset_id": "dance"}  # Qwen named the motion video
        checked = validate_director_plan(plan, {"hero": "image", "dance": "video"}, 243)
        self.assertEqual(checked["control"]["type"], "none")
        self.assertIsNone(checked["control"]["source_asset_id"])
        self.assertEqual(plan["control"]["source_asset_id"], "dance")  # input left untouched


class MotionCameraRoleTests(unittest.TestCase):
    def test_motion_camera_video_drives_camera_and_keeps_focus_on_character(self):
        self.assertEqual(DIRECTOR_ROLE_MAP["Движение и камера"], "motion_camera")
        plan = valid_plan()
        plan["references"][1]["role"] = "motion_camera"
        plan["control"] = {**plan["control"], "type": "none", "source_asset_id": None}
        checked = validate_director_plan(plan, {"hero": "image", "dance": "video"}, 243)
        self.assertEqual(checked["references"][1]["role"], "motion_camera")
        studio_camera = {"version": 2, "keys": [{"t": 0, "orbit": 0, "elevation": 0, "distance": 4.2,
                         "target": [0, 1, 0], "fov": 27, "focal_length_mm": 50, "aperture_f": 2,
                         "focus_distance_m": 4.2}]}
        manifest = {"videos": [{"asset_id": "dance", "role": "Движение и камера"}]}
        kept = preserve_explicit_camera_state(checked, studio_camera, manifest,
                                              {"hero": "image", "dance": "video"}, 243)
        self.assertIsNone(kept["camera_state"])
        from app.director import ROLE_INSTRUCTION
        contract = ROLE_INSTRUCTION["motion_camera"][1]
        self.assertIn("camera movement and framing", contract)
        self.assertIn("original unbranded location", contract)
        self.assertIn("preserve source framing and focus", contract)
        self.assertIn("performer supplies performance, not appearance", contract)
        self.assertIn("anatomy, build and hands", contract)

class MotionSourceAppearanceTests(unittest.TestCase):
    def test_source_actor_appearance_is_audit_evidence_not_target_traits(self):
        plan = valid_plan()
        plan['references'][1]['visual_evidence'] = ['Long blonde hair, feminine hands, red dress']
        result = compile_director_plan('The assigned male character performs the dance.', plan,
                                       {'hero': 'image', 'dance': 'video'}, duration=5, frame_count=124)
        self.assertNotIn('Long blonde hair', result['prompt'])
        self.assertNotIn('feminine hands', result['prompt'])
        self.assertIn('Long blonde hair', result['visual_evidence']['<Video 1>'][0])
        self.assertIn('Large green eyes', result['prompt'])
