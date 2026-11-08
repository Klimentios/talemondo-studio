"""Pins the reference-role wording used by the picture-only golden render.

That job (replace mode, pictures only: «Персонаж целиком», «Лицо героя», «Окружение»)
is the quality reference. Changing these strings changes its compiled prompt, so
role fixes for other cases must add new roles or be scoped to video jobs instead.
"""
import pathlib
import re
import unittest

from app.director import ROLE_INSTRUCTION
from local_studio.store import REFERENCE_ROLE_PROMPTS

ROOT = pathlib.Path(__file__).resolve().parents[1]

APPROVED = {
    "Лицо героя": "defines this character's face and identity only",
    "Персонаж целиком": "defines this character's full-body design and silhouette",
    "Окружение": "defines the location and set dressing only",
}
APPROVED_IDENTITY = (
    "locks the same visible face, head shape, hair/fur, age, skin/fur colors, body proportions, "
    "silhouette, hands, costume and carried accessories; preserve recognizable design details"
)


class ApprovedRecipeRoleTextTests(unittest.TestCase):
    def test_store_role_texts(self):
        for role, text in APPROVED.items():
            self.assertEqual(REFERENCE_ROLE_PROMPTS[role][1], text, role)

    def test_studio_role_texts(self):
        source = (ROOT / "preview/h3-director/model.mjs").read_text()
        for role, text in APPROVED.items():
            match = re.search(r"'%s':\s*([\"'])(.*?)\1," % re.escape(role), source)
            self.assertIsNotNone(match, role)
            self.assertEqual(match.group(2), text, role)

    def test_director_identity_text(self):
        self.assertEqual(ROLE_INSTRUCTION["identity"][1], APPROVED_IDENTITY)


if __name__ == "__main__":
    unittest.main()
