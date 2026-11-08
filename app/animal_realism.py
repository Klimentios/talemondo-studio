"""Live-action realism for animal characters, taken from their picture references.

Human identity already survives Ref2VA at pore level when the face picture is large.
Animals drifted toward a generic CGI look (9896b064: big glossy eyes, smooth fur, both cats the same
breed), so realistic scenes with animals get an explicit, positively phrased contract: H3 runs at
CFG 1 and does not honor negative lists. Scenes without animals are unchanged.
"""

from __future__ import annotations

import re

_ANIMAL_WORDS = re.compile(
    r"\b(?:cats?|kittens?|kitty|feline|dogs?|pupp(?:y|ies)|canine|horses?|fox(?:es)?|rabbits?|hamsters?|"
    r"parrots?|owls?|raccoons?|ferrets?|squirrels?|bears?|tigers?|lions?|wolf|wolves|"
    r"muzzles?|whiskers?|paws?|fur"
    r"|кот(?!ор)\w*|кош(?:к|е|ач)\w*|котён\w*|котен\w*|котик\w*|собак\w*|пёс|пес|псы|щен\w*|лошад\w*|"
    r"лис(?:а|ы|у|ой|ица|ёнок)\b|кролик\w*|хомяк\w*|попуга\w*|сов(?:а|ы|у|ой)\b|енот\w*|"
    r"хор[её]к\w*|белк\w*|медвед\w*|тигр\w*|лев|льв\w*|волк\w*|морд\w*|усы|лап(?:а|ы|ой|ами|ах)\b|шерст\w*)\b",
    re.IGNORECASE,
)

ANIMAL_REALISM_DIRECTION = (
    "ANIMAL REALISM: Every animal is a real, living animal filmed in live action with a cinema camera, "
    "as in a wildlife or pet documentary, with the same photographic realism as the human characters. "
    "Each animal's face matches its picture reference one to one: the exact skull and muzzle shape, "
    "eye shape, eye size relative to the head, iris color and pattern, pupil shape, nose leather color "
    "and texture, ear shape, size and set, whisker pads and whiskers, and every fur marking, stripe, "
    "ticking and color patch in its exact position. Render real fur: individual hairs with natural "
    "growth direction, length and density, soft undercoat, natural sheen and fine flyaway hairs; moist "
    "nose leather; wet, reflective eyes with true catchlights and natural depth. Anatomy, weight, gait "
    "and behavior are those of the real species. Each animal keeps its own breed and markings from its "
    "own picture; different animals stay visibly different."
)


# Studio/GPU boilerplate that mentions animals in every job (scale continuity: "a domestic cat remains
# cat-sized", emotional contract: "whiskers, muzzle", role contract: "hair/fur"). Only the user's own
# scene text and the observed reference details decide whether animals are in the shot.
_GENERATED_LABELS = (
    "SUBJECT SCALE AND HEIGHT CONTINUITY:", "VOICE PERFORMANCE DIRECTION:", "EMOTIONAL PERFORMANCE DIRECTION:",
    "VIDEO PERFORMANCE DIRECTION:", "AUDIO DIRECTION:", "REFERENCE ROLE CONTRACT:", "CHARACTER REFERENCE BINDINGS:",
    "VISUAL MODE:", "VISUAL DIRECTION:", "CINEMATOGRAPHY CONTROL:", "VOICE CAST FOR", "ANIMAL REALISM:",
)


def _scene_text(prompt: str) -> str:
    return "\n".join(line for line in (prompt or "").splitlines()
                     if not any(label in line for label in _GENERATED_LABELS))


_NEGATED_ANIMAL = re.compile(
    r"(?:\b(?:no|not|without|без|нет)\b|не должно быть)\s+"
    r"(?:(?:a|an|any|the|visible|living|real|more|новых|никаких)\s+){0,3}"
    r"(?:" + _ANIMAL_WORDS.pattern + r"\s+(?:or|and|и|или)\s+)*$",
    re.IGNORECASE,
)


def _has_positive_animal_mention(text: str) -> bool:
    for match in _ANIMAL_WORDS.finditer(text or ""):
        # Limit negation to this clause; a later positive mention still counts.
        prefix = re.split(r"[.,;:!?\n]", text[:match.start()])[-1]
        if not _NEGATED_ANIMAL.search(prefix):
            return True
    return False


def scene_has_animals(prompt: str, visual_evidence: dict | None = None) -> bool:
    texts = [_scene_text(prompt)]
    for details in (visual_evidence or {}).values():
        texts.extend(details)
    return any(_has_positive_animal_mention(text) for text in texts)


def animal_realism_direction(prompt: str, style: str, style_adapter: str,
                             visual_evidence: dict | None = None) -> str:
    """The contract for realistic scenes with animals, or "" for other scenes and stylized looks."""
    if style != "realism" or style_adapter != "none":
        return ""
    if "ANIMAL REALISM:" in prompt or not scene_has_animals(prompt, visual_evidence):
        return ""
    return ANIMAL_REALISM_DIRECTION
