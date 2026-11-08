"""Conservative, auditable LoRA routing for future Studio jobs.

Only explicit visual or combat intent selects an adapter. The GPU capability
list remains authoritative; routing never guesses that a model is installed.
"""

import re


_FIGHT = re.compile(r"\b(?:fight|fighting|combat|martial arts|wushu|duel|sparring|"
                    r"бой|боя|боев|драк[а-я]*|поедин[а-я]*|единоборств[а-я]*|"
                    r"кунг[ -]?фу|ушу)\b", re.IGNORECASE)
_HUMAN_DETAIL = re.compile(r"(?:real (?:human|person|people|face|skin)|photoreal(?:istic)? (?:human|person|people|face|skin)|"
                           r"detailed (?:human|person|people|face|skin)|"
                           r"реалистичн[а-я]* (?:лиц[ао]|кож[а-я]*|человек[а-я]*)|"
                           r"детализаци[а-я]* (?:лиц[ао]|кож[а-я]*|человек[а-я]*))",
                           re.IGNORECASE)
def select_adapter(prompt: str, mode: str, available: set[str], people: bool = False) -> tuple[str, str]:
    """Return (adapter, reason); replacement takes priority over cosmetic/action adapters.

    people: the job has a hero face / whole-person picture. User decision 2026-10-08: live people get the
    photoreal people LoRA by default (plastic CG faces without it); fights keep combat, animals get none.
    """
    if mode == "animation3d":
        return ("style3d", "3D style") if "style3d" in available else ("none", "3D LoRA unavailable")
    if mode == "animation2d":
        return ("cartoon2d", "2D style") if "cartoon2d" in available else ("none", "2D LoRA unavailable")
    if mode == "replace":
        return ("character_swap", "explicit hero replacement") if "character_swap" in available else \
            ("none", "character-swap LoRA unavailable")
    if mode != "realistic":
        return "none", "no supported LoRA intent"
    if _FIGHT.search(prompt):
        return (("combat", "fight choreography") if "combat" in available else
                ("none", "combat LoRA unavailable"))
    if _HUMAN_DETAIL.search(prompt) and "realism_people" in available:
        return "realism_people", "explicit realistic human detail"
    if people and "realism_people" in available:
        from .animal_realism import scene_has_animals
        if not scene_has_animals(prompt):
            return "realism_people", "photoreal people (hero pictures)"
    return "none", "matching LoRA unavailable or intent unspecified"
