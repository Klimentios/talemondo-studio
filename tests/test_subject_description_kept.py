"""2026-10-08: Studio's picture binding replaced the whole <Subject> line, so each hero's own voice, age,
place in the shot and wardrobe never reached H3 (BORIS / MIKHAIL / VIKTOR bathhouse song)."""
from app.h3_ir import Reference, build_skeleton, rebind_subject_pictures

PROMPT = """subject_definitions:
<Subject 1> is BORIS, a man approximately 60 years old, seated on the left. His complete facial identity, hairstyle and body proportions come from <Picture 2>. He wears a traditional bath towel around his waist. His singing voice is a bright, slightly nasal tenor.
<Subject 2> is MIKHAIL, a man approximately 62 years old, whose recognizable facial identity comes from <Picture 1>. His voice is a deep, powerful bass.

summary:
[reference generation] Two friends sing in a bathhouse.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - BORIS's face.
<Subject 2> (appears in [Shot 1]): fully_preserved - MIKHAIL's face.

detailed_description:
[Shot 1] They sing.

overall_soundscape:
Two male voices.

non_diegetic_music:
N/A
"""


def test_binding_keeps_each_heros_own_description_and_voice():
    skeleton = build_skeleton([Reference("image", 1, "Лицо героя", "BORIS"), Reference("image", 2, "Лицо героя", "MIKHAIL")])
    out = rebind_subject_pictures(PROMPT, skeleton)
    boris = next(line for line in out.splitlines() if line.startswith("<Subject 1> is BORIS"))
    mikhail = next(line for line in out.splitlines() if line.startswith("<Subject 2> is MIKHAIL"))
    assert "<Picture 1>" in boris and "<Picture 2>" not in boris  # Studio's binding wins
    for words in ("approximately 60 years old, seated on the left", "bath towel around his waist",
                  "bright, slightly nasal tenor"):
        assert words in boris
    assert "approximately 62 years old" in mikhail and "deep, powerful bass" in mikhail
    assert "<Picture 1>" not in mikhail.split(":", 1)[-1] or mikhail.count("<Picture") == 1


def test_correct_picture_keeps_description_after_its_tag():
    skeleton = build_skeleton([Reference("image", 1, "Персонаж целиком", "ADAM"),
                               Reference("image", 2, "Персонаж целиком", "VEGA")])
    prompt = PROMPT.replace(
        PROMPT.split("\n\nsummary:")[0],
        "subject_definitions:\n<Subject 1> is ADAM, the adult man in <Picture 1>, with the same face and hair.\n"
        "<Subject 2> is VEGA, the cat in <Picture 2>, with his exact face, markings and proportions.")
    out = rebind_subject_pictures(prompt, skeleton)
    assert "same face and hair" in out
    assert "exact face, markings and proportions" in out
