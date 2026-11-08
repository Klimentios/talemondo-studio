import unittest
import json
import tempfile
from pathlib import Path

from app.lora_routing import select_adapter
from app.director import missing_model_files
from local_studio.store import StudioStore


class LoraRoutingTests(unittest.TestCase):
    def test_explicit_combat_routes_only_when_available(self):
        available = {"combat"}
        self.assertEqual(select_adapter("A martial arts fight in the rain", "realistic", available)[0],
                         "combat")
        self.assertEqual(select_adapter("Бой: перенос веса, работа ног и контакт", "realistic", available)[0],
                         "combat")
        self.assertEqual(select_adapter("Бой на мечах", "realistic", set())[0], "none")
        self.assertEqual(select_adapter("A martial arts fight", "replace", available)[0], "none")

    def test_no_keyword_and_other_modes_do_not_get_combat(self):
        available = {"combat", "style3d", "cartoon2d"}
        self.assertEqual(select_adapter("A character walks through the city", "realistic", available)[0],
                         "none")
        self.assertEqual(select_adapter("A fight", "animation3d", available)[0], "style3d")
        self.assertEqual(select_adapter("A fight", "replace", available)[0], "none")

    def test_human_detail_needs_explicit_intent(self):
        available = {"realism_people"}
        self.assertEqual(select_adapter("Detailed human skin in a close-up", "realistic", available)[0],
                         "realism_people")
        self.assertEqual(select_adapter("A person walks", "realistic", available)[0], "none")
        self.assertEqual(select_adapter("Replace the cats with two real human characters", "realistic", available)[0],
                         "realism_people")
        self.assertEqual(select_adapter("Replace the cats with two real human characters", "replace", available)[0],
                         "none")

    def test_explicit_replacement_uses_only_the_character_swap_adapter(self):
        self.assertEqual(select_adapter("Replace performer A", "replace", {"character_swap", "combat"}),
                         ("character_swap", "explicit hero replacement"))
        self.assertEqual(select_adapter("Replace performer A", "replace", set()),
                         ("none", "character-swap LoRA unavailable"))

    def test_combat_capability_requires_both_files(self):
        node = {"LoraLoaderModelOnly": {"input": {"required": {
            "lora_name": [["wushu_action_h3_lora_v5_3000_pruned.safetensors"]]}}}}
        self.assertEqual(missing_model_files("combat", node),
                         ["wushu_spatial_physics_clean_3000_pruned.safetensors"])

    def test_character_swap_capability_requires_loader_and_exact_weight(self):
        node = {"LoraLoaderModelOnly": {"input": {"required": {
            "lora_name": [["h3_character_swap_pro4500_1000.safetensors"]]}}}}
        self.assertEqual(missing_model_files("character_swap", node), [])
        self.assertEqual(missing_model_files("character_swap", {}), ["h3_character_swap_pro4500_1000.safetensors"])

    def test_mac_job_persists_resolved_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "hero.png"
            path.write_bytes(b"image")
            asset = store.add_asset(kind="image", role="hero", original_name="hero.png",
                                    mime="image/png", size=5, sha256="image", path=path)
            job = store.create_job(prompt="<Picture 1> fights in a martial arts duel",
                                   asset_ids=[asset["asset_id"]],
                                   settings={"mode": "realistic", "style_adapter": "auto"},
                                   style_adapters={"combat"})
            settings = json.loads(job["settings_json"])
            self.assertEqual(settings["style_adapter"], "combat")
            self.assertEqual(settings["lora_auto_reason"], "fight choreography")


if __name__ == "__main__":
    unittest.main()


class PeopleRealismTests(unittest.TestCase):
    """User decision 2026-10-08: people from pictures get the photoreal people LoRA unless it is a fight or animals."""

    def test_people_get_realism_fights_and_animals_do_not(self):
        available = {"combat", "realism_people"}
        self.assertEqual(select_adapter("Three friends sing in a bathhouse.", "realistic", available, people=True)[0],
                         "realism_people")
        self.assertEqual(select_adapter("Three friends sing in a bathhouse.", "realistic", available)[0], "none")
        self.assertEqual(select_adapter("A martial arts fight.", "realistic", available, people=True)[0], "combat")
        self.assertEqual(select_adapter("A cat sings into a microphone.", "realistic", available, people=True)[0], "none")
        self.assertEqual(select_adapter("No cat. Two people talk.", "realistic", available, people=True)[0],
                         "realism_people")
        self.assertEqual(select_adapter("No cats or dogs. Two people talk.", "realistic", available, people=True)[0],
                         "realism_people")
        self.assertEqual(select_adapter("Two men talk.", "replace", {"character_swap", "realism_people"}, people=True)[0],
                         "character_swap")
