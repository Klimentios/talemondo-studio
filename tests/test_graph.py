import os
import unittest
from unittest.mock import patch

from app.contract import GenerationRequest, resolution
from app.graph import build_prompt


@patch.dict(os.environ, {"H3_CAMERA_GUIDE_VIDEO": "1"})
class GraphTests(unittest.TestCase):
    def test_ref_only_graph_has_no_structural_control_nodes(self):
        request = GenerationRequest("<Picture 1> walks", 5, "9:16", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"])
        self.assertNotIn("300", graph)
        self.assertEqual(graph["136"]["inputs"]["ref_images.ref_image_0"], ["137", 0])

    def test_native_h3_video_reference_graph_has_no_controlnet_nodes(self):
        request = GenerationRequest("<Picture 1> follows <Video 1>", 5, "9:16", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"], reference_videos=["dance.mp4"])
        self.assertEqual(graph["136"]["inputs"]["ref_videos.ref_video_0"], ["139", 0])
        self.assertEqual(graph["138"]["class_type"], "LoadVideo")
        self.assertNotIn("300", graph)

    def test_camera_trajectory_is_real_video_reference_without_guide_audio(self):
        request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
        state = {"version": 1, "keys": [
            {"t": t, "orbit": orbit, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45}
            for t, orbit in ((0, 0), (4, 35))]}
        graph = build_prompt(request, ["hero.png"], camera_state=state)
        self.assertEqual(graph["330"]["class_type"], "H3CameraGuide")
        self.assertEqual(graph["136"]["inputs"]["ref_videos.ref_video_0"], ["330", 0])
        self.assertNotIn("ref_video_audios.ref_video_audio_0", graph["136"]["inputs"])
        self.assertIn("<Video 1>", graph["136"]["inputs"]["prompt"])

    def test_version_two_camera_adds_lens_aperture_and_dynamic_focus_to_h3_prompt(self):
        request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
        state = {"version": 2, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 5,
             "target": [0, 1, 0], "fov": 37.85, "focal_length_mm": 35,
             "aperture_f": 2.8, "focus_distance_m": 5},
            {"t": 4, "orbit": 0, "elevation": 0, "distance": 3,
             "target": [0, 1, 0], "fov": 37.85, "focal_length_mm": 35,
             "aperture_f": 2.8, "focus_distance_m": 3},
        ]}
        graph = build_prompt(request, ["hero.png"], camera_state=state)
        prompt = graph["136"]["inputs"]["prompt"]
        self.assertIn("35 mm", prompt)
        self.assertIn("f/2.8", prompt)
        self.assertIn("rack focus", prompt.lower())

    def test_explicit_optics_use_native_operator_node_and_h3_video_input(self):
        request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
        state = {"version": 2, "keys": [
            {"t": t, "orbit": 0, "elevation": 0, "distance": distance,
             "target": [0, 1, 0], "fov": 37.85, "focal_length_mm": 35,
             "aperture_f": 2.8, "focus_distance_m": distance}
            for t, distance in ((0, 5), (4, 3))]}
        graph = build_prompt(request, ["hero.png"], camera_state=state,
                             camera_optics={"sensor_format": "super_35",
                                            "focal_length_mm": 35, "aperture_f": 2.8,
                                            "focus_mode": "manual"})
        node = graph["330"]
        self.assertEqual(node["class_type"], "H3OperatorCamera")
        self.assertEqual(node["inputs"]["sensor_format"], "super_35")
        self.assertEqual(node["inputs"]["focal_length_mm"], 35.0)
        self.assertEqual(node["inputs"]["focus_distance_m"], 5.0)
        self.assertEqual(graph["136"]["inputs"]["ref_videos.ref_video_0"], ["330", 0])
        self.assertNotIn("ref_video_audios.ref_video_audio_0", graph["136"]["inputs"])

    def test_optics_without_trajectory_or_with_unknown_input_are_rejected(self):
        request = GenerationRequest("hero", 5, "16:9", "quick", steps=24)
        with self.assertRaisesRegex(ValueError, "траектория"):
            build_prompt(request, ["hero.png"], camera_optics={"iso": 800})
        state = {"version": 1, "keys": [
            {"t": 0, "orbit": 0, "elevation": 0, "distance": 4,
             "target": [0, 1, 0], "fov": 45}]}
        with self.assertRaisesRegex(ValueError, "Неверные параметры"):
            build_prompt(request, ["hero.png"], camera_state=state,
                         camera_optics={"fictional_camera_mode": "cinematic"})
        with self.assertRaisesRegex(ValueError, "iso"):
            build_prompt(request, ["hero.png"], camera_state=state,
                         camera_optics={"iso": float("nan")})

    def test_camera_refuses_fourth_video_reference(self):
        request = GenerationRequest("hero", 5, "16:9", "quick", steps=24)
        state = {"version": 1, "keys": [{"t": t, "orbit": orbit, "elevation": 0,
                "distance": 4, "target": [0, 1, 0], "fov": 45} for t, orbit in ((0, 0), (4, 35))]}
        with self.assertRaises(ValueError):
            build_prompt(request, ["hero.png"], reference_videos=["a.mp4", "b.mp4", "c.mp4"],
                         camera_state=state)

    def test_locked_camera_goes_to_h3_as_text_without_extra_video_reference(self):
        request = GenerationRequest("<Picture 1> walks", 10, "9:16", "custom", steps=24, megapixels=0.73)
        key = {"elevation": 0, "distance": 2.94, "target": [0, 1, 0], "fov": 37.85, "orbit": 0,
               "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 2.94}
        state = {"version": 2, "keys": [{**key, "t": 0}, {**key, "t": 10.0833}]}
        optics = {"sensor_format": "full_frame", "focal_length_mm": 35, "aperture_f": 2.8,
                  "focus_mode": "subject_head", "focus_distance_m": 2.94}
        graph = build_prompt(request, ["hero.png"], camera_state=state, camera_optics=optics)
        self.assertNotIn("330", graph)
        self.assertNotIn("ref_videos.ref_video_0", graph["136"]["inputs"])
        prompt = graph["136"]["inputs"]["prompt"]
        self.assertIn("35 mm", prompt)
        self.assertIn("f/2.8", prompt)
        self.assertNotIn("<Video 1>", prompt)
        with patch.dict("os.environ", {"H3_STATIC_CAMERA_GUIDE": "1"}):
            graph = build_prompt(request, ["hero.png"], camera_state=state, camera_optics=optics)
        self.assertEqual(graph["136"]["inputs"]["ref_videos.ref_video_0"], ["330", 0])

    def test_moving_camera_guide_is_rendered_small_to_save_tokens(self):
        request = GenerationRequest("<Picture 1> walks", 10, "9:16", "custom", steps=24, megapixels=0.98)
        state = {"version": 1, "keys": [{"t": t, "orbit": 0, "elevation": 0, "distance": d,
                 "target": [0, 1, 0], "fov": 27} for t, d in ((0, 4.5), (10, 3.1))]}
        graph = build_prompt(request, ["hero.png"], camera_state=state)
        self.assertEqual((graph["330"]["inputs"]["width"], graph["330"]["inputs"]["height"]), (256, 448))
        landscape = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
        graph = build_prompt(landscape, ["hero.png"], camera_state=state)
        self.assertEqual((graph["330"]["inputs"]["width"], graph["330"]["inputs"]["height"]), (448, 256))
        with patch.dict("os.environ", {"H3_CAMERA_GUIDE_SHORT_EDGE": "512"}):
            graph = build_prompt(request, ["hero.png"], camera_state=state)
        self.assertEqual((graph["330"]["inputs"]["width"], graph["330"]["inputs"]["height"]), (512, 896))

    def test_camera_move_goes_as_text_without_guide_video_by_default(self):
        request = GenerationRequest("<Picture 1> and <Picture 2> plan a heist", 10, "9:16", "custom", steps=24, megapixels=0.98)
        state = {"version": 2, "keys": [{"t": t, "orbit": 0, "elevation": 0, "distance": d, "target": [0, 1.2, 0],
                 "fov": 27, "focal_length_mm": 50, "aperture_f": 2, "focus_distance_m": d}
                 for t, d in ((0, 4.49), (10.08, 3.13))]}
        with patch.dict(os.environ, {"H3_CAMERA_GUIDE_VIDEO": ""}):
            graph = build_prompt(request, ["vega.png", "orion.png"], camera_state=state)
        self.assertNotIn("330", graph)
        self.assertNotIn("ref_videos.ref_video_0", graph["136"]["inputs"])
        prompt = graph["136"]["inputs"]["prompt"]
        self.assertIn("push in", prompt)
        self.assertIn("50 mm", prompt)

    def test_locked_camera_leaves_room_for_three_user_videos(self):
        request = GenerationRequest("<Video 1> <Video 2> <Video 3>", 5, "16:9", "quick", steps=24)
        state = {"version": 1, "keys": [{"t": 0, "orbit": 0, "elevation": 0,
                "distance": 4, "target": [0, 1, 0], "fov": 45}]}
        graph = build_prompt(request, ["hero.png"], reference_videos=["a.mp4", "b.mp4", "c.mp4"],
                             camera_state=state)
        self.assertNotIn("330", graph)

    def test_unverified_1930s_2d_lora_is_not_a_supported_style_adapter(self):
        request = GenerationRequest("<Picture 1> runs", 5, "16:9", "quick", steps=24)
        with self.assertRaisesRegex(ValueError, "Неизвестная стилевая LoRA"):
            build_prompt(request, ["hero.png"], style_adapter="cel2d")
        for removed in ("characterswap", "sketch2d"):  # non-canonical/unsupported names
            with self.assertRaisesRegex(ValueError, "Неизвестная стилевая LoRA"):
                build_prompt(request, ["hero.png"], style_adapter=removed)

    def test_style_lora_uses_model_only_loader_with_selected_cache(self):
        request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"], style_adapter="style3d")
        self.assertEqual(graph["311"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(graph["311"]["inputs"]["lora_name"],
                         "3dsrx_minimax_h3_rank16-step00001250.safetensors")
        self.assertEqual(graph["311"]["inputs"]["strength_model"], 1.0)
        self.assertEqual(graph["124"]["inputs"]["model"], ["311", 0])
        self.assertEqual(graph["311"]["inputs"]["model"], ["310", 0])

    def test_cartoon2d_lora_uses_pinned_model(self):
        request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"], style_adapter="cartoon2d")
        self.assertEqual(graph["311"]["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(graph["311"]["inputs"]["lora_name"],
                         "minimax-h3-rough-2d-cartoon-illustration-1200.safetensors")
        self.assertIn("rough 2D cartoon illustration", str(graph))

    def test_character_swap_uses_official_model_only_lora_strength_without_trigger(self):
        prompt = "subject_definitions:\n<P1>\nsummary:\nSwap <Video 1> with <Picture 1>."
        request = GenerationRequest(prompt, 5, "16:9", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"], reference_videos=["source.mp4"],
                             style_adapter="character_swap")
        node = graph["311"]
        self.assertEqual(node["class_type"], "LoraLoaderModelOnly")
        self.assertEqual(node["inputs"]["lora_name"], "h3_character_swap_pro4500_1000.safetensors")
        self.assertEqual(node["inputs"]["strength_model"], 1.0)
        self.assertEqual(node["inputs"]["model"], ["310", 0])
        self.assertNotIn("character swap", graph["136"]["inputs"]["prompt"].lower())

    def test_combat_lora_pair_uses_card_strengths_and_trigger(self):
        request = GenerationRequest("<Picture 1> fights", 5, "16:9", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"], style_adapter="combat")
        self.assertEqual(graph["311"]["inputs"]["strength_model"], 0.5)
        self.assertEqual(graph["312"]["inputs"]["strength_model"], 0.3)
        self.assertEqual(graph["312"]["inputs"]["model"], ["311", 0])
        self.assertEqual(graph["124"]["inputs"]["model"], ["312", 0])
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("wushu_action,"))
        self.assertIn("COMBAT CHOREOGRAPHY:", graph["136"]["inputs"]["prompt"])
        with_effect = build_prompt(request, ["hero.png"], style_adapter="combat",
                                   camera_effect="bullet_time")
        self.assertTrue(with_effect["136"]["inputs"]["prompt"].startswith("wushu_action,"))
        self.assertIn("COMBAT CHOREOGRAPHY:", with_effect["136"]["inputs"]["prompt"])

    def test_official_combat_prompt_gets_one_choreography_contract_after_lora_trigger(self):
        prompt = (
            "subject_definitions:\n<Subject 1> is ADAM from <Picture 1>.\n\n"
            "summary:\nADAM fights an opponent.\n\nretention_analysis:\nADAM retains his identity.\n\n"
            "detailed_description:\n[Shot 1] ADAM exchanges martial-arts strikes.\n\n"
            "overall_soundscape:\nFootsteps and impacts.\n\nnon_diegetic_music:\nN/A"
        )
        request = GenerationRequest(prompt, 5, "16:9", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"], style_adapter="combat")
        rendered = graph["136"]["inputs"]["prompt"]
        self.assertIn("detailed_description:\nwushu_action, COMBAT CHOREOGRAPHY:", rendered)
        self.assertEqual(rendered.count("COMBAT CHOREOGRAPHY:"), 1)

    def test_realism_people_uses_card_trigger_and_light_strength(self):
        request = GenerationRequest("<Picture 1> looks at camera", 5, "16:9", "quick", steps=24)
        graph = build_prompt(request, ["hero.png"], style_adapter="realism_people")
        self.assertEqual(graph["311"]["inputs"]["strength_model"], 0.7)
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("r34l1sm,"))

    def test_preset_values_reach_real_nodes(self):
        for quality, width, height, steps, detail in (
            ("compact", 960, 544, 24, "max"),
            ("quick", 1216, 672, 24, "max"),
            ("balanced", 1280, 736, 24, "max"),
            ("max", 1344, 768, 25, "max"),
        ):
            with self.subTest(quality=quality):
                request = GenerationRequest("hero", 5, "16:9", quality, steps=steps,
                                            ref_image_size=detail, seed=123)
                graph = build_prompt(request, ["hero.png"])
                self.assertEqual((graph["136"]["inputs"]["width"], graph["136"]["inputs"]["height"]),
                                 (width, height))
                self.assertEqual(graph["136"]["inputs"]["ref_image_size"], detail)
                self.assertEqual(graph["124"]["inputs"]["steps"], steps)
                self.assertEqual(graph["129"]["inputs"]["noise_seed"], 123)
                self.assertIn("310", graph)

    def test_quick_uses_h3_safe_cache_by_default(self):
        request = GenerationRequest("hero", 5, "16:9", "quick", steps=24)
        with patch.dict("os.environ", {}, clear=True):
            graph = build_prompt(request, ["hero.png"])
        self.assertEqual(graph["310"]["class_type"], "ApplyMiniMaxH3FirstBlockCache")
        self.assertEqual(graph["310"]["inputs"]["mode"], "Custom — manual values")
        self.assertTrue(graph["310"]["inputs"]["temporal_guard"])
        self.assertEqual(graph["124"]["inputs"]["model"], ["310", 0])

    def test_custom_megapixels_use_h3_cache(self):
        request = GenerationRequest("hero", 5, "16:9", "custom", megapixels=0.7, steps=24)
        graph = build_prompt(request, ["hero.png"])
        self.assertEqual(graph["310"]["class_type"], "ApplyMiniMaxH3FirstBlockCache")
        self.assertEqual((graph["136"]["inputs"]["width"], graph["136"]["inputs"]["height"]),
                         resolution("16:9", "custom", 0.7))

    def test_quick_cache_can_be_disabled_without_changing_resolution(self):
        request = GenerationRequest("hero", 5, "16:9", "quick", steps=24)
        with patch.dict("os.environ", {"H3_CACHE_MODE": "off"}):
            graph = build_prompt(request, ["hero.png"])
        self.assertNotIn("310", graph)
        self.assertEqual(graph["124"]["inputs"]["model"], ["127", 0])
        self.assertEqual(graph["136"]["inputs"]["width"], 1216)

    def test_cache_mode_is_explicit_and_precedes_lora_and_control(self):
        request = GenerationRequest("hero", 5, "16:9", "compact", steps=24)
        graph = build_prompt(request, ["hero.png"], style_adapter="style3d", cache_mode="fast")
        self.assertEqual(graph["310"]["inputs"]["mode"], "H3 Fast — 0.10 / max 2")
        self.assertEqual(graph["311"]["inputs"]["model"], ["310", 0])

    def test_fast_cache_stays_fast_with_a_movement_video(self):
        request = GenerationRequest("<Picture 1> repeats the movement from <Video 1>", 5, "9:16", "custom",
                                    megapixels=0.7, steps=25)
        graph = build_prompt(request, ["hero.png"], reference_videos=["move.mp4"], use_video_audio=False,
                             cache_mode="fast")
        self.assertEqual(graph["310"]["inputs"]["mode"], "H3 Fast — 0.10 / max 2")
        self.assertFalse(graph["310"]["inputs"]["temporal_guard"])

    def test_reference_graph_uses_bf16_full_model_and_official_aliases(self):
        request = GenerationRequest(
            "@ref1 walks through rain",
            5,
            "16:9",
            "native",
            profile="bf16",
            steps=25,
            seed=42,
        )
        graph = build_prompt(request, ["ref-a.png"])

        self.assertEqual(
            graph["127"]["inputs"]["unet_name"],
            "minimax_h3_ref2va_bf16.safetensors",
        )
        self.assertEqual(
            graph["128"]["inputs"]["clip_name"],
            "qwen3vl_32b_minimax_h3_bf16.safetensors",
        )
        self.assertEqual(graph["136"]["inputs"]["prompt"], "<Picture 1> walks through rain")
        self.assertEqual(graph["136"]["inputs"]["width"], 1344)
        self.assertEqual(graph["136"]["inputs"]["length"], 124)
        self.assertEqual(graph["136"]["inputs"]["ref_images.ref_image_0"], ["137", 0])
        self.assertEqual(graph["137"]["inputs"]["image"], "ref-a.png")
        self.assertEqual(graph["124"]["inputs"]["steps"], 25)
        self.assertEqual(graph["129"]["inputs"]["noise_seed"], 42)
        self.assertNotIn("145", graph, "Turbo LoRA must not be present")

    def test_requires_at_least_one_reference_in_reference_mode(self):
        request = GenerationRequest("A landscape", 5, "16:9", "native")
        with self.assertRaises(ValueError):
            build_prompt(request, [])

    def test_accepts_at_most_nine_image_references(self):
        request = GenerationRequest("A landscape", 5, "16:9", "native")
        with self.assertRaises(ValueError):
            build_prompt(request, [f"{index}.png" for index in range(10)])

    def test_reference_graph_supports_video_soundtrack_and_voice_reference(self):
        request = GenerationRequest(
            "@ref1 moves like @video1 and speaks with @audio1; reuse @video1audio rhythm",
            5,
            "16:9",
            "native",
        )
        graph = build_prompt(
            request,
            ["identity.png"],
            reference_videos=["motion.mp4"],
            reference_audios=["voice.wav"],
            use_video_audio=True,
        )

        self.assertEqual(graph["138"]["class_type"], "LoadVideo")
        self.assertEqual(graph["139"]["class_type"], "GetVideoComponents")
        self.assertEqual(graph["140"]["class_type"], "LoadAudio")
        self.assertEqual(graph["136"]["inputs"]["ref_videos.ref_video_0"], ["139", 0])
        self.assertEqual(graph["136"]["inputs"]["ref_video_audios.ref_video_audio_0"], ["139", 1])
        self.assertEqual(graph["136"]["inputs"]["ref_audios.ref_audio_0"], ["140", 0])
        self.assertIn("<Video 1>", graph["136"]["inputs"]["prompt"])
        self.assertIn("<Audio 2>", graph["136"]["inputs"]["prompt"])
        self.assertIn("<Audio 1>", graph["136"]["inputs"]["prompt"])

    def test_composition_guide_changes_native_conditioning_at_exact_frame(self):
        request = GenerationRequest("<Picture 1> and <Picture 2> in scene", 5, "9:16", "compact", steps=24)
        graph = build_prompt(request, ["face.png", "scene.png"], guide_frames=[(2, 48)])
        self.assertEqual(graph["320"]["class_type"], "MiniMaxH3AddGuide")
        self.assertEqual(graph["320"]["inputs"]["image"], ["138", 0])
        self.assertEqual(graph["320"]["inputs"]["frame_idx"], 48)
        self.assertEqual(graph["126"]["inputs"]["conditioning"], ["320", 0])
        with self.assertRaisesRegex(ValueError, "вне ролика"):
            build_prompt(request, ["face.png"], guide_frames=[(1, 500)])

    def test_structural_control_arguments_no_longer_exist(self):
        request = GenerationRequest("A landscape", 5, "16:9", "native")
        with self.assertRaises(TypeError):
            build_prompt(request, ["identity.png"], control_video="motion.mp4", control_type="pose")


if __name__ == "__main__":
    unittest.main()
