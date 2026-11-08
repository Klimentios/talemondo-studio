"""Shared role contract for source performance with target character appearance."""
import json
import re
from pathlib import Path

POLICY = json.loads(Path(__file__).with_suffix('.json').read_text())
ROLE_INSTRUCTION = POLICY['role_instruction']
IDENTITY_RULE = POLICY['identity_rule']
CAMERA_RULE = POLICY['camera_rule']
SCENE_RULE = POLICY['scene_rule']

MOTION_ONLY_INSTRUCTION = POLICY['motion_only_instruction']


def apply_cast_and_clean_scene_contract(prompt: str, videos: list[dict]) -> str:
    """Bind multi-performer identity and optional relative size at the GPU prompt boundary.

    This is conditioning text, not a guarantee that the model will obey it. It applies
    only to movement references, leaving picture-only and full source-scene edits alone.
    """
    movement = [video for video in videos if video.get('role') in {'Движение', 'Движение и камера'}]
    if not movement:
        return prompt
    rules = []
    if any(video.get('role') == 'Движение и камера' for video in movement):
        rules.append(SCENE_RULE)
    for video in movement:
        pairs = video.get('performers') or []
        if len(pairs) > 1:
            assignments = '; '.join(
                f"{pair['owner']} follows only {pair.get('performer') or 'the assigned performer'}"
                for pair in pairs if pair.get('owner')
            )
            rules.append(f"CAST CONTINUITY: {assignments}. The video is a motion/camera reference, not an additional visible cast. "
                         "Keep each face, fur, costume and role fixed from first to last frame, including overlaps and crossings; "
                         "exactly one instance of each assigned hero is visible when that performer is visible. "
                         "Do not create background, reflected, transitional or partial duplicates of an assigned hero; "
                         "preserve only the mapped performers and other cast explicitly allowed by the brief.")
        if pairs:
            anchor = pairs[0].get('owner')
            for pair in pairs[1:]:
                scale = pair.get('relative_scale_percent')
                if anchor and type(scale) is int and 25 <= scale <= 200:
                    rules.append(f"SUBJECT SCALE: {pair['owner']} remains {scale}% of {anchor}'s "
                                 "standing height in the same depth plane, including costume hood, "
                                 "throughout the shot. Camera perspective may change apparent screen size.")
    if not rules:
        return prompt
    contract = ' '.join(rules)
    match = re.search(r'(?im)^detailed_description:[ \t]*', prompt)
    if match:
        return prompt[:match.end()] + '\n' + contract + '\n' + prompt[match.end():]
    return contract + '\n\n' + prompt


def normalize_motion_retention(prompt: str, videos) -> str:
    """Movement transfers attributes, never the RGB performer's appearance.

    Normalize only the reference retention contract. Leave the creative description
    and non-movement references unchanged; this is not a pixel-level identity lock.
    """
    if not any(v.get('job_role', v.get('role')) in {'Движение', 'Движение и камера', 'motion', 'motion_camera'} for v in videos):
        return prompt
    match = re.search(r'(?ims)^retention_analysis:[ \t]*\n(.*?)(?=^[a-z_]+:[ \t]*$|\Z)', prompt)
    if not match:
        return prompt
    body = match.group(1)
    for number, video in enumerate(videos, 1):
        role = video.get('job_role', video.get('role'))
        if role not in {'Движение', 'Движение и камера', 'motion', 'motion_camera'}:
            continue
        tag = f'<Video {number}>'
        # Do not change nested prose or another video's contract. Collapse only
        # the old top-level retention entries for this movement reference.
        body = re.sub(rf'(?im)^{re.escape(tag)}[^\n]*:[^\n]*(?:\n|$)', '', body)
        instruction = MOTION_ONLY_INSTRUCTION if role in {'Движение', 'motion'} else ROLE_INSTRUCTION
        body = body.rstrip() + f'\n{tag}: attribute_transfer — {instruction}.\n'
    return prompt[:match.start(1)] + body.rstrip() + '\n\n' + prompt[match.end(1):].lstrip('\n')
