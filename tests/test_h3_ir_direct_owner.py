"""An official prompt that names the owner right at each picture tag ("<Picture 1> VEGA face identity") gets its
<Subject N> lines from the job's owners, so the scene's job is not refused (VEGA & ORION S01E02, 2026-10-30)."""
from app.h3_ir import Reference, build_skeleton, complete_subject_sources, is_blocking, parse_ir, _subject_binding_problems

PROMPT = """subject_definitions:
<Picture 1> VEGA face identity, fully_preserved; <Picture 2> VEGA full pink bunny suit, fully_preserved. <Picture 3> ORION face identity, fully_preserved; <Picture 4> ORION full dragon suit, fully_preserved. <Picture 5> industrial warehouse location geometry, partially_preserved.
summary:
A crime caper.
retention_analysis:
Keep both cats.
detailed_description:
[Shot 1] Vega pounds the counter.
overall_soundscape:
Paw taps.
non_diegetic_music:
Low bass.
"""


def test_owner_named_at_the_tag_becomes_a_subject_line():
    refs = [Reference("image", 1, "Лицо героя", "VEGA"), Reference("image", 2, "Персонаж целиком", "VEGA"),
            Reference("image", 3, "Лицо героя", "ORION"), Reference("image", 4, "Персонаж целиком", "ORION"),
            Reference("image", 5, "Окружение")]
    skeleton = build_skeleton(refs)
    fixed = complete_subject_sources(PROMPT, skeleton)
    sections = parse_ir(fixed)
    problems = [p for p in _subject_binding_problems(sections["subject_definitions"], sections["retention_analysis"], skeleton)
                if is_blocking(p)]
    assert problems == []
    assert "<Picture 1> VEGA face identity" in fixed  # the author's line stays


def test_a_picture_named_for_another_hero_is_not_bound():
    refs = [Reference("image", 1, "Лицо героя", "VEGA"), Reference("image", 2, "Персонаж целиком", "VEGA")]
    wrong = PROMPT.replace("<Picture 2> VEGA full", "<Picture 2> ORION full")
    sections = parse_ir(complete_subject_sources(wrong, build_skeleton(refs)))
    assert "<Subject 1> is VEGA" not in sections["subject_definitions"]
