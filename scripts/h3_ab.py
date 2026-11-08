#!/usr/bin/env python3
"""A/B harness: one saved Studio job, several render variants, side-by-side report.

    python3 scripts/h3_ab.py run [--gpu-url http://127.0.0.1:18189] [--job JOB_ID | --golden]
                                 [--variants baseline,beta,normal,realism-0.6,realism-1.0 | quality | swap] [--sparse]
                                 [--swap-mask '{"performers": [{"name": "HERO", "box": [x, y, w, h]}]}']
                                 [--tag TAG] [--merge PREVIOUS_RUN_DIR ...] [--dry-run]
    python3 scripts/h3_ab.py report RUN_DIR [--merge PREVIOUS_RUN_DIR ...]
    python3 scripts/h3_ab.py upscale RUN_DIR [--models 3b,7b] [--gpu-url ...]   (SeedVR2 3B vs 7B on the baseline)

Official-graph variants (app/ab_variant.py): 768p (native 1344x768 / 768x1344), cache-off (no
FirstBlockCache), camera-words (camera move in official words instead of the numeric text, legacy
prompts with a camera trajectory), colormatch-v2 (KJNodes ColorMatchV2 instead of the clip-wide
H3ColorGrade; the job needs color_match). SeedVR2 7B needs H3_SEEDVR2_7B=1 on the server.

Every variant gets the same seed, size, steps, prompt and references (the saved Studio job, read-only
from the Mac DB, or tests/golden/pictures_studio_job.json). The baseline goes first; when Qwen plans it,
the other variants replay that plan (ab_director_plan_json), so they differ only in the A/B option.
Output: logs/ab-<date>[-tag]/ with NN-<variant>.mp4, report.md, index.html (synced players),
results.json, prompts/. Details: docs/guides/H3_AB_HARNESS.md. Standard library only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from local_studio.worker import gpu_form_fields  # noqa: E402  (the exact Studio submission fields)
from scripts.h3_seam_jump import seam_jump  # noqa: E402  (seam of a «Продолжение» variant)

DEFAULT_GPU_URL = "http://127.0.0.1:18189"  # the Mac tunnel to the GPU API
STUDIO_ROOT = Path.home() / "Library/Application Support/H3 Studio"
GOLDEN_JOB = ROOT / "tests/golden/pictures_studio_job.json"
GOLDEN_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")

VARIANTS = {
    "baseline": {},
    "beta": {"ab_scheduler": "beta"},
    "normal": {"ab_scheduler": "normal"},
    "realism-0.6": {"ab_lora": "realism_people", "ab_lora_strength": "0.6"},
    "realism-1.0": {"ab_lora": "realism_people", "ab_lora_strength": "1.0"},
    # BlockSparseAttention and FirstBlockCache both replace DiT blocks and refuse to stack (A/B 2026-10-05).
    "sparse": {"ab_sparse_attention": "sol-attn", "ab_cache": "off"},
    "cache-off-ref": {"ab_cache": "off"},
    # 741aec51 (2026-10-04, "ideal" dance): full-size movement video + named Fast cache.
    "legacy-motion": {"ab_legacy_motion": "on"},  # same cache-off reference for judging sparse speed
    "768p": {"ab_resolution": "768p"},
    "cache-off": {"ab_cache": "off"},
    # Studio's own Safe preset (0.08 + temporal guard) against a job that ran with Fast.
    "cache-safe": {"cache_mode": "safe"},
    "camera-words": {"ab_camera_words": "on"},
    "colormatch-v2": {"ab_color_match": "colormatch_v2"},
    # (steps-32 variants removed 05.11: the contract accepts 20–25 steps, DECISIONS 16.10 and 04.11.)
    # Faces and eyes: decoder only (same latent) vs the face-realism LoRA.
    "x2vae": {"ab_vae": "x2detail"},
    "x2vae-down": {"ab_vae": "x2detail_down"},
    "face-0.5": {"ab_lora": "face_realism", "ab_lora_strength": "0.5"},
    "face-0.8": {"ab_lora": "face_realism", "ab_lora_strength": "0.8"},
    # LoRA tuning on real jobs (people, acting, camera, fights, speed).
    "realism-0.8": {"ab_lora": "realism_people", "ab_lora_strength": "0.8"},
    "camera-0.8": {"ab_lora": "camera_motion", "ab_lora_strength": "0.8"},
    "orb360": {"ab_lora": "orb360", "ab_lora_strength": "1.0"},
    "camera-1.0": {"ab_lora": "camera_motion", "ab_lora_strength": "1.0"},
    "wushu-0.5": {"ab_lora": "wushu_action", "ab_lora_strength": "0.5"},
    "wushu-0.8": {"ab_lora": "wushu_action", "ab_lora_strength": "0.8"},
    # Speed: reference tokens at the generation size instead of up to 2048 px (regular field), and the
    # two-pass hires (pass 1 at half size with every step, pass 2 on the 25 % tail at full size).
    "ref-match": {"ref_image_size": "match"},
    "hires": {"ab_hires": "bislerp"},
    # Quality kit 2026-11-02 (docs/guides/H3_AB_HARNESS.md «Набор качества»): community settings on our bf16 weights.
    "er-sde-beta": {"ab_sampler": "er_sde", "ab_scheduler": "beta"},
    "shift-8": {"ab_shift": "8"},
    "shift-6": {"ab_shift": "6"},
    "shift-16": {"ab_shift": "16"},
    # Config A/B plan 05.11 (docs/reference/H3_CONFIG_AB_PLAN.md): regular job fields, same seed and steps.
    "motion-ab": {"sampling_profile": "motion_ab"},
    "realism-0.7": {"ab_lora": "realism_people", "ab_lora_strength": "0.7"},
    "mp-0.6": {"quality": "custom", "megapixels": "0.6"},
    "mp-0.7": {"quality": "custom", "megapixels": "0.7"},
    "mp-0.8": {"quality": "custom", "megapixels": "0.8"},
    "mp-0.98": {"quality": "custom", "megapixels": "0.98"},
    "hires-er-sde-beta": {"ab_hires": "bislerp", "ab_sampler": "er_sde", "ab_scheduler": "beta"},
    # «Продолжение» seams 2026-11-02 (ComfyUI-H3-LongTake measured joins): baseline = AddGuide keyframe, 22 frames.
    "anchor-keyframe5": {"ab_anchor": "keyframe5"},
    "anchor-inpaint5": {"ab_anchor": "inpaint5"},
    "anchor-inpaint22": {"ab_anchor": "inpaint22"},
    # Speed matrix 2026-10-22 (user allowed every method, turbo to quantization): DiT quantization through
    # the regular render profile field, few-step turbo LoRAs (app/ab_variant.py AB_TURBO).
    "int8": {"profile": "int8_full"},
    "w4a8": {"profile": "w4a8"},
    "turbo4": {"ab_turbo": "lightx2v4"},
    "turbo8": {"ab_turbo": "dmd8"},
    "turbo8-soft": {"ab_turbo": "dmd8_soft"},
    "turbo12-half": {"ab_turbo": "lightx2v12_half"},
    # Turbo together with the reference size that already proved free (-13..22 %).
    "turbo4-match": {"ab_turbo": "lightx2v4", "ref_image_size": "match"},
    "turbo8-match": {"ab_turbo": "dmd8", "ref_image_size": "match"},
    # Hero swap kit 2026-11-04 (docs/guides/STUDIO_COMFYUI_ECOSYSTEM_PLAN.md): one swap job, 24 steps, same seed.
    # "@mask" is the --swap-mask JSON; "@source_short_edge" re-encodes the source video before upload.
    "swap-v15": {"style_adapter": "character_swap_v15"},
    "ref-beta": {"sampling_profile": "ref_beta"},
    "src-480": {"@source_short_edge": "480"},
    "swap-mask": {"swap_mask_json": "@mask"},
    "swap-mask-v15": {"style_adapter": "character_swap_v15", "swap_mask_json": "@mask"},
}
SEEDVR2_MODELS = ("3b", "7b")
DEFAULT_VARIANTS = "baseline,beta,normal,realism-0.6,realism-1.0"
# No turbo, no quantization, no fewer steps: only what can become a quality default.
QUALITY_KIT = "baseline,hires,er-sde-beta,shift-8,shift-6,hires-er-sde-beta"
# A job with a «Продолжение» video: every variant also gets the seam jump (scripts/h3_seam_jump.py).
CONTINUATION_KIT = "baseline,anchor-keyframe5,anchor-inpaint5,anchor-inpaint22"
# A source-video hero swap job: LoRA version, sampler, source size, mask.
SWAP_KIT = "baseline,swap-v15,ref-beta,src-480,swap-mask,swap-mask-v15"
KITS = {"quality": QUALITY_KIT, "continuation": CONTINUATION_KIT, "swap": SWAP_KIT}
ANCHOR_FRAMES = {"keyframe5": 5, "inpaint5": 5, "inpaint22": 22}
TERMINAL = {"success", "error", "unknown", "rejected"}


def log(message: str) -> None:
    print(f"[h3-ab {datetime.now():%H:%M:%S}] {message}", flush=True)


# ---------------------------------------------------------------- job spec

def load_studio_job(job_id: str, studio_root: Path) -> tuple[dict, list[tuple]]:
    """A saved Studio job and its reference files, read-only from the Mac DB."""
    db_path = studio_root / "studio.sqlite3"
    if not db_path.is_file():
        raise SystemExit(f"Нет базы Studio: {db_path}")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM jobs WHERE job_id LIKE ? ORDER BY created_at", (job_id + "%",)).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"Задание {job_id}: найдено {len(rows)} (нужно ровно одно)")
    job = dict(rows[0])
    manifest = json.loads(job["manifest_json"])
    files = []
    for group in ("images", "videos", "audios"):
        for entry in manifest[group]:
            asset = db.execute("SELECT * FROM assets WHERE asset_id=?", (entry["asset_id"],)).fetchone()
            if asset is None:
                raise SystemExit(f"Референс {entry['asset_id']} удалён из медиатеки")
            data = Path(asset["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise SystemExit(f"SHA-256 референса {entry['asset_id']} не совпадает с заданием")
            files.append((group, asset["original_name"], data, asset["mime"]))
    db.close()
    return job, files


def load_golden_job() -> tuple[dict, list[tuple]]:
    """tests/golden/pictures_studio_job.json: the picture-only fixture with 1x1 PNG references."""
    record = json.loads(GOLDEN_JOB.read_text(encoding="utf-8"))
    job = {"job_id": "golden-pictures", "compiled_prompt": record["compiled_prompt"],
           "settings_json": json.dumps(record["settings"], ensure_ascii=False),
           "manifest_json": json.dumps(record["manifest"], ensure_ascii=False)}
    files = []
    for index, entry in enumerate(record["manifest"]["images"], 1):
        if entry["sha256"] != hashlib.sha256(GOLDEN_PNG).hexdigest():
            raise SystemExit("Фикстура golden изменилась: SHA-256 картинки не совпадает")
        files.append(("images", f"picture{index}.png", GOLDEN_PNG, "image/png"))
    if record["manifest"]["videos"] or record["manifest"]["audios"]:
        raise SystemExit("golden-фикстура с видео/аудио не поддерживается; укажи --job")
    return job, files


# ---------------------------------------------------------------- HTTP (stdlib)

def _request(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: float = 60):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    return urllib.request.urlopen(request, timeout=timeout)


def get_json(url: str, timeout: float = 30) -> dict:
    with _request(url, timeout=timeout) as response:
        return json.loads(response.read())


def post_multipart(url: str, fields: dict[str, str], files: list[tuple]) -> tuple[int, dict]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                     + str(value).encode() + b"\r\n")
    for form_name, filename, data, mime in files:
        safe = filename.replace('"', "_")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{form_name}"; filename="{safe}"\r\n'
                     f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    try:
        with _request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                      timeout=1800) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except ValueError:
            detail = exc.reason
        return exc.code, {"detail": detail}


def download(url: str, destination: Path) -> int:
    tmp = destination.with_suffix(".part")
    with _request(url, timeout=1800) as response, tmp.open("wb") as output:
        while block := response.read(1 << 20):
            output.write(block)
    tmp.replace(destination)
    return destination.stat().st_size


# ---------------------------------------------------------------- run

def continuation_source(job: dict, files: list[tuple]) -> bytes | None:
    """The previous clip of a «Продолжение» job (files follow the manifest order of each group)."""
    videos = json.loads(job["manifest_json"]).get("videos") or []
    index = next((n for n, entry in enumerate(videos) if entry.get("role") == "Продолжение"), None)
    if index is None:
        return None
    return [data for group, _name, data, _mime in files if group == "videos"][index]


def variant_fields(job: dict, name: str, plan: dict | None, swap_mask: str = "") -> dict[str, str]:
    fields = gpu_form_fields(job)
    fields["client_job_id"] = str(uuid.uuid4())  # a fresh GPU registry entry per variant
    fields.setdefault("speed", "quality")  # every variant uses the requested full-step sampler
    own = {k: (swap_mask if v == "@mask" else v) for k, v in VARIANTS[name].items() if not k.startswith("@")}
    if "@mask" in VARIANTS[name].values() and not swap_mask:
        raise SystemExit(f"{name}: нужен --swap-mask '{{\"performers\": [{{\"name\": ..., \"box\": [x, y, w, h]}}]}}'")
    fields.update({"ab_variant": name, **own})
    if plan is not None and name != "baseline":
        fields["ab_director_plan_json"] = json.dumps(plan, ensure_ascii=False)
    return fields


def shrink_videos(files: list[tuple], short_edge: int) -> list[tuple]:
    """The job's videos re-encoded at short_edge (as local_studio.reference_segments.segment_filter)."""
    import tempfile
    vf = (f"fps=24,scale='if(gt(iw,ih),-2,min({short_edge},iw))':'if(gt(iw,ih),min({short_edge},ih),-2)'"
          ":flags=lanczos")
    out = []
    for group, name, data, mime in files:
        if group != "videos":
            out.append((group, name, data, mime))
            continue
        with tempfile.TemporaryDirectory() as temp:
            src, dst = Path(temp) / "in.mp4", Path(temp) / "out.mp4"
            src.write_bytes(data)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
                            "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", str(dst)],
                           check=True, timeout=300)
            out.append((group, f"{short_edge}p-{name}", dst.read_bytes(), "video/mp4"))
    return out


