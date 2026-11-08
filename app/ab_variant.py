"""A/B-only render variants for comparing a saved job by eye.

Studio never sends these fields. With every ``ab_*`` form field absent the GPU API builds the same
graph, fingerprint and Director summary as before (tests/golden/*). A variant is applied to the graph
that ``app.graph.build_prompt`` returned, so the default render path has no new branches:

* ``ab_scheduler``  — BasicScheduler ``scheduler`` (default: the graph's ``simple``);
* ``ab_lora`` + ``ab_lora_strength`` — one allow-listed LoRA through LoraLoaderModelOnly (node 312,
  after FirstBlockCache, like the style LoRA 311), with its trigger word in the prompt;
* ``ab_sparse_attention`` — the native ComfyUI BlockSparseAttention node (node 313, right after the
  UNET loader) with the defaults documented by the node at our ComfyUI pin b5cc8830;
* ``ab_resolution=768p`` — the official native 768p canvas (1344x768 / 768x1344 by the job's aspect);
* ``ab_cache=off`` — FirstBlockCache (node 310) removed, the model goes straight to the sampler;
* ``ab_camera_words=on`` — a legacy prompt gets the camera move in official words
  (app/camera_words.py) instead of the numeric compile_projected text (built by graph.py);
* ``ab_color_match=colormatch_v2|h3colorgrade`` — scene colour match with KJNodes ColorMatchV2
  (per frame, the pre-2026-10-05 path) or the default clip-wide H3ColorGrade;
* ``ab_vae=x2detail|x2detail_down`` — decode with speach1sdef178/MiniMax-H3-X2-Detail-VAE through
  MiniMaxH3VAEDecodeFast (TripleHeadedMonkey/ComfyUI-MiniMaxH3_LatentUpscaler): a 2x frame from the
  same latent, ``x2detail_down`` scaled back to the job size (supersampled decode). Same sampling,
  only the decoder differs — the test for "glassy" irises and smeared eyes.
* ``ab_turbo=lightx2v4|dmd8`` — few-step turbo LoRA (node 316, after the job's style LoRA) with its own step
  count, sampler and scheduler; FirstBlockCache is removed (nothing to reuse at 4–8 steps). A/B only:
  user decision 2026-10-22 lifted the "never turbo, not even in A/B" ban for speed tests.
* ``ab_hires=bislerp|learned`` (+ ``ab_hires_tail`` 0.1–0.5, default 0.25) — two-pass render: pass 1 at half
  size with every step, latent ×2 (MiniMaxH3LatentUpscaleCombined), pass 2 on the schedule tail
  (app/h3graph/hires.py). ``learned`` downloads its checkpoint on first use; ``bislerp`` needs no file.
SeedVR2 7B is an A/B option of the upscale pass instead: ``ab_seedvr2_model=7b`` on /api/upscale/jobs.

Driver: scripts/h3_ab.py (docs/H3_AB_HARNESS.md).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass

from .h3_ir import is_ir_prompt
from .h3graph import hires as h3hires
from .object_info import input_options

AB_SCHEDULERS = ("simple", "beta", "normal")
AB_SAMPLERS = ("res_multistep", "euler", "er_sde")
# MiniMaxH3SigmaShift video shift: lower moves steps toward fine detail, higher toward large motion.
AB_SHIFT_RANGE = (3.0, 20.0)
AB_SHIFT_AUDIO = 3.0
# «Продолжение» anchor (app/comfy_nodes/h3_continuation_anchor): baseline is the AddGuide keyframe of 22 frames.
AB_ANCHORS = {"keyframe5": ("keyframe", 5), "inpaint5": ("inpaint", 5), "inpaint22": ("inpaint", 22)}
ANCHOR_NODE = "356"

# name -> (file in models/loras, trigger word). Trigger words come from the LoRA model cards:
#   style3d        — our 3D style LoRA (install_h3_h200_overlay.sh), trigger "3dsrx";
#   realism_people — fal/MiniMax-H3-Realism-People-LoRA, file h3-realism-people-t2v-i2v-r2v.safetensors,
#                    trigger "r34l1sm" ("start the prompt with the trigger word"; 1.0 intended, 0.6–0.8 lighter).
AB_LORAS = {
    "style3d": ("3dsrx_minimax_h3_rank16-step00001250.safetensors", "3dsrx"),
    "realism_people": ("h3-realism-people-t2v-i2v-r2v.safetensors", "r34l1sm"),
    # prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp cp2000 (card recommends 1800/2000), trigger
    # "Facial Realism"; trained on 480x832 face close-ups, "does not guarantee identity preservation".
    "face_realism": ("minimax-h3-facial-realism-closeup-cp2000.safetensors", "Facial Realism"),
    # Jojocodex/minimax-h3-Camera-Motion-lora v1 3000 (Apache-2.0): trigger "camera motion" must open the
    # prompt; strength 0.8 gentle, 1.0 explicit, >1.2 unstable. Card labels it T2V — Ref2VA is the test.
    "camera_motion": ("camera_motion_h3_lora_v1_3000_pruned.safetensors", "camera motion"),
    # MATLOWAI/MiniMax-H3-ORB360-CardSpin step1500 (app/render_assets.json adapters.orb360): orbit inside one
    # continuous shot — a written «circles behind him» turns into a cut. Trigger ORB360_CW.
    "orb360": ("minimax_h3_orb360_step1500.safetensors", "ORB360_CW"),
    # Jojocodex/minimax-h3-wushu-action-lora (Studio «combat» uses 0.5 + spatial physics 0.3).
    "wushu_action": ("wushu_action_h3_lora_v5_3000_pruned.safetensors", "wushu_action"),
}
AB_LORA_STRENGTH = (0.0, 1.2)
# name -> file in models/loras and the card's sampling: Kijai/MiniMax-H3_comfy README ("4 steps, 0.75 LoRA
# strength, er_sde"); drbaph/MiniMax-H3-Turbo-Lora-ComfyUI card (8 steps, euler, beta, 1.0, full-base file).
AB_TURBO = {
    "lightx2v4": {"lora": "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
                  "strength": 0.75, "steps": 4, "sampler": "er_sde", "scheduler": "simple"},
    "dmd8": {"lora": "minimax_h3_dmd_8step_turbo.safetensors",
             "strength": 1.0, "steps": 8, "sampler": "euler", "scheduler": "beta"},
    # Middle ground (community practice, not from the cards): a softer LoRA so the base model keeps detail.
    "dmd8_soft": {"lora": "minimax_h3_dmd_8step_turbo.safetensors",
                  "strength": 0.8, "steps": 8, "sampler": "euler", "scheduler": "beta"},
    "lightx2v12_half": {"lora": "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors",
                        "strength": 0.5, "steps": 12, "sampler": "er_sde", "scheduler": "simple"},
}

# BlockSparseAttention at ComfyUI b5cc8830 (comfy_extras/nodes_sparse_attention.py), node defaults.
# sol-attn is the training-free method; sla/vsa need weights trained for that pattern, so only
# sol-attn is offered. sink_conditioning=exact_kv_and_rows keeps the packed text/reference rows
# and the generated audio rows dense (MiniMax-H3 specific).
SPARSE_ATTENTION_DEFAULTS = {
    "selection": "sol-attn", "selection.tau": 1.3,
    "start_percent": 0.2, "end_percent": 1.0, "dense_blocks": "",
    "min_tokens": 12288, "extra_tokens": 256,
    "sink_conditioning": "exact_kv_and_rows", "verbose": False,
}
AB_SPARSE_MODES = ("off", "sol-attn")
AB_RESOLUTIONS = {"768p": {"16:9": (1344, 768), "9:16": (768, 1344)}}
AB_CACHE_MODES = ("off",)
AB_COLOR_MATCH = ("h3colorgrade", "colormatch_v2")
AB_VAES = ("x2detail", "x2detail_down")
# 06.11 Vast (128 GB RAM, cgroup limit 129.6 GB): X2-Detail decode of 124 frames 672×1216 (12-channel buffer on CPU,
# pixel_shuffle copies) on top of ~55 GB of resident weights → kernel OOM kill of ComfyUI after 24/24 steps; the
# queued jobs died with it. Refuse it before the GPU on smaller hosts (docs/reference/H3_ENCODER_RESEARCH.md).
X2_MIN_HOST_RAM_GB = 192


def host_ram_gb() -> float:
    """Memory limit of this host/container in GB (cgroup v2 limit when set, else MemTotal); 0 when unknown."""
    try:
        limit = open("/sys/fs/cgroup/memory.max", encoding="ascii").read().strip()
        if limit.isdigit():
            return int(limit) / 2**30
    except OSError:
        pass
    try:
        for line in open("/proc/meminfo", encoding="ascii"):
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 2**20
    except OSError:
        pass
    return float("inf")  # unknown host (Mac stand): do not block
AB_HIRES = tuple(h3hires.METHODS)
X2_DETAIL_VAE = "MiniMax-H3-X2-Detail-v1.safetensors"
# Validated starting settings from the X2 Detail VAE card / example workflow.
X2_DECODE_SETTINGS = {"tiling": True, "tile_size": 256, "tile_overlap": 64, "output_device": "cpu",
                      "temporal_tiling": False, "temporal_tile_frames": 85, "temporal_context_frames": 39,
                      "seam_warning_threshold": 0.02}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")


@dataclass(frozen=True)
class ABVariant:
    name: str
    scheduler: str | None = None
    lora: str | None = None
    lora_strength: float | None = None
    sparse_attention: bool = False
    replayed_plan: bool = False
    resolution: str | None = None
    cache: str | None = None
    camera_words: bool = False
    color_match: str | None = None
    vae: str | None = None
    sampler: str | None = None
    legacy_motion: bool = False   # ab_legacy_motion=on: full-size movement video + the named Fast cache (741aec51)
    hires: str | None = None      # ab_hires=bislerp|learned: two-pass render (app/h3graph/hires.py)
    hires_tail: float | None = None
    turbo: str | None = None      # ab_turbo=lightx2v4|dmd8: few-step LoRA + its steps/sampler, no cache
    shift: float | None = None    # ab_shift=8: MiniMaxH3SigmaShift video shift (model default 12, audio stays 3)
    anchor: str | None = None     # ab_anchor=keyframe5|inpaint5|inpaint22: how «Продолжение» holds the previous clip

    def describe(self) -> dict:
        extra = {key: value for key, value in (("resolution", self.resolution), ("cache", self.cache),
                                               ("camera_words", self.camera_words or None),
                                               ("color_match", self.color_match), ("vae", self.vae),
                                               ("sampler", self.sampler),
                                               ("legacy_motion", self.legacy_motion or None),
                                               ("hires", self.hires), ("hires_tail", self.hires_tail),
                                               ("turbo", self.turbo), ("shift", self.shift),
                                               ("anchor", self.anchor)) if value}
        return {"name": self.name, "scheduler": self.scheduler or "simple",
                "lora": self.lora, "lora_strength": self.lora_strength,
                "sparse_attention": SPARSE_ATTENTION_DEFAULTS if self.sparse_attention else None,
                "replayed_plan": self.replayed_plan, **extra}

    def build_overrides(self) -> dict:
        """Extra app.graph.build_prompt arguments: variants that change how the graph is built."""
        overrides = {}
        if self.camera_words:
            overrides["camera_words"] = True
        if self.color_match:
            overrides["color_match_impl"] = self.color_match
        if self.hires:
            overrides["hires"] = {"method": self.hires, "tail": self.hires_tail or h3hires.DEFAULT_TAIL}
        return overrides

    @property
    def lora_file(self) -> str | None:
        return AB_LORAS[self.lora][0] if self.lora else None

    def required_nodes(self) -> set[str]:
        nodes = set()
        if self.lora or self.turbo:
            nodes.add("LoraLoaderModelOnly")
        if self.sparse_attention:
            nodes.add("BlockSparseAttention")
        if self.color_match == "colormatch_v2":
            nodes.add("ColorMatchV2")
        if self.vae:
            nodes.update({"VAELoader", "MiniMaxH3VAEDecodeFast"} | ({"ImageScale"} if self.vae == "x2detail_down" else set()))
        if self.hires:
            nodes.update(h3hires.REQUIRED_NODES)
        if self.shift:
            nodes.add("MiniMaxH3SigmaShift")
        if self.anchor and AB_ANCHORS[self.anchor][0] == "inpaint":
            nodes.add("H3ContinuationInpaint")
        return nodes

    def missing_files(self, node_info: dict) -> list[str]:
        """Model files this variant loads that ComfyUI does not list."""
        wanted = []
        if self.lora:
            wanted.append(("LoraLoaderModelOnly", "lora_name", self.lora_file))
        if self.vae:
            wanted.append(("VAELoader", "vae_name", X2_DETAIL_VAE))
        if self.turbo:
            wanted.append(("LoraLoaderModelOnly", "lora_name", AB_TURBO[self.turbo]["lora"]))
        missing = []
        for node_type, input_name, filename in wanted:
            if filename not in input_options(node_info, node_type, input_name):
                missing.append(filename)
        return missing


def parse_ab_variant(name: str, scheduler: str, lora: str, lora_strength: str,
                     sparse_attention: str, director_plan_json: str, *, resolution: str = "", cache: str = "",
                     camera_words: str = "", color_match: str = "", vae: str = "",
                     sampler: str = "", steps: str = "", legacy_motion: str = "", hires: str = "",
                     hires_tail: str = "", turbo: str = "", shift: str = "", anchor: str = "") -> tuple[ABVariant | None, dict | None]:
    """Strictly validated variant (None when no ``ab_*`` field was sent) and an optional replay plan."""
    fields = (name, scheduler, lora, lora_strength, sparse_attention, director_plan_json,
              resolution, cache, camera_words, color_match, vae, sampler, steps, legacy_motion, hires, hires_tail, turbo, shift, anchor)
    if not any(str(item).strip() for item in fields):
        return None, None
    name = name.strip() or "ab"
    if not _NAME_RE.match(name):
        raise ValueError("ab_variant: латиница, цифры, «_.-», до 48 символов")
    scheduler = scheduler.strip()
    if scheduler and scheduler not in AB_SCHEDULERS:
        raise ValueError(f"ab_scheduler: только {', '.join(AB_SCHEDULERS)}")
    lora = lora.strip()
    strength = None
    if lora:
        if lora not in AB_LORAS:
            raise ValueError(f"ab_lora: только {', '.join(AB_LORAS)}")
        try:
            strength = float(lora_strength) if lora_strength.strip() else 1.0
        except ValueError as exc:
            raise ValueError("ab_lora_strength должен быть числом") from exc
        if not AB_LORA_STRENGTH[0] <= strength <= AB_LORA_STRENGTH[1]:
            raise ValueError("ab_lora_strength: 0.0–1.2")
    elif lora_strength.strip():
        raise ValueError("ab_lora_strength без ab_lora")
    sparse = sparse_attention.strip() or "off"
    if sparse not in AB_SPARSE_MODES:
        raise ValueError(f"ab_sparse_attention: только {', '.join(AB_SPARSE_MODES)}")
    resolution, cache, color_match = resolution.strip(), cache.strip(), color_match.strip()
    if resolution and resolution not in AB_RESOLUTIONS:
        raise ValueError(f"ab_resolution: только {', '.join(AB_RESOLUTIONS)}")
    if cache and cache not in AB_CACHE_MODES:
        raise ValueError("ab_cache: только off")
    words = camera_words.strip() or "off"
    if words not in {"on", "off"}:
        raise ValueError("ab_camera_words: on или off")
    if color_match and color_match not in AB_COLOR_MATCH:
        raise ValueError(f"ab_color_match: только {', '.join(AB_COLOR_MATCH)}")
    sampler = sampler.strip()
    if sampler and sampler not in AB_SAMPLERS:
        raise ValueError(f"ab_sampler: только {', '.join(AB_SAMPLERS)}")
    if steps.strip():
        # Few-step / turbo renders are banned for good (user, 2026-10-06/08); steps come from the job (>= 24).
        raise ValueError("ab_steps не поддерживается: шаги задаются заданием, минимум 20, без turbo")
    legacy = legacy_motion.strip() or "off"
    if legacy not in {"on", "off"}:
        raise ValueError("ab_legacy_motion: on или off")
    vae = vae.strip()
    if vae and vae not in AB_VAES:
        raise ValueError(f"ab_vae: только {', '.join(AB_VAES)}")
    if vae and host_ram_gb() < X2_MIN_HOST_RAM_GB:
        raise ValueError(f"ab_vae={vae}: на сервере {host_ram_gb():.0f} ГБ ОЗУ, X2-Detail декодирует ролик на ЦПУ "
                         f"и на 124 кадрах 672×1216 убил ComfyUI по OOM при 128 ГБ (06.11); нужно ≥ {X2_MIN_HOST_RAM_GB} ГБ")
    hires = hires.strip()
    if hires and hires not in AB_HIRES:
        raise ValueError(f"ab_hires: только {', '.join(AB_HIRES)}")
    tail = None
    if hires_tail.strip():
        if not hires:
            raise ValueError("ab_hires_tail без ab_hires")
        try:
            tail = float(hires_tail)
        except ValueError as exc:
            raise ValueError("ab_hires_tail должен быть числом") from exc
        if not h3hires.TAIL_RANGE[0] <= tail <= h3hires.TAIL_RANGE[1]:
            raise ValueError(f"ab_hires_tail: {h3hires.TAIL_RANGE[0]}–{h3hires.TAIL_RANGE[1]}")
    if hires and resolution:
        raise ValueError("ab_hires задаёт размеры проходов сам; ab_resolution вместе с ним не используется")
    shift_value = None
    if shift.strip():
        try:
            shift_value = float(shift)
        except ValueError as exc:
            raise ValueError("ab_shift должен быть числом") from exc
        if not AB_SHIFT_RANGE[0] <= shift_value <= AB_SHIFT_RANGE[1]:
            raise ValueError(f"ab_shift: {AB_SHIFT_RANGE[0]}–{AB_SHIFT_RANGE[1]} (по умолчанию модели 12)")
    anchor = anchor.strip()
    if anchor and anchor not in AB_ANCHORS:
        raise ValueError(f"ab_anchor: только {', '.join(AB_ANCHORS)}")
    if anchor and hires:
        raise ValueError("ab_anchor с ab_hires не совмещается: второй проход перерисует закреплённое начало")
    turbo = turbo.strip()
    if turbo and turbo not in AB_TURBO:
        raise ValueError(f"ab_turbo: только {', '.join(AB_TURBO)}")
    if turbo and (hires or sampler or scheduler or cache or sparse == "sol-attn"):
        raise ValueError("ab_turbo задаёт шаги, сэмплер и кэш сам; с hires/sampler/scheduler/cache/sparse не совмещается")
    plan = None
    if director_plan_json.strip():
        try:
            plan = json.loads(director_plan_json)
        except json.JSONDecodeError as exc:
            raise ValueError("ab_director_plan_json: некорректный JSON") from exc
        if not isinstance(plan, dict):
            raise ValueError("ab_director_plan_json должен быть объектом плана Qwen")
    return ABVariant(name, scheduler or None, lora or None, strength, sparse == "sol-attn",
                     plan is not None, resolution or None, cache or None, words == "on", color_match or None, vae or None, sampler or None, legacy == "on",
                     hires or None, tail, turbo or None, shift_value, anchor or None), plan


def _with_trigger(prompt: str, trigger: str) -> str:
    """Trigger placement: official six-section prompts must open with ``subject_definitions:``, so the
    trigger opens ``detailed_description`` (as graph.py does for style3d); otherwise it opens the prompt."""
    if is_ir_prompt(prompt):
        if f"detailed_description:\n{trigger}" in prompt:
            return prompt
        return prompt.replace("detailed_description:\n", f"detailed_description:\n{trigger}, ", 1)
    if prompt.lstrip().lower().startswith(trigger.lower()):
        return prompt
    return f"{trigger}, {prompt}"


def apply_ab_variant(graph: dict, variant: ABVariant | None) -> dict:
    """Graph with the variant applied; without a variant the very same object is returned."""
    if variant is None:
        return graph
    graph = copy.deepcopy(graph)
    if variant.resolution:
        ref = graph["136"]["inputs"]
        aspect = "16:9" if ref["width"] >= ref["height"] else "9:16"
        ref["width"], ref["height"] = AB_RESOLUTIONS[variant.resolution][aspect]
    if (variant.cache == "off" or variant.turbo) and "310" in graph:
        source = graph.pop("310")["inputs"]["model"]
        for node in graph.values():
            if node["inputs"].get("model") == ["310", 0]:
                node["inputs"]["model"] = source
    if variant.legacy_motion and "310" in graph:
        # The 2026-10-04 afternoon behaviour (741aec51, "ideal"): the named Fast preset even with a video.
        graph["310"]["inputs"].update({"mode": "H3 Fast — 0.10 / max 2", "temporal_guard": False})
    if variant.sampler:
        graph["123"]["inputs"]["sampler_name"] = variant.sampler
    if variant.scheduler:
        graph["124"]["inputs"]["scheduler"] = variant.scheduler
    if variant.sparse_attention:
        graph["313"] = {"inputs": {"model": ["127", 0], **SPARSE_ATTENTION_DEFAULTS},
                        "class_type": "BlockSparseAttention",
                        "_meta": {"title": "A/B Block Sparse Attention"}}
        for node_id, node in graph.items():
            if node_id != "313" and node["inputs"].get("model") == ["127", 0]:
                node["inputs"]["model"] = ["313", 0]
    if variant.vae:
        decoders = [node_id for node_id, node in graph.items()
                    if node["class_type"] == "VAEDecode" and node["inputs"].get("vae") == ["119", 0]]
        if not decoders:
            raise ValueError("ab_vae: в графе нет видео-декодера VAEDecode")
        graph["314"] = {"inputs": {"vae_name": X2_DETAIL_VAE}, "class_type": "VAELoader",
                        "_meta": {"title": "A/B X2 Detail VAE"}}
        width, height = graph["136"]["inputs"]["width"], graph["136"]["inputs"]["height"]
        for node_id in decoders:
            node = graph[node_id]
            node["class_type"] = "MiniMaxH3VAEDecodeFast"
            node["_meta"] = {"title": "A/B X2 Detail decode"}
            node["inputs"] = {"samples": node["inputs"]["samples"], "vae": ["314", 0], **X2_DECODE_SETTINGS}
            if variant.vae == "x2detail_down":
                scaled = f"315{node_id}"
                graph[scaled] = {"inputs": {"image": [node_id, 0], "upscale_method": "lanczos",
                                            "width": width, "height": height, "crop": "disabled"},
                                 "class_type": "ImageScale", "_meta": {"title": "A/B X2 back to job size"}}
                for other_id, other in graph.items():
                    if other_id == scaled:
                        continue
                    for key, value in other["inputs"].items():
                        if value == [node_id, 0]:
                            other["inputs"][key] = [scaled, 0]
    if variant.turbo:
        turbo = AB_TURBO[variant.turbo]
        source = graph["124"]["inputs"]["model"]
        consumers = [node for node in graph.values() if node["inputs"].get("model") == source]
        graph["316"] = {"inputs": {"model": source, "lora_name": turbo["lora"], "strength_model": turbo["strength"]},
                        "class_type": "LoraLoaderModelOnly", "_meta": {"title": f"A/B turbo {variant.turbo}"}}
        for node in consumers:
            node["inputs"]["model"] = ["316", 0]
        graph["124"]["inputs"].update({"steps": turbo["steps"], "scheduler": turbo["scheduler"]})
        graph["123"]["inputs"]["sampler_name"] = turbo["sampler"]
    if variant.lora:
        if "311" in graph:
            raise ValueError("ab_lora нельзя совмещать со стилевой LoRA задания")
        lora_file, trigger = AB_LORAS[variant.lora]
        source = graph["124"]["inputs"]["model"]
        consumers = [node for node in graph.values() if node["inputs"].get("model") == source]
        graph["312"] = {"inputs": {"model": source, "lora_name": lora_file,
                                   "strength_model": variant.lora_strength},
                        "class_type": "LoraLoaderModelOnly", "_meta": {"title": f"A/B LoRA {variant.lora}"}}
        for node in consumers:  # the scheduler, the guider and, with ab_hires, the second pass
            node["inputs"]["model"] = ["312", 0]
        ref = graph["136"]["inputs"]
        if trigger:
            ref["prompt"] = _with_trigger(ref["prompt"], trigger)
    if variant.anchor:
        _apply_anchor(graph, variant.anchor)
    if variant.shift:
        # Last in the model chain, so it also holds with an A/B LoRA and the hires second pass.
        source = graph["124"]["inputs"]["model"]
        consumers = [node for node in graph.values() if node["inputs"].get("model") == source]
        graph["317"] = {"inputs": {"model": source, "shift_video": variant.shift, "shift_audio": AB_SHIFT_AUDIO},
                        "class_type": "MiniMaxH3SigmaShift", "_meta": {"title": f"A/B shift {variant.shift:g}"}}
        for node in consumers:
            node["inputs"]["model"] = ["317", 0]
    return graph


def _apply_anchor(graph: dict, anchor: str) -> None:
    """Rewires «Продолжение» (app/continuation.py nodes 350–354): fewer tail frames, or the tail copied
    into the latent under the denoise mask (H3ContinuationInpaint) instead of the AddGuide keyframe."""
    from .continuation import CONDITIONING_NODE, FPS
    if CONDITIONING_NODE not in graph:
        raise ValueError("ab_anchor: в задании нет «Продолжения»")
    mode, frames = AB_ANCHORS[anchor]
    graph["352"]["inputs"].update(batch_index=-frames, length=frames)
    graph["352"]["_meta"]["title"] = f"Последние {frames} кадров"
    if "353" in graph:
        audio = graph["353"]["inputs"]
        end = audio["start_index"] + audio["duration"]  # the last frame of the previous clip (4-digit inputs)
        audio["start_index"], audio["duration"] = round(end - frames / FPS, 4), round(frames / FPS, 4)
    if mode == "keyframe":
        return
    guide = graph.pop(CONDITIONING_NODE)["inputs"]
    for node in graph.values():  # the guider and scene guides take the Ref2VA conditioning directly
        for name, value in node["inputs"].items():
            if value == [CONDITIONING_NODE, 0]:
                node["inputs"][name] = guide["positive"]
    graph[ANCHOR_NODE] = {"inputs": {"latent": guide["latent"], "vae": guide["vae"], "image": guide["image"],
                                     **({"audio": guide["audio"], "audio_vae": guide["audio_vae"]} if "audio" in guide else {})},
                          "class_type": "H3ContinuationInpaint", "_meta": {"title": f"A/B продолжение: inpaint {frames}"}}
    for node in graph.values():
        if node["inputs"].get("latent_image") == guide["latent"]:
            node["inputs"]["latent_image"] = [ANCHOR_NODE, 0]


_UPLOAD_INPUTS = {"LoadImage": "image", "LoadVideo": "file", "LoadAudio": "audio"}


def graph_fingerprint(graph: dict) -> dict:
    """Hashes that are equal for equal renders: upload names (random per submission) and the seed
    node title are normalised, everything else — prompt, seed, sizes, nodes — is hashed as queued."""
    normal = copy.deepcopy(graph)
    for node in normal.values():
        key = _UPLOAD_INPUTS.get(node.get("class_type"))
        if key and isinstance(node["inputs"].get(key), str):
            node["inputs"][key] = node["_meta"]["title"]
    prompt = str(graph.get("136", {}).get("inputs", {}).get("prompt", ""))
    return {
        "graph_sha256": hashlib.sha256(json.dumps(normal, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_chars": len(prompt),
    }
