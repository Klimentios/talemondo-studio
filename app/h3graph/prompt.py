"""The text H3 reads: the user's (or the director's) prompt plus what the graph options require.

An official six-section prompt (app/h3_ir.py) must open with "subject_definitions:" and carries its own
camera, keyframe and continuation wording; LoRA triggers then open detailed_description instead.
A legacy prompt gets the option sentences in front and the LoRA trigger at the very start.
"""
from __future__ import annotations

from .. import continuation as continuation_take
from ..camera_effects import with_camera_effect
from ..h3_ir import is_ir_prompt
from .guides import guide_frame_sentence

COMBAT_CHOREOGRAPHY_DIRECTION = (
    "COMBAT CHOREOGRAPHY: Make every described exchange visually readable as one continuous action: "
    "attack, defensive response or evasion, clear contact or a clear miss, visible physical reaction, "
    "then recovery into the next guard. Preserve each fighter's identity and costume, left-right screen "
    "position, distance, eyeline, planted-foot location and cause-and-effect between moves throughout the "
    "sequence. Keep both fighters and the attacking limb visible in a medium or wide action composition; "
    "move closer only for a described impact or facial reaction. Show grounded weight transfer and controlled "
    "recovery between techniques."
)
LORA_TRIGGERS = {"style3d": "3dsrx", "cartoon2d": "rough 2D cartoon illustration",
                 "realism_people": "r34l1sm", "combat": "wushu_action"}
_DESCRIPTION = "detailed_description:\n"


def _insert_combat_direction(prompt: str, trigger: str) -> str:
    trigger_start = f"{_DESCRIPTION}{trigger}, "
    if trigger_start in prompt:
        prompt = prompt.replace(trigger_start, trigger_start + COMBAT_CHOREOGRAPHY_DIRECTION + " ", 1)
    return prompt


def style_prompt(prompt: str, style_adapter: str) -> str:
    """The prompt with the LoRA trigger (and combat direction) placed inside an official IR."""
    trigger = LORA_TRIGGERS.get(style_adapter)
    if trigger and is_ir_prompt(prompt):
        if f"{_DESCRIPTION}{trigger}" not in prompt:
            prompt = prompt.replace(_DESCRIPTION, f"{_DESCRIPTION}{trigger}, ", 1)
    if style_adapter == "combat" and is_ir_prompt(prompt) and "COMBAT CHOREOGRAPHY:" not in prompt:
        prompt = _insert_combat_direction(prompt, trigger)
    return prompt


def option_sentences(prompt: str, camera_sentences: list[str], resolved_guides: list[tuple[int, int]],
                     length: int, continuation: dict | None) -> list[str]:
    """Sentences put in front of a legacy prompt; an official IR states keyframes and continuation itself."""
    additions = list(camera_sentences)
    if not is_ir_prompt(prompt):
        additions += [guide_frame_sentence(number, resolved, length) for number, resolved in resolved_guides]
        if continuation:
            additions.insert(0, continuation_take.LEGACY_SENTENCE)
    return additions


def final_prompt(prompt: str, additions: list[str], style_adapter: str, camera_effect: str | None) -> str:
    trigger = LORA_TRIGGERS.get(style_adapter)
    text = with_camera_effect(" ".join([*additions, prompt]).strip(), camera_effect)
    if style_adapter == "combat" and "COMBAT CHOREOGRAPHY:" not in text:
        if is_ir_prompt(text):
            text = _insert_combat_direction(text, trigger)
        else:
            text = f"{trigger}, {COMBAT_CHOREOGRAPHY_DIRECTION} {text}"
    # The author cards require every trigger at the very start of ordinary prompts. Camera words,
    # the continuation sentence or embeddings must not push it behind another prefix (style3d and
    # cartoon2d used to be prepended before them — audit 2026-10-05).
    if trigger and not is_ir_prompt(text):
        text = f"{trigger}, {text}" if not text.lower().startswith(f"{trigger.lower()},") else text
    return text
