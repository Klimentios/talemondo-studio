import asyncio
import base64
import hashlib
import json
import os
import tempfile
import unittest
import uuid

import httpx

from app import main as remote_main
from app.animal_realism import animal_realism_direction, scene_has_animals

SCALE_BOILERPLATE = ("SUBJECT SCALE AND HEIGHT CONTINUITY: A domestic cat remains clearly cat-sized and visibly "
                     "smaller than its adult owner; never enlarge it to human size.")


class AnimalRealismTests(unittest.TestCase):
    def test_animals_in_the_scene_text_or_observed_details_are_detected(self):
        self.assertTrue(scene_has_animals("VEGA and ORION are two cats racing through the store."))
        self.assertTrue(scene_has_animals("Кот Орион бежит по коридору."))
        self.assertTrue(scene_has_animals("<Picture 1> runs.", {"<Picture 1>": ["fur: short, ruddy ticked"]}))

    def test_boilerplate_and_lookalike_words_do_not_count(self):
        self.assertFalse(scene_has_animals("Мартин спит, другой идёт по квартире.\n" + SCALE_BOILERPLATE))
        self.assertFalse(scene_has_animals("Человек, который держит лист бумаги, совсем молчит."))
        self.assertFalse(scene_has_animals("No cat, no species change. A man enters the room."))
        self.assertFalse(scene_has_animals("Without any dogs, the two people cross the street."))
        self.assertFalse(scene_has_animals("Без кота в кадре. Человек открывает дверь."))
        self.assertFalse(scene_has_animals("No cats or dogs. Two people talk."))
        self.assertFalse(scene_has_animals("Без кошек и собак. Два человека разговаривают."))
        self.assertTrue(scene_has_animals("No cat in the shop; a dog waits outside."))

    def test_contract_only_for_realistic_scenes(self):
        prompt = "Two cats run through a mall."
        self.assertIn("ANIMAL REALISM", animal_realism_direction(prompt, "realism", "none"))
        self.assertEqual(animal_realism_direction(prompt, "animation_3d", "none"), "")
        self.assertEqual(animal_realism_direction(prompt, "realism", "style3d"), "")
        self.assertEqual(animal_realism_direction("A man walks home.", "realism", "none"), "")
        once = prompt + "\n\n" + animal_realism_direction(prompt, "realism", "none")
        self.assertEqual(animal_realism_direction(once, "realism", "none"), "")

    def test_final_graph_carries_the_contract_only_for_animal_scenes(self):
        from tests.test_remote_api import FakeComfy
        with tempfile.TemporaryDirectory() as temp:
            old_registry = os.environ.get("H3_JOB_REGISTRY_PATH")
            old_comfy = remote_main.comfy
            os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry.sqlite3"
            fake = FakeComfy()
            remote_main.comfy = fake
            picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
            manifest = {"version": 1, "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}],
                        "videos": [], "audios": []}

            async def submit(client, prompt):
                data = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                        "prompt": prompt, "precompiled_prompt": "true", "control_type": "none",
                        "duration": "5", "quality": "quick"}
                response = await client.post("/api/jobs", data=data,
                                             files=[("images", ("hero.png", picture, "image/png"))])
                self.assertEqual(response.status_code, 202, response.text)
                return fake.graph["136"]["inputs"]["prompt"]

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    cat = await submit(client, "<Picture 1> is VEGA, a cat sprinting down the aisle.")
                    self.assertIn("ANIMAL REALISM", cat)
                    self.assertIn("one to one", cat)
                    human = await submit(client, "<Picture 1> walks home at night.\n\n" + SCALE_BOILERPLATE)
                    self.assertNotIn("ANIMAL REALISM", human)

            try:
                asyncio.run(exercise())
            finally:
                remote_main.comfy = old_comfy
                if old_registry is None:
                    os.environ.pop("H3_JOB_REGISTRY_PATH", None)
                else:
                    os.environ["H3_JOB_REGISTRY_PATH"] = old_registry


if __name__ == "__main__":
    unittest.main()
