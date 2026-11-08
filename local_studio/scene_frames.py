"""Start frames of a scene run made and checked by the agents, so a scene goes on overnight without the GPU queue
or the user (local_studio/scene_runs.py).

draw():   Codex draws the shot's first frame with its built-in image_gen (the user's ChatGPT subscription, no GPU):
          the same request the GPU keyframe would get (storyboard-model.mjs autoKeyframeRequest), its pictures attached.
review(): Claude looks at the frame next to the hero and place references and accepts it or says what is wrong —
          in place of the user's «✓ Принять кадр» when the run allows it (options.autoAccept).
"""
from __future__ import annotations

from .character_identity import PROPORTION_QC

import asyncio
import io
import json
import os
import tempfile
import time
from pathlib import Path

from .agent_brain import BrainRequest, ask, clean_env, executable

DRAW_TIMEOUT_S = 900
_draws = asyncio.Semaphore(2)  # a scene's frames one after another, two scenes side by side
_reviews = asyncio.Semaphore(3)  # Claude checks of frames and takes across all running scenes

DRAW_RULES = (
    "Rules of a STUDIO start frame: follow the shot's specified visual medium "
    "and the character references (animation stays animation; live action stays live action), one moment, the exact number of "
    "characters the shot names and nobody else; every hero keeps the face, fur or skin, body and costume of their "
    "reference picture; the place keeps the layout, materials and light of its reference; correct anatomy and "
    "contacts (paws, hands, feet on surfaces); no text, logos, watermarks, captions or frames; no split screen, side-by-side "
    "panels or picture-in-picture. "
    "Compare each visible character to their own named reference: face geometry, age, hair/fur markings, body "
    "build and costume. Never replace an adult with a child or a named character with a generic lookalike. "
    "Preserve stated relative sizes on a shared ground plane. Use perspective to explain apparent size; "
    "do not impose a generic human/cat height ratio over the franchise's explicit proportions. "
    "An environment or composition picture controls layout, not the identity of people or animals inside it. "
    "A frame taken from the source video gives ONLY camera, framing, lens, poses and action: never copy its room, "
    "furniture, wall colours, props or anyone's clothes; the room comes from the environment picture.")

# A still from the source video: drawn "Redraw image 1 exactly" from such stills without the franchise
# place, even keyframe-per-shot takes keep the source room.
SOURCE_FRAME_ROLES = frozenset({"Композиция кадра", "Кадр исходника"})

REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["accept", "problems"],
                 "properties": {"accept": {"type": "boolean"}, "problems": {"type": "string"}}}


SPLIT_SEAM = 0.75  # share of rows crossed by one central vertical edge: two-panel sheets 0.85–1.0, real refs ≤ 0.48


def split_panel(data: bytes) -> bool:
    """True for a side-by-side sheet: one vertical edge near the centre runs down almost the whole picture.
    PIL only: the Studio service venv has no numpy."""
    from PIL import Image
    try:
        image = Image.open(io.BytesIO(data)).convert("L")
    except (OSError, ValueError):  # not a picture: other checks report a broken reference
        return False
    if image.width <= image.height:  # the idea sheets are landscape; portrait refs have no such seam
        return False
    width, height = 600, max(1, round(600 * image.height / image.width))
    pixels = image.resize((width, height)).tobytes()
    best = 0.0
    for x in range(int(width * 0.35), int(width * 0.65)):
        edges = sum(abs(pixels[row + x + 1] - pixels[row + x]) > 25 for row in range(0, width * height, width))
        best = max(best, edges / height)
    return best >= SPLIT_SEAM


def _size(size: str) -> tuple[int, int]:
    width, height = (int(part) for part in size.split("x"))
    return width, height


def fit(data: bytes, size: str) -> bytes:
    """Centre crop and resize to the frame size of the scene (9:16 768x1344 …), PNG."""
    from PIL import Image
    width, height = _size(size)
    image = Image.open(io.BytesIO(data)).convert("RGB")
    scale = max(width / image.width, height / image.height)
    image = image.resize((max(width, round(image.width * scale)), max(height, round(image.height * scale))), Image.LANCZOS)
    left, top = (image.width - width) // 2, (image.height - height) // 2
    out = io.BytesIO()
    image.crop((left, top, left + width, top + height)).save(out, "PNG")
    return out.getvalue()


