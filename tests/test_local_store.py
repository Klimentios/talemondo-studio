import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from local_studio.store import (StudioStore, character_reference_contract, validate_image_bindings,
                                validate_visual_mode)


class StudioStoreTests(unittest.TestCase):
    def test_short_motion_video_is_rejected_before_queueing(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            assets = []
            for name, kind, role, mime in (
                ("hero.png", "image", "Лицо героя", "image/png"),
                ("motion.mp4", "video", "Движение и камера", "video/mp4"),
            ):
                path = store.assets_dir / name
                path.write_bytes(b"fixture")
                assets.append(store.add_asset(kind=kind, role=role, original_name=name,
                                              mime=mime, size=7, sha256=name, path=path)["asset_id"])
            with patch("local_studio.store._media_duration_seconds", return_value=7.5):
                with self.assertRaisesRegex(ValueError, "длится 7.5 с.*задан на 15 с"):
                    store.create_job(
                        prompt="<Picture 1> dances using the movement in <Video 1>.",
                        asset_ids=assets, settings={"duration": 15},
                        reference_roles={assets[0]: "Лицо героя", assets[1]: "Движение и камера"},
                        reference_owners={assets[0]: "Anna", assets[1]: "Anna"},
                    )
                accepted = store.create_job(
                    prompt="<Picture 1> dances using the movement in <Video 1>.",
                    asset_ids=assets, settings={"duration": 15, "allow_short_motion_reference": True},
                    reference_roles={assets[0]: "Лицо героя", assets[1]: "Движение и камера"},
                    reference_owners={assets[0]: "Anna", assets[1]: "Anna"},
                    style_adapters={"character_swap"},
                )
                self.assertTrue(json.loads(accepted["settings_json"])["allow_short_motion_reference"])

    def test_named_voice_reference_reaches_audio_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = []
            for name, kind, role, mime in (("hero.png", "image", "Лицо героя", "image/png"),
                                            ("voice.wav", "audio", "Голос", "audio/wav")):
                path = store.assets_dir / name
                path.write_bytes(b"fixture")
                ids.append(store.add_asset(kind=kind, role=role, original_name=name,
                                           mime=mime, size=7, sha256=name, path=path)["asset_id"])
            job = store.create_job(prompt="<Picture 1> speaks using <Audio 1>", asset_ids=ids,
                                   settings={}, reference_owners={ids[0]: "Вега", ids[1]: "Вега"})
            self.assertIn("<Audio 1> belongs to Вега only and provides voice characteristics only", job["compiled_prompt"])
            self.assertIn("VOICE PERFORMANCE DIRECTION", job["compiled_prompt"])
            self.assertEqual(json.loads(job["manifest_json"])["audios"][0]["owner"], "Вега")

    def test_unowned_costume_with_two_heroes_is_rejected_before_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = []
            for name, role in (("anna.png", "Лицо героя"), ("adam.png", "Лицо героя"),
                               ("coat.png", "Костюм")):
                path = store.assets_dir / name
                path.write_bytes(b"fixture")
                ids.append(store.add_asset(kind="image", role=role, original_name=name,
                                           mime="image/png", size=7, sha256=name, path=path)["asset_id"])
            with self.assertRaisesRegex(ValueError, "назначь владельца"):
                store.create_job(prompt="<Picture 1> and <Picture 2> wear <Picture 3>", asset_ids=ids,
                                 settings={}, reference_owners={ids[0]: "Анна", ids[1]: "Адам"})

    def test_optical_camera_settings_are_validated_and_persisted_with_job(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "hero.png"
            path.write_bytes(b"fixture")
            asset = store.add_asset(kind="image", role="Референс", original_name=path.name,
                                    mime="image/png", size=7, sha256="fixture", path=path)
            state = {"version": 2, "keys": [{"t": 0, "orbit": 0, "elevation": 0,
                     "distance": 4, "target": [0, 1, 0], "fov": 39,
                     "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 4}]}
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                settings={"camera_state": state, "camera_optics": {"sensor_format": "super_35",
                                                               "focus_mode": "subject_head"}})
            optics = json.loads(job["settings_json"])["camera_optics"]
            self.assertEqual(optics["sensor_format"], "super_35")
            self.assertEqual(optics["focal_length_mm"], 35.0)
            with self.assertRaisesRegex(ValueError, "iso"):
                store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                    settings={"camera_state": state, "camera_optics": {"iso": 20000}})

    def test_face_costume_and_environment_have_separate_roles(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            assets = []
            for index in range(3):
                path = store.assets_dir / f"asset-{index}.png"
                path.write_bytes(b"fixture")
                assets.append(store.add_asset(kind="image", role="Референс", original_name=path.name,
                                              mime="image/png", size=7, sha256="fixture", path=path)["asset_id"])
            roles = dict(zip(assets, ("Лицо героя", "Костюм", "Окружение")))
            owners = {assets[0]: "Вега", assets[1]: "Вега"}
            job = store.create_job(prompt="<Picture 1> face; <Picture 2> coat; <Picture 3> room",
                                   asset_ids=assets, settings={}, reference_roles=roles,
                                   reference_owners=owners)
            manifest = json.loads(job["manifest_json"])
            self.assertEqual([item["owner"] for item in manifest["images"]], ["Вега", "Вега", ""])
            self.assertIn("<Picture 2> belongs to Вега only and defines wardrobe and carried props for the named hero only", job["compiled_prompt"])
            self.assertIn("<Subject 1> is Вега; face and identity from <Picture 1>; clothing and carried props from <Picture 2>", job["compiled_prompt"])
            with self.assertRaisesRegex(ValueError, "не привязан"):
                store.create_job(prompt="<Picture 1> face; <Picture 2> coat; <Picture 3> room",
                                 asset_ids=assets, settings={}, reference_roles=roles,
                                 reference_owners={assets[0]: "Вега", assets[1]: "Другой"})

    def test_multi_image_scene_requires_native_tags_for_each_sheet(self):
        with self.assertRaisesRegex(ValueError, "<Environment 1> не является"):
            validate_image_bindings("<Environment 1> room; <Picture 2> ADAM", 3)
        with self.assertRaisesRegex(ValueError, "<Picture 3>"):
            validate_image_bindings("<Picture 1> room; <Picture 2> ADAM; VEGA is the cat", 3)
        validate_image_bindings("<Picture 1> room; <Picture 2> ADAM; <Picture 3> VEGA", 3)

    def test_character_sheets_get_distinct_subjects(self):
        prompt = "<Picture 1> = ALEX / TENANT ONLY.\n<Picture 2> = ANDREAS / NEIGHBOUR ONLY."
        contract = character_reference_contract(prompt, 2)
        self.assertIn("<Subject 1> is ALEX; only <Picture 1>", contract)
        self.assertIn("<Subject 2> is ANDREAS; only <Picture 2>", contract)

    def test_absent_character_sheet_is_rejected(self):
        prompt = ("<Picture 1> = MICHALIS / TECHNICIAN ONLY.\n"
                  "MICHALIS MUST NOT APPEAR PHYSICALLY ANYWHERE IN THIS 10-SECOND CLIP.")
        with self.assertRaisesRegex(ValueError, "MICHALIS запрещён"):
            character_reference_contract(prompt, 1)

    def test_visual_mode_rejects_explicit_2d_realism_conflict(self):
        with self.assertRaisesRegex(ValueError, "2D.*Реализм"):
            validate_visual_mode("STYLE: ESTABLISHED 2D ADULT ANIMATED SITCOM", "realistic")
        validate_visual_mode("STYLE: ESTABLISHED 2D ADULT ANIMATED SITCOM", "animation2d")
        validate_visual_mode("Photoreal scene. NO 2D animation.", "realistic")

    def test_auto_reference_role_is_left_for_the_director(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "look.png"
            path.write_bytes(b"fixture")
            asset = store.add_asset(kind="image", role="Авто — определить по содержимому",
                                    original_name="look.png", mime="image/png", size=7,
                                    sha256="fixture", path=path)
            job = store.create_job(
                prompt="Use the picture as the animation look reference.",
                asset_ids=[asset["asset_id"]], settings={"mode": "replace"},
                reference_numbers={asset["asset_id"]: 1},
                reference_roles={asset["asset_id"]: "Авто — определить по содержимому"},
            )
            self.assertEqual(json.loads(job["manifest_json"])["images"][0]["role"], "Авто — определить по содержимому")
            self.assertNotIn("Авто — определить", job["compiled_prompt"])

    def test_reference_role_selection_reaches_compiled_model_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = []
            for name, kind in (("hero.png", "image"), ("scene.mp4", "video")):
                path = store.assets_dir / name
                path.write_bytes(b"fixture")
                ids.append(store.add_asset(kind=kind, role="Лицо героя" if kind == "image" else "Исходное видео",
                                           original_name=name, mime="image/png" if kind == "image" else "video/mp4",
                                           size=7, sha256="fixture", path=path)["asset_id"])
            job = store.create_job(prompt="<Picture 1> crosses the room following <Video 1>.",
                                   asset_ids=ids, settings={"mode": "realistic"},
                                   reference_numbers={ids[0]: 1, ids[1]: 1},
                                   reference_roles={ids[0]: "Лицо героя", ids[1]: "Движение"})
            self.assertIn("<Picture 1> defines this character's face", job["compiled_prompt"])
            self.assertIn("<Video 1> provides body movement, choreography", job["compiled_prompt"])
            self.assertIn("camera, location and lighting follow their assigned references", job["compiled_prompt"])
            self.assertIn("VIDEO PERFORMANCE DIRECTION: The movement video lends only its performer's action order",
                          job["compiled_prompt"])
            self.assertIn("CLEAN FRAME: The reference videos lend only movement, camera path and spatial depth.",
                          job["compiled_prompt"])
            self.assertEqual(json.loads(job["manifest_json"])["videos"][0]["role"], "Движение")
            self.assertIn("The camera travels with the character to hold a near-constant screen size", job["compiled_prompt"])
            self.assertEqual(store.get_asset(ids[1])["role"], "Исходное видео")
            with self.assertRaisesRegex(ValueError, "роли референса|Неизвестная роль"):
                store.create_job(prompt="test", asset_ids=ids, settings={},
                                 reference_roles={ids[0]: "Окружение", ids[1]: "Инструкция модели"})

    def test_only_full_scene_image_can_anchor_a_guide(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "scene.png"
            path.write_bytes(b"frame")
            image = store.add_asset(kind="image", role="Лицо героя", original_name="scene.png",
                                    mime="image/png", size=5, sha256="frame", path=path)
            args = dict(prompt="<Picture 1> shows the room", asset_ids=[image["asset_id"]],
                        reference_numbers={image["asset_id"]: 1})
            with self.assertRaisesRegex(ValueError, "ролью"):
                store.create_job(**args, settings={"guide_frames": [[1, 24]]},
                                 reference_roles={image["asset_id"]: "Лицо героя"})
            job = store.create_job(**args, settings={"guide_frames": [[1, 24]]},
                                   reference_roles={image["asset_id"]: "Кадр сцены"})
            self.assertEqual(json.loads(job["settings_json"])["guide_frames"], [[1, 24]])
            self.assertIn("<Picture 1> anchors", job["compiled_prompt"])

    def test_visual_mode_reaches_compiled_prompt_without_rewriting_user_prompt(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"H3_ENABLED_STYLE_ADAPTERS": "style3d,cel2d,sketch2d"}
        ):
            store = StudioStore(Path(temp))
            path = store.assets_dir / "fake.png"
            path.write_bytes(b"test")
            asset = store.add_asset(kind="image", role="hero", original_name="hero.png",
                                    mime="image/png", size=4, sha256="hash", path=path)
            prompt = "<Picture 1> walks into the room."
            job = store.create_job(prompt=prompt, asset_ids=[asset["asset_id"]],
                                   settings={"quality": "balanced", "mode": "animation3d"})
            self.assertEqual(job["original_prompt"], prompt)
            self.assertIn("FULLY STYLIZED 3D ANIMATED FEATURE FILM", job["compiled_prompt"])
            self.assertIn("no photoreal fur", job["compiled_prompt"].lower())
            self.assertIn("facial acting", job["compiled_prompt"].lower())
            self.assertTrue(job["compiled_prompt"].endswith(prompt))
            self.assertEqual(json.loads(job["settings_json"])["mode"], "animation3d")
            self.assertEqual(json.loads(job["settings_json"])["style_adapter"], "none")
            self.assertEqual(json.loads(job["settings_json"])["steps"], 24)  # default per DECISIONS 16.10 and 04.11
            self.assertFalse(json.loads(job["settings_json"])["emotional_direction"])  # DECISIONS 22.10: precompiled
            with self.assertRaisesRegex(ValueError, "визуальный режим"):
                store.create_job(prompt=prompt, asset_ids=[asset["asset_id"]],
                                 settings={"mode": "unknown"})
            with self.assertRaisesRegex(ValueError, "ControlNet\\) удалён"):
                store.create_job(prompt=prompt, asset_ids=[asset["asset_id"]],
                                 settings={"motion_control": "pose"})
            # LoRA availability comes from GPU capabilities; unreachable GPU (None) allows only "none".
            with self.assertRaisesRegex(ValueError, "недоступна"):
                store.create_job(prompt=prompt, asset_ids=[asset["asset_id"]],
                                 settings={"style_adapter": "style3d"})
            with patch.dict("os.environ", {"H3_ENABLED_STYLE_ADAPTERS": "style3d"}):
                with self.assertRaisesRegex(ValueError, "недоступна"):
                    store.create_job(prompt=prompt, asset_ids=[asset["asset_id"]],
                                     settings={"style_adapter": "style3d"}, style_adapters=set())
            lora = store.create_job(prompt=prompt, asset_ids=[asset["asset_id"]],
                                    settings={"style_adapter": "style3d"}, style_adapters={"style3d"})
            self.assertEqual(json.loads(lora["settings_json"])["style_adapter"], "style3d")

    def test_emotion_direction_is_explicit_and_off_by_default_in_every_visual_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "hero.png"
            path.write_bytes(b"test")
            asset = store.add_asset(kind="image", role="hero", original_name=path.name,
                                    mime="image/png", size=4, sha256="hash", path=path)
            for mode in ("replace", "realistic", "animation2d", "animation3d"):
                job = store.create_job(prompt="<Picture 1> reacts", asset_ids=[asset["asset_id"]],
                                       settings={"mode": mode, "emotional_direction": True})
                self.assertTrue(json.loads(job["settings_json"])["emotional_direction"], mode)
            default = store.create_job(prompt="<Picture 1> reacts", asset_ids=[asset["asset_id"]], settings={})
            # 05.11: absent means no GPU Qwen director (DECISIONS 22.10); asking for it explicitly still works
            self.assertFalse(json.loads(default["settings_json"])["emotional_direction"])

    def test_2d_mode_does_not_force_a_lora_but_preserves_explicit_choice(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"H3_ENABLED_STYLE_ADAPTERS": "style3d,cel2d,sketch2d"}
        ):
            store = StudioStore(Path(temp))
            path = store.assets_dir / "fake.png"
            path.write_bytes(b"test")
            asset = store.add_asset(kind="image", role="hero", original_name="hero.png",
                                    mime="image/png", size=4, sha256="hash", path=path)
            cel = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                   settings={"mode": "animation2d", "style_adapter": "none"})
            self.assertEqual(json.loads(cel["settings_json"])["style_adapter"], "none")
            # The sketch2d LoRA was never installed on a GPU server; it is no longer an option.
            with self.assertRaisesRegex(ValueError, "недоступна"):
                store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]],
                                 settings={"mode": "animation2d", "style_adapter": "sketch2d"},
                                 style_adapters={"style3d", "sketch2d"})

    def test_replace_scenario_requires_and_records_character_swap_lora(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "fake.bin"
            path.write_bytes(b"test")
            image = store.add_asset(kind="image", role="hero", original_name="hero.png",
                                    mime="image/png", size=4, sha256="image", path=path)
            video = store.add_asset(kind="video", role="motion", original_name="source.mp4",
                                    mime="video/mp4", size=4, sha256="video", path=path)
            request = dict(prompt="Replace <Video 1> with <Picture 1>.",
                           asset_ids=[image["asset_id"], video["asset_id"]],
                           settings={"mode": "replace", "style_adapter": "none"},
                           reference_roles={video["asset_id"]: "Исходное видео"})
            with self.assertRaisesRegex(ValueError, "Character Swap LoRA не объявлена"):
                store.create_job(**request)
            job = store.create_job(**request, style_adapters={"character_swap"})
            settings = json.loads(job["settings_json"])
            self.assertEqual(settings["style_adapter"], "character_swap")
            trial = store.create_job(**{**request, "settings": {"mode": "replace", "style_adapter": "none",
                                                               "no_lora_swap_trial": True}}, style_adapters={"character_swap"})
            self.assertEqual(json.loads(trial["settings_json"])["style_adapter"], "none")
            self.assertEqual(settings["motion_control"], "none")
            # «Движение и камера» lends motion only: plain Ref2VA, the hero's own hair (0062f40a, 2026-10-08).
            motion = store.create_job(**{**request, "reference_roles": {video["asset_id"]: "Движение и камера"}},
                                      style_adapters={"character_swap", "realism_people"})
            self.assertEqual(json.loads(motion["settings_json"])["style_adapter"], "none")
            auto = store.create_job(**{**request, "settings": {"mode": "replace", "style_adapter": "auto"},
                                       "reference_roles": {video["asset_id"]: "Движение и камера"}},
                                    style_adapters={"character_swap"})
            self.assertNotEqual(json.loads(auto["settings_json"])["style_adapter"], "character_swap")

    def test_every_draft_save_keeps_the_version_it_replaces(self):
        """1.11: the canvas was replaced by {"prompt": "first"}; earlier versions must survive a bad save."""
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            store.save_draft({"scenes": [{"title": "White Planet"}]})
            store.save_draft({"prompt": "first"})
            self.assertEqual([(h["revision"], h["scenes"]) for h in store.draft_history()], [(1, ["White Planet"])])
            self.assertEqual(store.draft_version(1), {"scenes": [{"title": "White Planet"}]})
            for n in range(40):
                store.save_draft({"n": n})
            self.assertEqual(len(store.draft_history()), 30)

    def test_draft_local_numbers_ignore_global_library_and_jobs_are_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "fake.png"
            path.write_bytes(b"test")
            assets = [store.add_asset(kind="image", role="hero", original_name=f"{n}.png",
                                      mime="image/png", size=4, sha256="hash", path=path)
                      for n in range(5)]
            one = store.create_job(prompt="<Picture 1> waves", asset_ids=[assets[4]["asset_id"]],
                                   reference_numbers={assets[4]["asset_id"]: 1}, settings={"quality": "quick"})
            self.assertEqual(json.loads(one["manifest_json"])["images"][0]["asset_id"], assets[4]["asset_id"])
            self.assertTrue(one["compiled_prompt"].endswith("<Picture 1> waves"))
            two = store.create_job(prompt="<Picture 1> waves", asset_ids=[assets[3]["asset_id"]],
                                   reference_numbers={assets[3]["asset_id"]: 1}, settings={"quality": "quick"})
            self.assertEqual(json.loads(store.get_job(one["job_id"])["manifest_json"])["images"][0]["asset_id"], assets[4]["asset_id"])
            self.assertNotEqual(one["job_id"], two["job_id"])

    def test_local_video_soundtrack_precedes_standalone_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "fake.bin"
            path.write_bytes(b"test")
            video = store.add_asset(kind="video", role="source", original_name="source.mp4",
                                    mime="video/mp4", size=4, sha256="hash", path=path)
            audio = store.add_asset(kind="audio", role="Музыка", original_name="suno-track.wav",
                                    mime="audio/wav", size=4, sha256="hash", path=path)
            job = store.create_job(prompt="<Video 1> <Audio 1> <Audio 2>",
                                   asset_ids=[video["asset_id"], audio["asset_id"]],
                                   reference_numbers={video["asset_id"]: 1, audio["asset_id"]: 2},
                                   settings={"quality": "quick", "use_video_audio": True})
            self.assertTrue(job["compiled_prompt"].endswith("<Video 1> <Audio 1> <Audio 2>"))
            self.assertEqual(json.loads(job["manifest_json"])["audios"][0]["role"], "Музыка")

    def test_old_motion_control_none_still_loads_and_pose_or_canny_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "clip.mp4"
            path.write_bytes(b"test")
            video = store.add_asset(kind="video", role="motion", original_name="clip.mp4",
                                    mime="video/mp4", size=4, sha256="hash", path=path)
            for settings in ({"motion_control": "none", "quality": "quick"}, {"quality": "quick"}):
                job = store.create_job(prompt="Follow <Video 1>", asset_ids=[video["asset_id"]], settings=settings)
                self.assertEqual(json.loads(job["settings_json"])["motion_control"], "none")
                self.assertEqual(json.loads(job["settings_json"])["width"], 672)  # 9:16 by default
            for removed in ("pose", "canny"):
                with self.assertRaisesRegex(ValueError, "ControlNet\\) удалён"):
                    store.create_job(prompt="Follow <Video 1>", asset_ids=[video["asset_id"]],
                                     settings={"motion_control": removed, "quality": "quick"})

    def test_motion_role_cannot_silently_use_native_video_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            photo_path = store.assets_dir / "hero.png"
            photo_path.write_bytes(b"image")
            video_path = store.assets_dir / "dance.mp4"
            video_path.write_bytes(b"video")
            photo = store.add_asset(kind="image", role="Персонаж целиком", original_name="hero.png",
                                    mime="image/png", size=5, sha256="image", path=photo_path)
            video = store.add_asset(kind="video", role="Движение", original_name="dance.mp4",
                                    mime="video/mp4", size=5, sha256="video", path=video_path)
            with self.assertRaisesRegex(ValueError, "роль видео «Исходное видео»"):
                store.create_job(prompt="Follow <Video 1> using <Picture 1>",
                                 asset_ids=[photo["asset_id"], video["asset_id"]],
                                 settings={"mode": "replace", "motion_control": "none",
                                           "use_video_audio": True, "quality": "quick"})
            movement_only = store.create_job(prompt="Follow <Video 1> using <Picture 1>",
                                             asset_ids=[photo["asset_id"], video["asset_id"]],
                                             settings={"mode": "realistic", "motion_control": "none",
                                                       "use_video_audio": True, "quality": "quick"})
            self.assertIn("VIDEO PERFORMANCE DIRECTION:", movement_only["compiled_prompt"])
            self.assertIn("The camera travels with the character", movement_only["compiled_prompt"])
            job = store.create_job(prompt="Follow <Video 1> using <Picture 1>",
                                   asset_ids=[photo["asset_id"], video["asset_id"]],
                                   settings={"mode": "replace", "motion_control": "none",
                                             "use_video_audio": True, "quality": "quick"},
                                   reference_roles={video["asset_id"]: "Исходное видео"},
                                   style_adapters={"character_swap"})
            self.assertTrue(json.loads(job["settings_json"])["use_video_audio"])
            kept = store.create_job(prompt="Follow <Video 1> using <Picture 1>",
                                    asset_ids=[photo["asset_id"], video["asset_id"]],
                                    settings={"mode": "replace", "motion_control": "none", "use_video_audio": True,
                                              "keep_source_audio": True, "quality": "quick"},
                                    reference_roles={video["asset_id"]: "Исходное видео"},
                                    style_adapters={"character_swap"})
            # the original track goes under the render anyway: H3 does not sample the soundtrack for nothing
            self.assertFalse(json.loads(kept["settings_json"])["use_video_audio"])
            combined = store.create_job(prompt="Use <Video 1> motion and camera with <Picture 1>",
                                        asset_ids=[photo["asset_id"], video["asset_id"]],
                                        settings={"mode": "replace", "motion_control": "none", "quality": "quick"},
                                        reference_roles={video["asset_id"]: "Движение и камера"},
                                        style_adapters={"character_swap"})
            self.assertIn("choreography, timing, gestures and expressions together with camera movement and framing",
                          combined["compiled_prompt"])

    def test_movement_video_binds_inside_subject_and_silent_scene_drops_voice_block(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            photo_path = store.assets_dir / "hero.png"
            video_path = store.assets_dir / "dance.mp4"
            photo_path.write_bytes(b"image")
            video_path.write_bytes(b"video")
            photo = store.add_asset(kind="image", role="Персонаж целиком", original_name="hero.png",
                                    mime="image/png", size=5, sha256="image", path=photo_path)
            video = store.add_asset(kind="video", role="Движение и камера", original_name="dance.mp4",
                                    mime="video/mp4", size=5, sha256="video", path=video_path)
            ids = [photo["asset_id"], video["asset_id"]]
            job = store.create_job(prompt="VEGA dances like <Video 1>. <Picture 1> is VEGA. No talking.",
                                   asset_ids=ids, settings={"mode": "replace", "motion_control": "none", "quality": "quick"},
                                   reference_roles={video["asset_id"]: "Движение и камера"},
                                   reference_owners={photo["asset_id"]: "VEGA"},
                                   style_adapters={"character_swap"})
            self.assertIn("<Subject 1> is VEGA; body and silhouette from <Picture 1>.", job["compiled_prompt"])
            # The performer in the movement video lends motion only, never hair or beard (2026-10-05).
            self.assertIn("attribute_transfer: only the body motion, pose progression and action timing of the "
                          "separate performer in <Video 1> are transferred onto VEGA. VEGA's hairstyle, hair length, "
                          "hair color, facial hair or clean-shaven face, face and visible anatomical features come only "
                          "from <Picture 1>", job["compiled_prompt"])
            self.assertNotIn("VOICE PERFORMANCE DIRECTION", job["compiled_prompt"])
            # Camera from the video: focus tracks the face; no follow-to-hold-size rule against the video's path.
            self.assertIn("preserve source framing and focus", job["compiled_prompt"])
            self.assertNotIn("MOTION FOCUS AND SUBJECT SCALE", job["compiled_prompt"])
            self.assertNotIn("HERO FACE FOCUS", job["compiled_prompt"])
            self.assertIn("anatomy, build and hands from the assigned identity pictures", job["compiled_prompt"])
            talking = store.create_job(prompt="VEGA dances like <Video 1> and sings. <Picture 1> is VEGA.",
                                       asset_ids=ids, settings={"mode": "replace", "motion_control": "none", "quality": "quick"},
                                       reference_roles={video["asset_id"]: "Движение и камера"},
                                       reference_owners={photo["asset_id"]: "VEGA"},
                                       style_adapters={"character_swap"})
            self.assertIn("VOICE PERFORMANCE DIRECTION", talking["compiled_prompt"])

    def test_picture_only_recipe_keeps_voice_block(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            photo_path = store.assets_dir / "hero.png"
            photo_path.write_bytes(b"image")
            photo = store.add_asset(kind="image", role="Персонаж целиком", original_name="hero.png",
                                    mime="image/png", size=5, sha256="image", path=photo_path)
            job = store.create_job(prompt="<Picture 1> sleeps. No talking.", asset_ids=[photo["asset_id"]],
                                   settings={"mode": "replace", "quality": "quick"})
            self.assertIn("VOICE PERFORMANCE DIRECTION", job["compiled_prompt"])

    def test_reopen_preserves_gap_mapping_and_fifo(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            asset_path = store.assets_dir / "fake.png"
            asset_path.write_bytes(b"test")
            first = store.add_asset(kind="image", role="hero", original_name="one.png",
                                    mime="image/png", size=4, sha256="hash", path=asset_path)
            second = store.add_asset(kind="image", role="hero", original_name="two.png",
                                     mime="image/png", size=4, sha256="hash", path=asset_path)
            self.assertEqual(second["number"], first["number"] + 1)
            one = store.create_job(prompt="<Picture 2> waves", asset_ids=[second["asset_id"]], settings={"quality": "quick"})
            two = store.create_job(prompt="<Picture 1> runs", asset_ids=[first["asset_id"]], settings={"quality": "max"})
            reopened = StudioStore(Path(temp))
            self.assertEqual(reopened.oldest_pending()["job_id"], one["job_id"])
            self.assertTrue(reopened.get_job(one["job_id"])["compiled_prompt"].endswith("<Picture 1> waves"))
            self.assertEqual(len(reopened.list_jobs()), 2)
            reopened.transition(one["job_id"], "uploading")
            self.assertEqual(reopened.active_job()["job_id"], one["job_id"])
            self.assertEqual(reopened.oldest_pending()["job_id"], two["job_id"])

    def test_missing_reference_is_not_queued(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            with self.assertRaises(ValueError):
                store.create_job(prompt="<Picture 1> waves", asset_ids=[], settings={})
            self.assertEqual(store.list_jobs(), [])

    def test_hide_failed_job_preserves_diagnostic_record(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "reference.png"
            path.write_bytes(b"reference")
            asset = store.add_asset(kind="image", role="Лицо героя", original_name="reference.png",
                                    mime="image/png", size=9, sha256="fixture", path=path)
            job = store.create_job(prompt="<Picture 1> waves", asset_ids=[asset["asset_id"]], settings={})
            store.transition(job["job_id"], "failed", error="HTTP 503 from GPU")

            store.hide_failed_job(job["job_id"])

            self.assertEqual(store.list_jobs(), [])
            preserved = store.get_job(job["job_id"])
            self.assertEqual(preserved["hidden"], 1)
            self.assertEqual(preserved["state"], "failed")
            self.assertEqual(preserved["error"], "HTTP 503 from GPU")
            self.assertTrue(Path(asset["path"]).is_file())

    def test_clear_visible_jobs_preserves_records_and_moves_results_to_trash(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            video = store.results_dir / "finished.mp4"
            video.write_bytes(b"video")
            with store.connect() as db:
                for job_id, state, output in (("done", "completed", str(video)),
                                              ("waiting", "queued_local", None),
                                              ("error", "failed", None)):
                    db.execute("INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,mapping_json,output_path) "
                               "VALUES (?,?,?,?,?,?,?,?)",
                               (job_id, state, "prompt", "prompt", "{}", "{}", "{}", output))
            result = store.clear_visible_jobs()
            self.assertEqual(result, {"hidden": 3, "cancelled_local": 1, "videos_in_trash": 1, "kept_active": 0})
            self.assertEqual(store.list_jobs(), [])
            self.assertEqual(store.get_job("waiting")["state"], "cancelled")
            self.assertEqual(store.get_job("done")["hidden"], 1)
            self.assertTrue((store.results_dir / "trash" / "finished.mp4").is_file())
            self.assertFalse(video.exists())

    def test_clear_visible_jobs_keeps_active_job_and_hides_the_rest(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            with store.connect() as db:
                for job_id, state in (("waiting", "queued_local"), ("running", "needs_reconciliation"),
                                      ("stuck", "stopping"), ("old", "cancelled")):
                    db.execute("INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,mapping_json) "
                               "VALUES (?,?,?,?,?,?,?)",
                               (job_id, state, "prompt", "prompt", "{}", "{}", "{}"))
            result = store.clear_visible_jobs()
            self.assertEqual(result["kept_active"], 2)
            self.assertEqual(store.get_job("running")["hidden"], 0)
            self.assertEqual(store.get_job("stuck")["state"], "stopping")
            self.assertEqual(store.get_job("stuck")["hidden"], 0)
            self.assertEqual(store.get_job("old")["hidden"], 1)
            self.assertEqual(store.get_job("waiting")["state"], "cancelled")
            self.assertEqual(store.get_job("waiting")["hidden"], 1)

if __name__ == "__main__":
    unittest.main()


class GeneratedBlockStripTests(unittest.TestCase):
    def test_reopened_job_prompt_does_not_stack_generated_blocks(self):
        from local_studio.store import NATIVE_AUDIO_DIRECTION, VISUAL_MODES, strip_generated_blocks
        user = "SCENE\n\nORION says: \"Not tonight.\"\n\nHold.\n\nHold."
        stacked = "\n\n".join([
            VISUAL_MODES["realistic"], NATIVE_AUDIO_DIRECTION,
            "REFERENCE ROLE CONTRACT: <Picture 1> defines this character.",
            "CHARACTER REFERENCE BINDINGS:\n<Subject 1> is Orion.",
        ] * 3 + [user])
        self.assertEqual(strip_generated_blocks(stacked), user)


class DirectorStateHarmonyTests(unittest.TestCase):
    STATE = ("<Picture 1> dances.\n\nDIRECTOR STATE — apply only where compatible with the described action and "
             "reference roles:\nКамера: Clean digital.\nОбъектив: 35 mm.\nКадр: Full-body framing.\n"
             "Движение: Smooth tracking shot.\nФокус: Deep focus.\nСвет: Daylight.")

    def test_video_camera_drops_lens_shot_and_movement_cards(self):
        from local_studio.store import harmonize_director_state
        text, cards = harmonize_director_state(self.STATE, [{"role": "Движение и камера"}])
        self.assertFalse(cards)
        for gone in ("Объектив:", "Кадр:", "Движение:"):
            self.assertNotIn(gone, text)
        for kept in ("Камера:", "Фокус:", "Свет:"):
            self.assertIn(kept, text)

    def test_body_motion_keeps_cards_and_motion_block_skips_its_own_camera(self):
        from local_studio.store import harmonize_director_state, motion_focus_direction
        text, cards = harmonize_director_state(self.STATE, [{"role": "Движение"}])
        self.assertEqual(text, self.STATE)
        self.assertTrue(cards)
        rule = motion_focus_direction([{"role": "Движение"}], camera_from_cards=cards)
        self.assertNotIn("The camera travels with the character", rule)
        self.assertIn("Focus tracks the principal character's face", rule)

    def test_no_director_state_is_untouched(self):
        from local_studio.store import harmonize_director_state
        self.assertEqual(harmonize_director_state("plain", [{"role": "Камера"}]), ("plain", False))


class SingleHeroOwnershipTests(unittest.TestCase):
    def test_unowned_costume_goes_to_the_only_hero(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StudioStore(Path(directory))
            ids = []
            for name in ("face.png", "costume.png"):
                path = store.assets_dir / name
                path.write_bytes(b"fixture")
                ids.append(store.add_asset(kind="image", role="Лицо героя", original_name=name, mime="image/png",
                                           size=7, sha256=name, path=path)["asset_id"])
            job = store.create_job(prompt="ANNA <Picture 1> wears <Picture 2>.", asset_ids=ids,
                                   settings={"mode": "realistic"},
                                   reference_roles={ids[0]: "Лицо героя", ids[1]: "Костюм"},
                                   reference_owners={ids[0]: "ANNA"})
            images = json.loads(job["manifest_json"])["images"]
            self.assertEqual([image["owner"] for image in images], ["ANNA", "ANNA"])
            self.assertIn("<Subject 1> is ANNA; face and identity from <Picture 1>; clothing and carried props "
                          "from <Picture 2>", job["compiled_prompt"])


class PerformerPairTests(unittest.TestCase):
    def make(self, directory, files):
        store = StudioStore(Path(directory))
        ids = []
        for name, kind, mime in files:
            path = store.assets_dir / name
            path.write_bytes(b"fixture" * 300)
            ids.append(store.add_asset(kind=kind, role="Лицо героя" if kind == "image" else "Исходное видео",
                                       original_name=name, mime=mime, size=2100, sha256=name, path=path)["asset_id"])
        return store, ids

    def two_heroes_and_video(self, directory):
        return self.make(directory, [("a.png", "image", "image/png"), ("b.png", "image", "image/png"),
                                     ("m.mp4", "video", "video/mp4")])

    def test_auto_video_is_refused_once_a_hero_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            store, ids = self.make(directory, [("a.png", "image", "image/png"), ("m.mp4", "video", "video/mp4")])
            with self.assertRaisesRegex(ValueError, "«Авто» не привязывает движение"):
                store.create_job(prompt="ANNA <Picture 1> like <Video 1>.", asset_ids=ids, settings={},
                                 reference_roles={ids[0]: "Лицо героя", ids[1]: "Авто — определить по содержимому"},
                                 reference_owners={ids[0]: "ANNA"})

    def test_two_heroes_need_to_say_whose_motion_it_is(self):
        with tempfile.TemporaryDirectory() as directory:
            store, ids = self.two_heroes_and_video(directory)
            with self.assertRaisesRegex(ValueError, "чьё движение"):
                store.create_job(prompt="ANNA <Picture 1> and ADAM <Picture 2> like <Video 1>.", asset_ids=ids,
                                 settings={}, reference_roles={ids[0]: "Лицо героя", ids[1]: "Лицо героя", ids[2]: "Движение"},
                                 reference_owners={ids[0]: "ANNA", ids[1]: "ADAM"})

    def test_multi_performer_pairs_compile_when_gpu_explicitly_advertises_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            store, ids = self.two_heroes_and_video(directory)
            job = store.create_job(
                prompt="ANNA <Picture 1> and ADAM <Picture 2> dance like <Video 1>.", asset_ids=ids,
                settings={"mode": "replace"},
                reference_roles={ids[0]: "Лицо героя", ids[1]: "Лицо героя", ids[2]: "Движение и камера"},
                reference_owners={ids[0]: "ANNA", ids[1]: "ADAM"},
                reference_performers={ids[2]: {"pairs": [{"owner": "ANNA", "performer": "the woman on the left"},
                                                         {"owner": "ADAM", "performer": "the man on the right"}]}},
                style_adapters={"character_swap"})
            prompt = job["compiled_prompt"]
            self.assertIn("of the woman on the left in <Video 1> are transferred onto ANNA", prompt)
            self.assertIn("of the man on the right in <Video 1> are transferred onto ADAM", prompt)
            video = json.loads(job["manifest_json"])["videos"][0]
            self.assertEqual([pair["owner"] for pair in video["performers"]], ["ANNA", "ADAM"])
            # A movement video lends motion only: no in-place Character Swap (it kept the dancers' hair, 0062f40a).
            self.assertEqual(json.loads(job["settings_json"])["style_adapter"], "none")

    def test_multi_performer_replacement_fails_before_queue_without_adapter_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            store, ids = self.two_heroes_and_video(directory)
            with self.assertRaisesRegex(ValueError, "Character Swap LoRA не объявлена"):
                store.create_job(
                    prompt="ANNA <Picture 1> and ADAM <Picture 2> replace the people in <Video 1>.",
                    asset_ids=ids, settings={"mode": "realistic"},
                    reference_roles={ids[0]: "Лицо героя", ids[1]: "Лицо героя", ids[2]: "Исходное видео"},
                    reference_owners={ids[0]: "ANNA", ids[1]: "ADAM"},
                    reference_performers={ids[2]: {"pairs": [{"owner": "ANNA", "performer": "left"},
                                                             {"owner": "ADAM", "performer": "right"}]}})

    def test_replace_one_and_keep_the_other(self):
        with tempfile.TemporaryDirectory() as directory:
            store, ids = self.make(directory, [("a.png", "image", "image/png"), ("m.mp4", "video", "video/mp4")])
            job = store.create_job(
                prompt="ANNA <Picture 1> takes the place of the woman on the left in <Video 1>.", asset_ids=ids,
                settings={"mode": "replace"},
                reference_roles={ids[0]: "Персонаж целиком", ids[1]: "Исходное видео"},
                reference_owners={ids[0]: "ANNA"},
                reference_performers={ids[1]: {"pairs": [{"owner": "ANNA", "performer": "the woman on the left"}],
                                               "keep_others": True}},
                style_adapters={"character_swap"})
            prompt = job["compiled_prompt"]
            self.assertIn("screen position, pose progression and action timing of the woman on the left in <Video 1>", prompt)
            self.assertIn("Every other person in <Video 1> stays exactly as in the video", prompt)

    def test_pairs_validate_heroes_and_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            store, ids = self.two_heroes_and_video(directory)
            base = dict(prompt="ANNA <Picture 1> ADAM <Picture 2> <Video 1>", asset_ids=ids, settings={},
                        reference_owners={ids[0]: "ANNA", ids[1]: "ADAM"})
            roles = {ids[0]: "Лицо героя", ids[1]: "Лицо героя", ids[2]: "Движение"}
            with self.assertRaisesRegex(ValueError, "не привязан"):
                store.create_job(**base, reference_roles=roles,
                                 reference_performers={ids[2]: {"pairs": [{"owner": "MARK", "performer": "left"}]}})
            with self.assertRaisesRegex(ValueError, "только в одной паре"):
                store.create_job(**base, reference_roles=roles, reference_performers={ids[2]: {"pairs": [
                    {"owner": "ANNA", "performer": "left"}, {"owner": "ADAM", "performer": "left"}]}})
            with self.assertRaisesRegex(ValueError, "только для видео"):
                store.create_job(**base, reference_roles={**roles, ids[2]: "Камера"},
                                 reference_performers={ids[2]: {"pairs": [{"owner": "ANNA", "performer": "left"}]}})

    def test_official_template_passes_for_one_hero(self):
        with tempfile.TemporaryDirectory() as directory:
            store, ids = self.make(directory, [("a.png", "image", "image/png"), ("c.png", "image", "image/png"),
                                               ("m.mp4", "video", "video/mp4")])
            template = (Path(__file__).resolve().parents[1] / "docs/prompts/hero_costume_motion.txt").read_text()
            job = store.create_job(prompt=template, asset_ids=ids, settings={"mode": "realistic", "duration": 15, "aspect_ratio": "9:16"},
                                   reference_numbers={ids[0]: 1, ids[1]: 2, ids[2]: 1},
                                   reference_roles={ids[0]: "Лицо героя", ids[1]: "Костюм", ids[2]: "Движение"},
                                   reference_owners={ids[0]: "ANNA"},
                                   style_adapters={"character_swap"})
            self.assertEqual(json.loads(job["settings_json"])["prompt_format"], "h3_ir")


class MotionFocusDirectionTests(unittest.TestCase):
    def test_full_body_dance_keeps_hands_and_feet_in_frame(self):
        from local_studio.store import add_default_hero_face_focus
        hero = [{"job_role": "Лицо героя"}]
        prompt = ("subject_definitions:\n<Subject 1> is LEO from <Picture 1>.\n\n"
                  "summary:\nA dance.\n\nretention_analysis:\n<Picture 1>: fully_preserved.\n\n"
                  "detailed_description:\n[Shot 1] Full-body choreography with hands and feet visible.\n\n"
                  "overall_soundscape:\nFootsteps.\n\nnon_diegetic_music:\nA beat.\n")
        compiled = add_default_hero_face_focus(prompt, hero, official_format=True)
        self.assertIn("Preserve enough room for both hands, feet", compiled)
        self.assertNotIn("prefer a medium close-up", compiled)

    def test_default_face_focus_is_not_suppressed_by_blurred_background_direction(self):
        from local_studio.store import add_default_hero_face_focus
        prompt = ("subject_definitions:\n<Subject 1> is ANNA from <Picture 1>.\n\n"
                  "summary:\nA portrait of ANNA.\n\nretention_analysis:\nANNA stays recognizable.\n\n"
                  "detailed_description:\nANNA stands by a mirror and smiles; the background falls softly out of focus.\n\n"
                  "overall_soundscape:\nQuiet room tone.\n\nnon_diegetic_music:\nN/A")
        hero = [{"role": "Лицо героя", "job_role": "Лицо героя", "job_owner": "ANNA"}]
        compiled = add_default_hero_face_focus(prompt, hero, official_format=True)
        self.assertIn("HERO FACE FOCUS:", compiled)
        self.assertLess(compiled.index("FACE FOCUS:"), compiled.index("overall_soundscape:"))
        explicit = prompt.replace("background falls softly out of focus.",
                                  "background falls softly out of focus while ANNA's face and eyes stay in focus.")
        self.assertEqual(add_default_hero_face_focus(explicit, hero, official_format=True), explicit)

    def test_only_movement_videos_get_the_focus_rule(self):
        from local_studio.store import motion_focus_direction
        self.assertEqual(motion_focus_direction([]), "")
        self.assertEqual(motion_focus_direction([{"role": "Исходное видео"}, {"role": "Камера"}]), "")
        self.assertIn("follows forward", motion_focus_direction([{"job_role": "Движение"}]))
        self.assertNotIn("follows forward", motion_focus_direction([{"role": "Движение и камера"}]))
        self.assertIn("each sudden eye opening is a crisp eyelid movement", motion_focus_direction([{"role": "Движение"}]))

    def test_clean_frame_only_for_reference_videos_that_lend_motion_or_camera(self):
        from local_studio.store import clean_frame_direction
        self.assertEqual(clean_frame_direction([{"role": "Исходное видео"}, {"role": "Продолжение"}]), "")
        for role in ("Движение", "Движение и камера", "Камера", "Ритм сцены", "Актёрская игра"):
            self.assertIn("free of the lettering", clean_frame_direction([{"role": role}]), role)


def test_cancelled_job_card_can_be_hidden(tmp_path):
    import local_studio.store as store_module
    store = store_module.StudioStore(tmp_path)
    with store.connect() as db:
        db.execute("INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,mapping_json) "
                   "VALUES ('c1','cancelled','p','p','{}','{}','{}')")
    store.hide_failed_job("c1")
    assert store.get_job("c1")["hidden"] == 1


class OfficialFormatMotionTransferTests(unittest.TestCase):
    def test_source_swap_identity_contract_and_conflict(self):
        from local_studio.store import source_swap_identity_contract
        prompt = "subject_definitions:\n<Subject 1> is ADAM.\n\nretention_analysis:\nKeep motion.\n\ndetailed_description:\n[Shot 1] ADAM moves."
        images = ({"job_role": "Персонаж целиком", "job_owner": "ADAM"},
                  {"job_role": "Персонаж целиком", "job_owner": "VEGA"})
        videos = ({"job_role": "Исходное видео", "job_performers": [
            {"owner": "ADAM", "performer": "woman"}, {"owner": "VEGA", "performer": "cat"}]},)
        out = source_swap_identity_contract(prompt, images, videos)
        self.assertIn("ADAM", out)
        self.assertIn("<Picture 1>", out)
        self.assertIn("<Picture 2>", out)
        self.assertIn("face from <Picture 1>, body, proportions and scale", out)
        self.assertEqual(source_swap_identity_contract(prompt, images, ()), prompt)
        with self.assertRaisesRegex(ValueError, "лица исходных"):
            source_swap_identity_contract(prompt + " Match source faces, poses and timing.", images, videos)

    def test_paired_performers_get_attribute_transfer_in_retention(self):
        from local_studio.store import add_motion_attribute_transfer
        prompt = ("subject_definitions:\n<Subject 1> is ANNA.\n\nretention_analysis:\n"
                  "<Video 1>: partially_preserved - choreography.\n\ndetailed_description:\n[Shot 1]\nANNA dances.\n")
        images = ({"job_role": "Лицо героя", "job_owner": "ANNA"}, {"job_role": "Костюм", "job_owner": "ANNA"})
        videos = ({"job_role": "Движение и камера", "job_performers": [{"owner": "ANNA", "performer": "performer A"}]},)
        out = add_motion_attribute_transfer(prompt, images, videos)
        retention = out.split("retention_analysis:")[1].split("detailed_description:")[0]
        self.assertIn("<Video 1> (motion of ANNA): attribute_transfer - only the body motion, pose progression and "
                      "action timing of performer A in <Video 1> are transferred onto ANNA. ANNA's hairstyle", retention)
        self.assertIn("come only from <Picture 1>", retention)
        self.assertNotIn("come only from <Picture 1> and <Picture 2>", retention)
        self.assertIn("\n\ndetailed_description:\n[Shot 1]", out)
        # «Исходное видео» swaps in place; no motion-only sentence.
        source = ({**videos[0], "job_role": "Исходное видео"},)
        self.assertEqual(add_motion_attribute_transfer(prompt, images, source), prompt)


def test_motion_transfer_for_a_cat_hero_speaks_of_fur_not_hair():
    # A cat hero must not get «hairstyle, facial hair or clean-shaven face … costume reference».
    from local_studio.store import hero_is_animal, motion_transfer_sentence
    prompt = ("subject_definitions:\n<Subject 1> is ADAM from <Picture 2>: the young man with black hair.\n"
              "<Subject 2> is VEGA from <Picture 3>: the single small ginger-tabby cat.\n")
    assert hero_is_animal(prompt, "VEGA") and not hero_is_animal(prompt, "ADAM")
    cat = motion_transfer_sentence("VEGA", "<Video 1>", ["<Picture 3>"], "the cat", animal=True)
    assert "fur color" in cat and "hairstyle" not in cat and "clean-shaven" not in cat and "costume" not in cat
    assert "hairstyle" in motion_transfer_sentence("ADAM", "<Video 1>", ["<Picture 2>"], "the man")
