"""Jobs whose prompt is already in the official H3 six-section format go to H3 as written."""
import tempfile
import unittest
from pathlib import Path

from app.h3_ir import Reference, build_skeleton, shot_starts
from local_studio.store import StudioStore
from tests.test_h3_ir import good_ir


def official_prompt():
    sk = build_skeleton([Reference("image", 1, "Персонаж целиком", "ADAM"),
                         Reference("image", 2, "Лицо героя", "MARTIN"),
                         Reference("image", 3, "Окружение")], speaking_owners=["MARTIN"])
    return good_ir(sk, shot_starts([2, 3]))  # fits the default 5 s take (shot timeline gate)


class OfficialFormatStoreTests(unittest.TestCase):
    def test_animation_mode_reaches_ir_description_without_changing_identity_bindings(self):
        from app.h3_ir import parse_ir
        from local_studio.store import VISUAL_MODES, apply_ir_animation_mode
        with tempfile.TemporaryDirectory() as temp:
            store, ids, roles, owners = self._store_with_pictures(temp)
            prompt = official_prompt()
            job = store.create_job(prompt=prompt, asset_ids=ids, reference_roles=roles, reference_owners=owners,
                                   settings={"mode": "animation3d", "quality": "quick"})
            before, after = parse_ir(prompt), parse_ir(job['compiled_prompt'])
            self.assertIn(VISUAL_MODES['animation3d'], after['detailed_description'])
            for section in before:
                if section != 'detailed_description':
                    self.assertEqual(before[section], after[section])
            self.assertIn(before['detailed_description'], after['detailed_description'])
            self.assertEqual(apply_ir_animation_mode(job['compiled_prompt'], 'animation3d'), job['compiled_prompt'])

    def test_pasted_single_hero_prompt_recovers_labels_and_motion_owner(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = []
            for name, kind, role in (("anna.png", "image", "Лицо героя"),
                                     ("costume.png", "image", "Костюм"),
                                     ("motion.mp4", "video", "Движение")):
                path = store.assets_dir / name
                path.write_bytes(b"fixture")
                ids.append(store.add_asset(kind=kind, role=role, original_name=name,
                                           mime="video/mp4" if kind == "video" else "image/png",
                                           size=7, sha256=name, path=path)["asset_id"])
            prompt = (Path(__file__).resolve().parents[1] / "docs/prompts/hero_costume_motion.txt").read_text()
            prompt = prompt.replace("<Subject 1> is NOVA, whose face", "NOVA’s face", 1)
            prompt = prompt.replace("<Subject 1> (appears in [Shot 1]): fully_preserved", "NOVA: fully_preserved", 1)
            job = store.create_job(prompt=prompt, asset_ids=ids,
                                   reference_owners={ids[0]: "NOVA", ids[1]: "NOVA"},
                                   settings={"mode": "realistic", "quality": "quick", "duration": 10},
                                   style_adapters={"character_swap"})
            self.assertIn("<Subject 1> is NOVA, whose face", job["compiled_prompt"])
            self.assertIn("<Subject 1> (appears in [Shot 1]): fully_preserved", job["compiled_prompt"])
            self.assertEqual(json.loads(job["manifest_json"])["videos"][0]["owner"], "NOVA")

    def test_face_costume_and_owned_motion_share_one_subject(self):
        import json
        from app.main import _validate_official_manifest_prompt
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = []
            for name, kind, role in (("anna.png", "image", "Лицо героя"),
                                     ("outfit.png", "image", "Костюм"),
                                     ("motion.mp4", "video", "Движение и камера")):
                path = store.assets_dir / name
                path.write_bytes(b"fixture")
                ids.append(store.add_asset(kind=kind, role=role, original_name=name,
                                           mime="video/mp4" if kind == "video" else "image/png",
                                           size=7, sha256=name, path=path)["asset_id"])
            prompt = (Path(__file__).resolve().parents[1] / "docs/prompts/hero_costume_motion.txt").read_text()
            job = store.create_job(prompt=prompt, asset_ids=ids, reference_owners={i: "NOVA" for i in ids},
                                   settings={"mode": "replace", "quality": "quick", "duration": 15},
                                   style_adapters={"character_swap"})
            manifest = json.loads(job["manifest_json"])
            self.assertEqual(manifest["videos"][0]["owner"], "NOVA")
            _validate_official_manifest_prompt(job["compiled_prompt"], manifest, False)

    def test_missing_face_source_is_completed_from_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids, roles, owners = self._store_with_pictures(temp)
            prompt = official_prompt().replace(
                "whose full-body appearance and silhouette come from <Picture 1>",
                "whose full-body appearance and silhouette come from the attached portrait", 1,
            )
            job = store.create_job(prompt=prompt, asset_ids=ids, reference_roles=roles, reference_owners=owners,
                                   settings={"mode": "replace", "quality": "quick"})
            self.assertNotIn("<Picture 1>", prompt.split("\n", 2)[1])
            self.assertIn("<Picture 1>", job["compiled_prompt"].split("\n", 2)[1])

    def test_multiple_heroes_are_not_assigned_by_photo_order(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = []
            roles = {}
            for name, role in (("first-face.png", "Лицо героя"), ("first-outfit.png", "Костюм"),
                               ("second-face.png", "Лицо героя"), ("second-outfit.png", "Костюм")):
                path = store.assets_dir / name
                path.write_bytes(name.encode())
                asset = store.add_asset(kind="image", role=role, original_name=name, mime="image/png",
                                        size=len(name), sha256=name, path=path)
                ids.append(asset["asset_id"])
                roles[asset["asset_id"]] = role
            prompt = "subject_definitions:\n<Subject 1> is Anna.\n<Subject 2> is Adam."
            with self.assertRaisesRegex(ValueError, "порядок загрузки не определяет владельца"):
                store.create_job(prompt=prompt, asset_ids=ids, reference_roles=roles,
                                 settings={"mode": "realistic", "quality": "quick"})

    def _store_with_pictures(self, temp):
        store = StudioStore(Path(temp))
        ids, roles, owners = [], {}, {}
        for name, role, owner in (("adam.png", "Персонаж целиком", "ADAM"), ("martin.png", "Лицо героя", "MARTIN"),
                                  ("room.png", "Окружение", "")):
            path = store.assets_dir / name
            path.write_bytes(name.encode())
            asset = store.add_asset(kind="image", role=role, original_name=name, mime="image/png",
                                    size=len(name), sha256=name, path=path)
            ids.append(asset["asset_id"]); roles[asset["asset_id"]] = role
            if owner:
                owners[asset["asset_id"]] = owner
        return store, ids, roles, owners

    def test_prompt_goes_to_h3_unchanged_and_bypasses_planner_and_camera_text(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids, roles, owners = self._store_with_pictures(temp)
            prompt = official_prompt()
            job = store.create_job(prompt=prompt, asset_ids=ids, reference_roles=roles, reference_owners=owners,
                                   settings={"mode": "replace", "quality": "quick", "emotional_direction": True,
                                             "camera_state": None})
            self.assertEqual(job["compiled_prompt"].strip(), prompt.strip())
            for block in ("REFERENCE ROLE CONTRACT", "CHARACTER REFERENCE BINDINGS", "VOICE PERFORMANCE", "VISUAL DIRECTION"):
                self.assertNotIn(block, job["compiled_prompt"])
            import json
            settings = json.loads(job["settings_json"])
            self.assertEqual(settings["prompt_format"], "h3_ir")
            self.assertFalse(settings["emotional_direction"])
            self.assertIsNone(settings["camera_state"])

    def test_director_presets_are_kept_for_ir_jobs(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            store, ids, roles, owners = self._store_with_pictures(temp)
            config = {"lighting": {"id": "lighting.night", "parameters": {"time_of_day": "night"}}}
            job = store.create_job(prompt=official_prompt(), asset_ids=ids, reference_roles=roles,
                                   reference_owners=owners, settings={"quality": "quick", "director_config": config})
            settings = json.loads(job["settings_json"])
            self.assertEqual(settings["director_config"], config)  # 05.11: no longer dropped for IR
            self.assertIsNone(settings["camera_state"])

    def test_labels_that_do_not_match_the_job_are_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids, roles, owners = self._store_with_pictures(temp)
            # A label used in the text but defined nowhere (a consistently renumbered Subject is fine).
            bad = official_prompt().replace("detailed_description:\n", "detailed_description:\n<Subject 7> waves. ", 1)
            with self.assertRaisesRegex(ValueError, "не сходится с заданием"):
                store.create_job(prompt=bad, asset_ids=ids, reference_roles=roles, reference_owners=owners,
                                 settings={"mode": "replace", "quality": "quick"})

    def test_swapped_subject_picture_sources_are_blocked_locally_and_on_gpu(self):
        from app.main import _validate_official_manifest_prompt
        with tempfile.TemporaryDirectory() as temp:
            store, ids, roles, owners = self._store_with_pictures(temp)
            bad = official_prompt().replace(
                "<Subject 1> is ADAM, whose full-body appearance and silhouette come from <Picture 1>",
                "<Subject 1> is ADAM, whose full-body appearance and silhouette come from <Picture 2>",
            )
            # Names match the Studio owners, so Studio rebinds the pictures itself (no manual renumbering).
            job = store.create_job(prompt=bad, asset_ids=ids, reference_roles=roles, reference_owners=owners,
                                   settings={"mode": "replace", "quality": "quick"})
            self.assertIn("<Subject 1> is ADAM, whose full-body appearance and silhouette come from <Picture 1>",
                          job["compiled_prompt"])
            # The GPU never rebinds: a wrong prompt sent straight to it is still refused.
            manifest = {"images": [{"role": roles[asset_id], "owner": owners.get(asset_id, "")}
                                   for asset_id in ids], "videos": [], "audios": []}
            with self.assertRaisesRegex(ValueError, "картинки героя"):
                _validate_official_manifest_prompt(bad, manifest, False)


class OfficialFormatGraphTests(unittest.TestCase):
    def test_lora_trigger_opens_the_description_not_the_prompt(self):
        from app.contract import GenerationRequest
        from app.graph import build_prompt
        prompt = official_prompt()
        graph = build_prompt(GenerationRequest(prompt, 5, "16:9", "quick", steps=24, seed=1),
                             ["adam.png", "martin.png", "room.png"], style_adapter="style3d")
        text = next(value for node in graph.values() for value in node.get("inputs", {}).values()
                    if isinstance(value, str) and "subject_definitions" in value)
        self.assertTrue(text.startswith("subject_definitions:"))
        self.assertIn("detailed_description:\n3dsrx, The target video", text)


if __name__ == "__main__":
    unittest.main()
