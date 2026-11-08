"""Broad build_prompt matrix used to prove a graph refactor changes nothing (tests/test_graph_fingerprint.py).

Every case is a fixed-seed call; the expected graphs live in tests/golden/graph_fingerprints.json.
Regenerate only for a deliberate, user-approved graph change:
    .venv/bin/python -m tests.graph_fingerprint_cases --write
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from app.contract import GenerationRequest
from app.graph import build_prompt

GOLDEN = Path(__file__).resolve().parent / "golden" / "graph_fingerprints.json"
ENV_KEYS = ("H3_CACHE_MODE", "H3_CAMERA_GUIDE_VIDEO", "H3_STATIC_CAMERA_GUIDE", "H3_FRAMING_LOCK",
            "H3_CAMERA_GUIDE_SHORT_EDGE")

IR = ("subject_definitions:\n<Subject 1> is the hero in <Picture 1>.\n\nsummary:\n[reference generation] x\n\n"
      "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - x\n\n"
      "detailed_description:\nThe target video is live action.\n[Shot 1] The camera pushes in.\n\n"
      "overall_soundscape:\nRoom tone.\n\nnon_diegetic_music:\nN/A")
MOVING = {"version": 2, "keys": [
    {"t": 0, "orbit": 0, "elevation": 0, "distance": 5.5, "target": [0, 1, 0], "fov": 40,
     "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 5.5},
    {"t": 4.5, "orbit": 0, "elevation": 0, "distance": 3.2, "target": [0, 1, 0], "fov": 40,
     "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 3.2}]}
STATIC = {"version": 1, "keys": [
    {"t": 0, "orbit": 10, "elevation": 5, "distance": 4, "target": [0, 1, 0], "fov": 45},
    {"t": 4, "orbit": 10, "elevation": 5, "distance": 4, "target": [0, 1, 0], "fov": 45}]}
MATCH = {"picture": 2, "strength": 0.6, "method": "mkl", "target_mean": [55.5, 3.2, -4.1], "target_std": [21.0, 6.5, 9.9]}
GRADE = {"target_mean": [55.0, 12.0, 30.0], "target_std": [18.0, 6.0, 9.0], "strength": 0.6}
LOOK = {"contrast": 0.2, "grain": 0.4, "grain_size": 2.0, "seed": 5}
CLIP = {"file": "continuation.mp4", "frames": 72, "audio": True}

P1 = "<Picture 1> walks to the window and looks outside."
P2 = "<Picture 1> walks into <Picture 2>."
PV = "<Picture 1> performs the motion of <Video 1>."
PA = "<Picture 1> speaks with the voice of <Audio 1>."


def req(prompt=P1, quality="quick", aspect="16:9", **kw):
    return GenerationRequest(prompt, kw.pop("duration", 5), aspect, quality, steps=kw.pop("steps", 25),
                             seed=kw.pop("seed", 7), **kw)


def cases():
    one, two = ["hero.png"], ["hero.png", "frame.png"]
    c = {
        "picture_only": (req(), one, {}),
        "picture_only_custom_09": (req(quality="custom", megapixels=0.9), one, {}),
        "picture_only_native_portrait": (req(quality="native", aspect="9:16"), one, {}),
        "picture_x3": (req("<Picture 1>, <Picture 2> and <Picture 3> talk."), ["a.png", "b.png", "c.png"], {}),
        "picture_x9": (req(" ".join(f"<Picture {i}>" for i in range(1, 10)) + " stand."), [f"{i}.png" for i in range(9)], {}),
        "ref_size_match": (req(ref_image_size="match"), one, {}),
        "steps_25_long": (req(steps=25, duration=10), one, {}),
        "video": (req(PV), one, {"reference_videos": ["v.mp4"]}),
        "video_no_audio": (req(PV), one, {"reference_videos": ["v.mp4"], "use_video_audio": False}),
        "video_x3": (req("<Picture 1> mixes <Video 1>, <Video 2>, <Video 3>."), one,
                     {"reference_videos": ["a.mp4", "b.mp4", "c.mp4"], "use_video_audio": False}),
        "video_only": (req("A person performs <Video 1>."), [], {"reference_videos": ["v.mp4"], "use_video_audio": False}),
        "audio": (req(PA), one, {"reference_audios": ["a.wav"]}),
        "audio_x3": (req("<Picture 1> <Audio 1> <Audio 2> <Audio 3>."), one, {"reference_audios": ["a.wav", "b.wav", "c.wav"]}),
        "cache_off": (req(), one, {"cache_mode": "off"}),
        "cache_safe": (req(), one, {"cache_mode": "safe"}),
        "cache_fast": (req(), one, {"cache_mode": "fast"}),
        "sampling_motion_ab": (req(), one, {"sampling_profile": "motion_ab"}),
        "camera_moving": (req(), one, {"camera_state": MOVING}),
        "camera_moving_words": (req(), one, {"camera_state": MOVING, "camera_words": True}),
        "camera_moving_optics": (req(), one, {"camera_state": MOVING, "camera_optics": {"focus_mode": "subject_head"}}),
        "camera_static": (req(), one, {"camera_state": STATIC}),
        "camera_ir": (req(IR), one, {"camera_state": MOVING}),
        "guide_first": (req(P2), two, {"guide_frames": [(2, 0)]}),
        "guide_last": (req(P2), two, {"guide_frames": [(2, -1)]}),
        "guide_mid_and_last": (req(P2), two, {"guide_frames": [(2, 60), (1, -1)]}),
        "guide_ir": (req(IR.replace("<Picture 1>.", "<Picture 1>; <Picture 2>.")), two, {"guide_frames": [(2, -1)]}),
        "continuation": (req(), one, {"continuation": CLIP}),
        "continuation_no_audio_guide": (req(P2), two, {"continuation": {**CLIP, "audio": False}, "guide_frames": [(2, -1)]}),
        "continuation_ir": (req(IR), one, {"continuation": CLIP}),
        "color_match": (req(P2), two, {"color_match": MATCH}),
        "color_match_v2": (req(P2), two, {"color_match": MATCH, "color_match_impl": "colormatch_v2"}),
        "color_grade": (req(), one, {"color_grade": GRADE}),
        "film_look": (req(), one, {"film_look": LOOK}),
        "post_chain_all": (req(P2), two, {"color_match": MATCH, "color_grade": GRADE, "film_look": LOOK}),
        "camera_effect": (req(), one, {"camera_effect": "kiss_camera"}),
        "ir_plain": (req(IR), one, {}),
        "hires_bislerp": (req(quality="native"), one, {"hires": {"method": "bislerp", "tail": 0.25}}),
        "hires_learned_guides": (req(P2, quality="native"), two, {"guide_frames": [(2, -1)], "continuation": CLIP,
                                                                   "hires": {"method": "learned", "tail": 0.3}}),
        "everything": (req(P2), two, {"reference_audios": ["a.wav"], "camera_state": MOVING, "guide_frames": [(2, -1)],
                                      "continuation": CLIP, "color_match": MATCH, "color_grade": GRADE,
                                      "film_look": LOOK, "camera_effect": "kiss_camera", "cache_mode": "fast"}),
    }
    for adapter in ("style3d", "cartoon2d", "realism_people", "character_swap", "combat"):
        c[f"adapter_{adapter}"] = (req(), one, {"style_adapter": adapter})
        c[f"adapter_{adapter}_ir"] = (req(IR), one, {"style_adapter": adapter})
        c[f"adapter_{adapter}_camera_cont"] = (req(), one, {"style_adapter": adapter, "camera_state": MOVING,
                                                             "continuation": CLIP})
    envs = {
        "env_guide_video_moving": ({"H3_CAMERA_GUIDE_VIDEO": "1"}, (req(), one, {"camera_state": MOVING})),
        "env_guide_video_optics": ({"H3_CAMERA_GUIDE_VIDEO": "1"},
                                   (req(PV), one, {"camera_state": MOVING, "reference_videos": ["v.mp4"],
                                                   "camera_optics": {"focus_mode": "subject_head"}})),
        "env_guide_video_static_off": ({"H3_CAMERA_GUIDE_VIDEO": "1"}, (req(), one, {"camera_state": STATIC})),
        "env_guide_video_static_on": ({"H3_CAMERA_GUIDE_VIDEO": "1", "H3_STATIC_CAMERA_GUIDE": "1"},
                                      (req(), one, {"camera_state": STATIC})),
        "env_framing_lock": ({"H3_FRAMING_LOCK": "1"}, (req(), one, {"camera_state": STATIC})),
        "env_guide_512": ({"H3_CAMERA_GUIDE_VIDEO": "1", "H3_CAMERA_GUIDE_SHORT_EDGE": "512"},
                          (req(aspect="9:16"), one, {"camera_state": MOVING})),
        "env_cache_default_fast": ({"H3_CACHE_MODE": "fast"}, (req(), one, {})),
    }
    return c, envs


def render() -> dict[str, dict]:
    plain, envs = cases()
    out = {}
    clean = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    for name, (request, refs, kwargs) in plain.items():
        with patch.dict(os.environ, clean, clear=True):
            out[name] = build_prompt(request, refs, **kwargs)
    for name, (env, (request, refs, kwargs)) in envs.items():
        with patch.dict(os.environ, {**clean, **env}, clear=True):
            out[name] = build_prompt(request, refs, **kwargs)
    return out


def canonical(graph: dict) -> str:
    return json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    graphs = {name: json.loads(canonical(g)) for name, g in render().items()}
    if "--write" in sys.argv:
        GOLDEN.write_text(json.dumps(graphs, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        print(f"{len(graphs)} графов записано в {GOLDEN}")
    else:
        for name, g in graphs.items():
            print(name, hashlib.sha256(canonical(g).encode()).hexdigest()[:12])