def draw_prompt(request: dict, references: list[dict], feedback: str) -> str:
    lines = [f"image {at}: {ref.get('role') or 'reference'}" + (f" of {ref['owner']}" if ref.get("owner") else "")
             for at, ref in enumerate(references, 1)]
    width, height = _size(request.get("size") or "768x1344")
    orientation = "vertical 9:16" if height > width else "horizontal 16:9" if width > height else "square"
    return "\n".join(filter(None, [
        "Use your built-in image generation tool (image_gen) exactly once to make ONE still frame, "
        f"{orientation}. It is the first frame of a video shot; the attached pictures are its references:",
        (f"EDIT image {request['edit_image']}: it is the real frame of this shot. Keep its exact composition, camera "
         "angle and framing, every object and piece of furniture with its count, size and place, and every pose; "
         "change only what the description below asks for (the look of the room and who the performers are). "
         "A performer is replaced entirely, even where only a knee, a hand or a foot is in view: the hero's own "
         "clothes, hair and fur from their pictures, never the performer's — but only the body parts this image "
         "shows: never add a hero's legs, feet or body that are out of view here. "
         "Never redraw the scene from scratch.") if request.get("edit_image") else "",
        "Draw in the visual medium of the character reference pictures and the 'Scene style' line: animated "
        "(3D or 2D) references mean an animated frame in that same look, never a photograph; photographic references "
        "mean live action.",
        *lines,
        "References may include characters needed later in the video. Put a character in this still only when the "
        "FIRST FRAME description explicitly places that character in view; do not assemble a group portrait from "
        "all attached character references.",
        "", request["prompt"], "", DRAW_RULES,
        f"A previous attempt was rejected for: {feedback}. Fix exactly that." if feedback else "",
        "Do not write files, do not run commands. After the image is generated, answer with the single word DONE.",
    ]))


async def draw(request: dict, references: list[dict], pictures: list[Path], feedback: str = "") -> bytes:
    """Codex image_gen → PNG bytes of the generated frame, cropped to request['size']."""
    validate_inputs(references, pictures)
    exe = executable("codex")
    if not exe:
        raise RuntimeError("Codex не найден на этом Mac")
    env = clean_env()
    generated = Path(env.get("CODEX_HOME") or Path.home() / ".codex") / "generated_images"
    async with _draws:
        with tempfile.TemporaryDirectory(prefix="h3-frame-") as tmp:
            cmd = [exe, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
                   "--json", "-C", tmp, *[f"--image={path}" for path in pictures],
                   draw_prompt(request, references, feedback)]
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=tmp, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), DRAW_TIMEOUT_S)
            except asyncio.TimeoutError:
                proc.kill()
                raise RuntimeError("Codex не нарисовал кадр за 15 минут")
    thread = ""
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        thread = thread or event.get("thread_id") or (event.get("thread") or {}).get("id") or ""
    folder = generated / thread if thread else None
    images = sorted(folder.glob("*.png"), key=lambda path: path.stat().st_mtime) if folder and folder.is_dir() else []
    if not images:
        tail = (err.decode("utf-8", "replace") + out.decode("utf-8", "replace"))[-400:]
        if "limit" in tail.lower():
            raise RuntimeError("у Codex закончился лимит подписки — переключи аккаунт в панели агента")
        raise RuntimeError(f"Codex не вернул картинку: {tail}")
    return fit(images[-1].read_bytes(), request.get("size") or "768x1344")


# Across the cut, the way a script supervisor checks a frame against the shot before it on screen.
CONTINUITY_RULES = (
    "Compare this frame with the previous shot across the cut and reject when: a hero swaps screen side without a "
    "camera move that motivates it, or in a shot/reverse-shot pair both heroes look the same screen direction (180° "
    "rule); eyelines do not meet the person or thing they look at; a posture changes with no action between the shots "
    "(sitting becomes standing, a cat on the floor is suddenly on the table); a costume, its colours, fur markings or a "
    "prop in a hand differ; in the same place the light direction, time of day or colour temperature changes; the room "
    "behind the heroes contradicts the layout seen before. A cut to another place, or into a fantasy through the camera "
    "move the passport allows, is not a break of place and light, but heroes still keep their look.")