def run(args: argparse.Namespace) -> Path:
    variants = KITS.get(args.variants, args.variants)
    names = [item.strip() for item in variants.split(",") if item.strip()]
    if args.sparse and "sparse" not in names:
        names.append("sparse")
    unknown = [name for name in names if name not in VARIANTS]
    # Without baseline the run must be merged with a run that has it (same job, seed and prompt).
    if unknown or not names or (names[0] != "baseline" and not args.merge):
        raise SystemExit(f"Варианты: {', '.join(VARIANTS)}; первым идёт baseline (лишние: {unknown})")
    if args.golden:
        job, files = load_golden_job()
    else:
        job, files = load_studio_job(args.job, Path(args.studio_root))
    if args.prompt_file:
        # Research: the same saved job (references, settings, seed) with another compiled prompt, e.g. a shot
        # format change on a part that Studio's production gates would stop (docs/reference/H3_ENCODER_RESEARCH.md).
        job = {**job, "compiled_prompt": Path(args.prompt_file).read_text(encoding="utf-8")}
    settings = json.loads(job["settings_json"])
    if args.duration:
        # Quick A/B on a short cut of the job (fewer tokens, minutes instead of most of an hour).
        settings["duration"] = args.duration
        settings.pop("length", None)
        job = {**job, "settings_json": json.dumps(settings, ensure_ascii=False)}
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    out = Path(args.out) if args.out else ROOT / "logs" / f"ab-{stamp}{'-' + args.tag if args.tag else ''}"
    log(f"Задание {job['job_id']}: {settings.get('width')}x{settings.get('height')}, "
        f"{settings['steps']} шагов, seed {settings['seed']}, референсов {len(files)}; варианты: {', '.join(names)}")
    if not args.dry_run and len(args.user_request.split()) < 2:
        # A/B swaps queued without the user's request burn GPU hours.
        raise SystemExit("A/B только по просьбе пользователя: передай его слова в --user-request \"…\"")
    if args.dry_run:
        for name in names:
            shown = {k: v for k, v in variant_fields(job, name, None, args.swap_mask).items()
                     if k.startswith("ab_") or k in VARIANTS[name]}
            shown.update({k: v for k, v in VARIANTS[name].items() if k.startswith("@")})
            print(f"  {name}: {json.dumps(shown, ensure_ascii=False)}")
        return out
    gpu = args.gpu_url.rstrip("/")
    try:
        caps = get_json(f"{gpu}/api/capabilities")
    except (OSError, ValueError) as exc:
        raise SystemExit(f"GPU API {gpu} недоступен: {exc} (туннель запущен?)")
    if "ab_v1" not in caps:
        raise SystemExit("GPU API без A/B-полей (нет ab_v1 в /api/capabilities): задеплой app/ из этой ветки")
    log(f"GPU API: контракт {caps.get('api_contract')}, A/B: {json.dumps(caps['ab_v1'], ensure_ascii=False)}")
    # An older GPU API ignores form fields it does not know: a «shift-8» would render the baseline.
    unsupported = [name for name in names if ("ab_shift" in VARIANTS[name] and not caps["ab_v1"].get("shift"))
                   or VARIANTS[name].get("ab_anchor", "") not in ["", *(caps["ab_v1"].get("anchor") or [])]]
    if unsupported:
        raise SystemExit(f"GPU API не знает ab_shift/ab_anchor (ab_v1.shift/anchor): задеплой app/ "
                         f"и h3-continuation-anchor или убери {', '.join(unsupported)}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompts").mkdir(exist_ok=True)
    source = continuation_source(job, files)
    if source is not None:
        (out / "continuation_source.mp4").write_bytes(source)
    elif any("ab_anchor" in VARIANTS[name] for name in names):
        raise SystemExit("ab_anchor: в задании нет видео «Продолжение»")
    results = {"created": datetime.now().isoformat(timespec="seconds"), "tag": args.tag or "",
               "gpu_url": gpu, "source": "golden" if args.golden else f"studio:{job['job_id']}",
               "settings": {k: settings.get(k) for k in ("width", "height", "length", "duration", "steps", "seed",
                                                         "cache_mode", "mode", "ref_image_size", "quality")},
               "capabilities_ab": caps["ab_v1"], "variants": []}
    plan = None
    for index, name in enumerate(names, 1):
        started = time.time()
        short_edge = VARIANTS[name].get("@source_short_edge")
        variant_files = shrink_videos(files, int(short_edge)) if short_edge else files
        fields = variant_fields(job, name, plan, args.swap_mask)
        if short_edge:
            # The GPU checks every upload against the manifest SHA-256: name the re-encoded bytes.
            manifest = json.loads(fields["manifest_json"])
            shrunk = [hashlib.sha256(data).hexdigest() for group, _, data, _ in variant_files if group == "videos"]
            for entry, digest in zip(manifest["videos"], shrunk):
                entry["sha256"] = digest
            fields["manifest_json"] = json.dumps(manifest, sort_keys=True)
        status, body = post_multipart(f"{gpu}/api/jobs", fields, variant_files)
        row = {"index": index, "name": name, "fields": VARIANTS[name], "submitted_at": started}
        if status >= 400:
            row.update(status="rejected", error=f"HTTP {status}: {body.get('detail')}")
            log(f"{name}: отклонён — {row['error']}")
            if name == "baseline":
                save(out, results | {"variants": [row]})
                raise SystemExit("Базовый вариант не принят; остальные не отправлены")
        else:
            director = body.get("director") or {}
            ab = director.get("ab") or {}
            row.update(status="queued", job_id=body["job_id"], steps=director.get("steps"),
                       director_fallback=director.get("director_fallback"),
                       ab={k: v for k, v in ab.items() if k not in {"prompt", "director_plan"}})
            (out / "prompts" / f"{index:02d}-{name}.txt").write_text(ab.get("prompt", ""), encoding="utf-8")
            if name == "baseline":
                plan = ab.get("director_plan")
                if plan is not None:
                    (out / "director_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
                if director.get("director_fallback"):
                    log(f"ВНИМАНИЕ: Qwen не дала план для baseline ({director['director_fallback'][:120]}); "
                        "все варианты повторят тот же запасной маршрут (промпт Studio)")
            log(f"{name}: принят, GPU job {body['job_id']}, prompt {ab.get('base_prompt_sha256', '')[:12]}")
        results["variants"].append(row)
        save(out, results)
    poll(gpu, results, out, args)
    write_report(out, results, [load_results(Path(item)) for item in args.merge or []])
    log(f"Готово: {out / 'index.html'}")
    return out


def poll(gpu: str, results: dict, out: Path, args: argparse.Namespace) -> None:
    deadline = time.time() + args.timeout
    pending = [row for row in results["variants"] if row["status"] not in TERMINAL]
    last_line = ""
    # A preempted GPU (Nebius, 05.11) forgets its running job: the registry keeps "running" or the API is
    # gone. No answer or no progress for STALL_S means the render is lost; stop so the caller can retry.
    stall_s = float(os.environ.get("H3_AB_STALL_S", 1200))
    seen: dict[str, tuple[str, float]] = {}
    while pending and time.time() < deadline:
        for row in pending:
            try:
                status = get_json(f"{gpu}/api/jobs/{row['job_id']}")
            except (OSError, ValueError) as exc:
                row["poll_error"] = str(exc)
                status = {}
            mark = json.dumps([status.get("status"), status.get("sampling_progress")], sort_keys=True)
            if seen.get(row["job_id"], ("", 0))[0] != mark:
                seen[row["job_id"]] = (mark, time.time())
            elif status.get("status") not in ("queued",) and time.time() - seen[row["job_id"]][1] > stall_s:
                raise SystemExit(f"{row['name']}: нет ответа или прогресса {int(stall_s)} с — задание потеряно (GPU перезапускался?)")
            if not status:
                continue
            row["status"] = status.get("status", "unknown")
            if status.get("vram"):
                row["vram"] = status["vram"]
            if status.get("sampling_progress"):
                row["progress"] = status["sampling_progress"]
            if row["status"] in TERMINAL:
                row["finished_at"] = time.time()
                row["timing"] = status.get("timing")
                row["cache_runtime"] = status.get("cache_runtime")
                row["error"] = (status.get("error") or "").strip() or None
                if row["status"] == "success":
                    target = out / f"{row['index']:02d}-{row['name']}.mp4"
                    row["video"] = target.name
                    row["video_bytes"] = download(f"{gpu}{status['video_url']}", target)
                    if (out / "continuation_source.mp4").is_file():
                        overlap = ANCHOR_FRAMES.get(row["fields"].get("ab_anchor"), 22)
                        try:
                            row["seam"] = seam_jump(out / "continuation_source.mp4", target, overlap)
                        except (ValueError, subprocess.SubprocessError) as exc:
                            row["seam"] = {"error": str(exc)}
                log(f"{row['name']}: {row['status']}" + (f" — {row['error']}" if row.get("error") else ""))
        pending = [row for row in pending if row["status"] not in TERMINAL]
        line = ", ".join(f"{row['name']}={row['status']}"
                         + (f" {row['progress']['value']}/{row['progress']['max']}" if row.get("progress") else "")
                         for row in pending)
        if line and line != last_line:
            log(f"жду: {line}")
            last_line = line
        save(out, results)
        if pending:
            time.sleep(args.poll)
    for row in pending:
        row["status"], row["error"] = "timeout", f"не завершился за {args.timeout} с"


def post_upscale(gpu: str, video: Path, model: str) -> tuple[int, dict]:
    fields = {"client_job_id": str(uuid.uuid4()), "mode": "upscale", "ab_seedvr2_model": model}
    return post_multipart(f"{gpu}/api/upscale/jobs", fields, [("video", video.name, video.read_bytes(), "video/mp4")])


def upscale(args: argparse.Namespace) -> Path:
    """SeedVR2 sizes on the baseline clip of a finished run; rows are appended to its report."""
    out = Path(args.run_dir)
    results = load_results(out)
    results.pop("_dir", None)
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models or set(models) - set(SEEDVR2_MODELS):
        raise SystemExit(f"Модели SeedVR2: {', '.join(SEEDVR2_MODELS)}")
    baseline = next((row for row in results["variants"] if row["name"] == "baseline" and row.get("video")), None)
    if baseline is None:
        raise SystemExit("В прогоне нет готового baseline.mp4")
    gpu = args.gpu_url.rstrip("/")
    available = (get_json(f"{gpu}/api/capabilities").get("ab_v1") or {}).get("seedvr2_models") or []
    rows = []
    for model in models:
        if model not in available:
            log(f"SeedVR2 {model}: весов нет на сервере (H3_SEEDVR2_7B=1 для 7b) — пропуск")
            continue
        status, body = post_upscale(gpu, out / baseline["video"], model)
        index = len(results["variants"]) + 1
        row = {"index": index, "name": f"upscale-{model}", "fields": {"ab_seedvr2_model": model},
               "submitted_at": time.time(), "steps": None}
        if status >= 400:
            row.update(status="rejected", error=f"HTTP {status}: {body.get('detail')}")
        else:
            row.update(status="queued", job_id=body["job_id"])
        results["variants"].append(row)
        rows.append(row)
    deadline = time.time() + args.timeout
    while any(row["status"] not in TERMINAL for row in rows) and time.time() < deadline:
        for row in rows:
            if row["status"] in TERMINAL:
                continue
            state = get_json(f"{gpu}/api/upscale/jobs/{row['job_id']}")
            row["status"] = state.get("status", "unknown")
            if row["status"] in TERMINAL:
                row["timing"] = state.get("timing")
                row["error"] = (state.get("error") or "").strip() or None
                if row["status"] == "success":
                    target = out / f"{row['index']:02d}-{row['name']}.mp4"
                    row["video"] = target.name
                    row["video_bytes"] = download(f"{gpu}/api/upscale/jobs/{row['job_id']}/video", target)
                log(f"{row['name']}: {row['status']}")
        save(out, results)
        if any(row["status"] not in TERMINAL for row in rows):
            time.sleep(args.poll)
    save(out, results)
    write_report(out, results, [])
    return out


def save(out: Path, results: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


def load_results(run_dir: Path) -> dict:
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    results["_dir"] = run_dir.resolve()
    return results


# ---------------------------------------------------------------- report

def metrics(row: dict) -> dict:
    timing = row.get("timing") or {}
    sampling = (timing.get("phases") or {}).get("sampling")
    steps = row.get("steps")
    vram = row.get("vram") or {}
    return {
        "seam": (row.get("seam") or {}).get("jump"),
        "total": timing.get("total_seconds"),
        "sampling": sampling,
        "per_step": round(sampling / steps, 2) if sampling and steps else None,
        "vram": vram.get("peak_used_gb"), "vram_total": vram.get("total_gb"), "vram_samples": vram.get("samples"),
    }


def _fmt(value, suffix="") -> str:
    return "—" if value is None else f"{value}{suffix}"


def _rows(results: dict, out: Path, label: str) -> list[dict]:
    base = results.get("_dir", out)
    rows = []
    for row in results["variants"]:
        video = row.get("video")
        rows.append({**row, "label": f"{label}{row['name']}",
                     "src": os.path.relpath(Path(base).resolve() / video, out.resolve()) if video else None, **metrics(row)})
    return rows


def write_report(out: Path, results: dict, merged: list[dict]) -> None:
    rows = _rows(results, out, f"{results['tag']}:" if merged and results.get("tag") else "")
    for other in merged:
        rows += _rows(other, out, f"{other.get('tag') or other['_dir'].name}:")
    prompt_hashes = {row.get("ab", {}).get("base_prompt_sha256") for row in rows if row.get("ab")}
    same_prompt = len(prompt_hashes) == 1
    s = results["settings"]
    md = [f"# A/B {results['created']} {results['tag']}".rstrip(), "",
          f"Источник: `{results['source']}`, GPU API `{results['gpu_url']}`. "
          f"{s.get('width')}x{s.get('height')}, {s.get('length')} кадров, {s.get('steps')} шагов, "
          f"seed {s.get('seed')}, cache {s.get('cache_mode')}, режим {s.get('mode')}.", "",
          ("Промпт до A/B-изменений одинаков у всех вариантов: `" + next(iter(prompt_hashes))[:16] + "`."
           if same_prompt else "**ВНИМАНИЕ: промпты вариантов различаются (base_prompt_sha256)** — сравнение нечестное."),
          "", "| # | Вариант | Статус | Рендер, с | Сэмплинг, с | с/шаг | Пик VRAM, ГБ | Скачок стыка | base prompt | graph | Видео |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        ab = row.get("ab") or {}
        md.append(f"| {row['index']} | {row['label']} | {row['status']} | {_fmt(row['total'])} | {_fmt(row['sampling'])} | "
                  f"{_fmt(row['per_step'])} | {_fmt(row['vram'])}{'/' + str(row['vram_total']) if row['vram_total'] else ''} | "
                  f"{_fmt(row['seam'], '×')} | "
                  f"`{ab.get('base_prompt_sha256', '')[:12]}` | `{ab.get('graph_sha256', '')[:12]}` | "
                  f"{('[' + row['src'] + '](' + row['src'] + ')') if row['src'] else (row.get('error') or '—')} |")
    md += ["", "Варианты (поля формы POST /api/jobs):", ""]
    for row in rows:
        md.append(f"- **{row['label']}** — `{json.dumps(row.get('fields') or {}, ensure_ascii=False)}`"
                  + (f"; ошибка: {row['error']}" if row.get("error") else ""))
    md += ["", "Скачок стыка (только «Продолжение») — изменение кадра на склейке с прошлым клипом, делённое на обычное "
               "между соседними кадрами (scripts/h3_seam_jump.py): 1× — склейки не видно.",
           "", "Пик VRAM — максимум занятой памяти GPU (вся карта, включая спящую Qwen) по `/system_stats` "
               "ComfyUI в моменты опроса статуса; с/шаг = фаза sampling / шаги. Промпты — `prompts/`, "
               "план Qwen baseline — `director_plan.json`, сырые данные — `results.json`."]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "index.html").write_text(_html(rows, results, same_prompt), encoding="utf-8")


def _html(rows: list[dict], results: dict, same_prompt: bool) -> str:
    cards = []
    for row in rows:
        stats = (f"рендер {_fmt(row['total'], ' с')} · {_fmt(row['per_step'], ' с/шаг')} · "
                 f"VRAM {_fmt(row['vram'], ' ГБ')}")
        media = (f'<video src="{html.escape(row["src"])}" preload="auto" playsinline muted loop></video>'
                 if row["src"] else f'<div class="empty">{html.escape(str(row.get("error") or row["status"]))}</div>')
        cards.append(f'<figure>{media}<figcaption><b>{html.escape(row["label"])}</b>'
                     f'<span>{html.escape(json.dumps(row.get("fields") or {}, ensure_ascii=False))}</span>'
                     f'<span>{html.escape(stats)}</span></figcaption></figure>')
    title = html.escape(f"A/B {results['created']} {results['tag']}".strip())
    warning = "" if same_prompt else '<p class="warn">Промпты вариантов различаются — см. report.md</p>'
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>H3 A/B</title>
<style>
:root {{ --bg:#f6f6f4; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#deded8; --warn:#a33; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --fg:#ecece8; --muted:#9a9a93; --card:#1f1f1d; --line:#33332f; --warn:#f77; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--fg); font:14px/1.4 -apple-system, system-ui, sans-serif; }}
header {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:12px; }}
h1 {{ font-size:16px; margin:0 12px 0 0; }}
button {{ font:inherit; padding:6px 12px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); cursor:pointer; }}
input[type=range] {{ flex:1; min-width:160px; }}
.grid {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fill, minmax(min(100%, 260px), 1fr)); }}
figure {{ margin:0; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
video, .empty {{ display:block; width:100%; aspect-ratio:{results['settings'].get('width') or 9}/{results['settings'].get('height') or 16}; background:#000; object-fit:contain; }}
.empty {{ display:flex; align-items:center; justify-content:center; padding:12px; color:var(--warn); background:var(--bg); }}
figcaption {{ padding:8px; display:grid; gap:2px; }} figcaption span {{ color:var(--muted); font-size:12px; overflow-wrap:anywhere; }}
.warn {{ color:var(--warn); font-weight:600; }}
</style></head><body>
<header><h1>{title}</h1>
<button id="play">▶ Все</button><button id="restart">⏮ С начала</button>
<label><input type="checkbox" id="sound"> звук первого</label>
<input type="range" id="seek" min="0" max="1000" value="0" aria-label="Позиция">
<span id="time">0.0 с</span></header>
{warning}
<div class="grid">{''.join(cards)}</div>
<script>
const videos = [...document.querySelectorAll('video')];
const master = videos[0];
const seek = document.getElementById('seek'), play = document.getElementById('play');
function sync(t) {{ videos.forEach(v => {{ if (Math.abs(v.currentTime - t) > 0.04) v.currentTime = Math.min(t, (v.duration || t)); }}); }}
play.onclick = () => {{
  if (!master) return;
  const paused = master.paused;
  sync(master.currentTime);
  videos.forEach(v => paused ? v.play().catch(() => {{}}) : v.pause());
  play.textContent = paused ? '⏸ Пауза' : '▶ Все';
}};
document.getElementById('restart').onclick = () => sync(0);
document.getElementById('sound').onchange = e => {{ if (master) master.muted = !e.target.checked; }};
seek.oninput = () => {{ if (master && master.duration) sync(master.duration * seek.value / 1000); }};
if (master) {{
  master.addEventListener('timeupdate', () => {{
    document.getElementById('time').textContent = master.currentTime.toFixed(1) + ' с';
    if (master.duration) seek.value = Math.round(1000 * master.currentTime / master.duration);
    if (!master.paused) videos.slice(1).forEach(v => {{ if (Math.abs(v.currentTime - master.currentTime) > 0.15) v.currentTime = master.currentTime; }});
  }});
  master.addEventListener('seeked', () => sync(master.currentTime));
}}
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="отправить варианты, дождаться, скачать MP4, написать отчёт")
    run_p.add_argument("--gpu-url", default=DEFAULT_GPU_URL)
    source = run_p.add_mutually_exclusive_group(required=True)
    source.add_argument("--job", help="ID (или префикс) сохранённого задания Studio")
    source.add_argument("--golden", action="store_true", help="tests/golden/pictures_studio_job.json")
    run_p.add_argument("--prompt-file", help="исследование: тот же job (референсы, настройки, сид) с другим скомпилированным промптом из файла")
    run_p.add_argument("--studio-root", default=str(STUDIO_ROOT))
    run_p.add_argument("--variants", default=DEFAULT_VARIANTS,
                       help=f"«quality» = {QUALITY_KIT}; или из: {', '.join(VARIANTS)}")
    run_p.add_argument("--duration", type=int, default=0, help="секунды тестового отрезка (4–15), по умолчанию как в задании")
    run_p.add_argument("--sparse", action="store_true", help="добавить вариант sparse (BlockSparseAttention)")
    run_p.add_argument("--tag", default="", help="метка прогона, например comfy-b5cc8830")
    run_p.add_argument("--swap-mask", default="", help="JSON маски замены для swap-mask*: {\"performers\": [{\"name\": \"HERO\", \"box\": [x, y, w, h]}]}")
    run_p.add_argument("--out", default="")
    run_p.add_argument("--merge", action="append", help="папка прошлого прогона: добавить его ролики в отчёт")
    run_p.add_argument("--poll", type=float, default=10.0)
    run_p.add_argument("--timeout", type=float, default=6 * 3600)
    run_p.add_argument("--dry-run", action="store_true", help="только показать варианты, без запросов")
    run_p.add_argument("--user-request", default="", help="слова пользователя, которыми он попросил этот A/B (обязательно без --dry-run)")
    report_p = sub.add_parser("report", help="пересобрать report.md/index.html (с --merge)")
    report_p.add_argument("run_dir")
    report_p.add_argument("--merge", action="append")
    up_p = sub.add_parser("upscale", help="SeedVR2 3B/7B на baseline готового прогона (A/B апскейла)")
    up_p.add_argument("run_dir")
    up_p.add_argument("--models", default="3b,7b")
    up_p.add_argument("--gpu-url", default=DEFAULT_GPU_URL)
    up_p.add_argument("--poll", type=float, default=10.0)
    up_p.add_argument("--timeout", type=float, default=3 * 3600)
    args = parser.parse_args(argv)
    if args.command == "run":
        run(args)
    elif args.command == "upscale":
        upscale(args)
    else:
        out = Path(args.run_dir)
        write_report(out, load_results(out), [load_results(Path(item)) for item in args.merge or []])
        log(f"Отчёт: {out / 'report.md'}, {out / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