async def review(frame: Path, shot_text: str, references: list[dict], pictures: list[Path],
                 previous: dict | None = None) -> dict:
    """Claude's acceptance of a start frame: {accept, problems}. previous = {"frame": Path, "text": str} is the
    accepted frame of the shot before it on screen; with it the frame is also checked across the cut."""
    validate_inputs(references, pictures)
    before = Path(previous["frame"]) if previous and previous.get("frame") else None
    if before and (not before.is_file() or not before.stat().st_size):
        raise ValueError("Кадр предыдущего шота отсутствует или пуст")
    lines = [f"- {path}: {ref.get('role') or 'reference'}" + (f" of {ref['owner']}" if ref.get("owner") else "")
             for ref, path in zip(references, pictures)]
    prompt = "\n".join([
        "You accept or reject the first frame of a video shot for the H3 Studio, in place of the director.",
        f"Open and look at the frame: {frame}", "Then at its references:", *lines,
        "", f"The shot: {shot_text}", "", DRAW_RULES, "",
        *([f"The previous shot on screen, accepted: {before}", f"What it shows: {str(previous.get('text') or '')[:1200]}",
           CONTINUITY_RULES, ""] if before else []),
        "Reject when a hero does not match their reference (face, fur or skin, costume), when the count of characters "
        "is wrong, when anatomy or contacts are broken, when there is text or a logo, or when the frame does not show "
        "what the shot starts with. Inspect ALL supplied pictures before accepting. If a picture cannot be read "
        "or identity/scale cannot be verified, reject and name the missing evidence. "
        "Do not accept a matching room as proof of matching characters. problems: what exactly is wrong, in English, "
        "short and concrete, so the next attempt can fix it; empty when accepted.",
    ])
    async with _reviews:
        result = await ask(BrainRequest(provider="claude", model=os.environ.get("H3_REVIEW_MODEL", "opus"), prompt=prompt,
                                        schema=REVIEW_SCHEMA, read_repo=False, timeout_s=600,
                                        images=[str(frame), *map(str, pictures), *([str(before)] if before else [])]))
    data = result.get("data") or {}
    if not isinstance(data.get("accept"), bool):
        raise RuntimeError(f"Claude не дал ответа по кадру: {str(result.get('text'))[:200]}")
    return {"accept": data["accept"], "problems": str(data.get("problems") or "")[:1000], "at": time.time(),
            "usage": result.get("usage"), "input_summary": result.get("input_summary"), "seconds": result.get("seconds")}


async def review_take(sheet: Path, shot_text: str, technical: dict, references: list[dict], pictures: list[Path]) -> dict:
    """Claude's acceptance of a finished take from its contact sheet: {accept, problems} (options.autoAcceptTakes)."""
    validate_inputs(references, pictures)
    lines = [f"- {path}: {ref.get('role') or 'reference'}" + (f" of {ref['owner']}" if ref.get("owner") else "")
             for ref, path in zip(references, pictures)]
    prompt = "\n".join([
        "You review a finished video take for the H3 Studio, in place of the director.",
        f"Open and look at its contact sheet (frames in time order, left to right, top to bottom): {sheet}",
        "Then at the hero references:", *lines,
        "", f"The shot: {shot_text}", "",
        f"Technical scan of the full MP4: {json.dumps(technical, ensure_ascii=False)}", "",
        PROPORTION_QC + " " +
        "Reject when a hero does not match their reference (face, fur or skin, costume), when a character appears, "
        "disappears or duplicates against the shot, when anatomy or contacts break, when a character's size or body "
        "proportions against another character or the furniture differ from the references, when the frame splits into "
        "panels or a fantasy appears as a separate picture instead of through one continuous camera move, when there is text, a logo or a "
        "hard cut inside the take, or when the action the shot asks for does not happen. problems: what exactly is "
        "wrong, in English, short and concrete; empty when accepted.",
    ])
    async with _reviews:
        result = await ask(BrainRequest(provider="claude", model=os.environ.get("H3_REVIEW_MODEL", "opus"), prompt=prompt,
                                        schema=REVIEW_SCHEMA, read_repo=False, timeout_s=600,
                                        images=[str(sheet), *map(str, pictures)]))
    data = result.get("data") or {}
    if not isinstance(data.get("accept"), bool):
        raise RuntimeError(f"Claude не дал ответа по дублю: {str(result.get('text'))[:200]}")
    return {"accept": data["accept"], "problems": str(data.get("problems") or "")[:1000], "at": time.time(),
            "usage": result.get("usage"), "seconds": result.get("seconds")}


def validate_inputs(references: list[dict], pictures: list[Path]) -> None:
    """Reject incomplete visual context before spending an agent call."""
    if len(references) != len(pictures):
        raise ValueError("Число файлов не совпадает с подписанными референсами")
    for ref, picture in zip(references, pictures):
        if not picture.is_file() or not picture.stat().st_size:
            raise ValueError("Файл референса отсутствует или пуст")
        if ref.get("role") in {"Лицо героя", "Персонаж целиком", "Костюм"} and not str(ref.get("owner") or "").strip():
            raise ValueError("Референс героя должен иметь владельца до вызова агента")
    roles = {ref.get("role") for ref in references}
    if roles & SOURCE_FRAME_ROLES and "Окружение" not in roles:
        raise ValueError("Кадр исходника без фото «Окружение»: художник перерисует комнату исходника, а не мир франшизы. "
                         "Добавьте фото места франшизы.")
