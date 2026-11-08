"""«Чат с агентом»: talk to Codex or Claude Code inside the Studio, on the user's own subscription.

Like Ponder's chat, but only through the official CLIs the user is logged in with (agent_brain.py:
clean environment, no API keys). Every chat keeps the CLI's own session id, so each message resumes
the same conversation (`claude -p --resume`, `codex exec resume`). The agent works with the Studio
through the h3-studio MCP server (scripts/h3_studio_mcp.py): bibles, franchise profiles, assets,
shot_lint, edit sheets, jobs. Mode «read» blocks every write tool; mode «act» lets the agent create
cards, assemble edits and — with an approved story — queue renders. Codex runs in a read-only
sandbox and Claude only gets Read/Glob/Grep, so neither edits the repo or runs shell commands.

A run is a background task: events are kept in memory and in the chat file, the browser streams
them (NDJSON) and can reconnect; closing the page does not stop the agent, «Стоп» does.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import signal
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .agent_brain import CLAUDE_DEFAULT, REPO, clean_env, executable

MCP_SCRIPT = REPO / "scripts" / "h3_studio_mcp.py"
PYTHON = "/opt/homebrew/bin/python3" if os.access("/opt/homebrew/bin/python3", os.X_OK) else "/usr/bin/python3"
CODEX_MODELS = Path.home() / ".codex" / "models_cache.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
CLAUDE_MODELS = [("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")]
# How hard the model thinks (CLI flags: claude --effort, codex -c model_reasoning_effort). Lighter = fewer tokens.
EFFORTS = {"claude": [("low", "Лёгкая"), ("medium", "Средняя"), ("high", "Высокая")],
           "codex": [("low", "Лёгкая"), ("medium", "Средняя"), ("high", "Высокая"), ("xhigh", "Очень высокая"),
                     ("max", "Максимальная")]}
EFFORT_PATTERN = "^(|low|medium|high|xhigh|max)$"
CODEX_DEFAULT = "gpt-6-sol"
CODEX_DEFAULT_EFFORT = "low"
CODEX_OFF_FEATURES = ("computer_use", "browser_use", "browser_use_external", "in_app_browser", "apps", "plugins",
                      "image_generation", "multi_agent", "memories", "tool_suggest")
CODEX_COMPACT_TOKENS = 120_000
RUN_TIMEOUT_S = 1800
MAX_RUNS = 4  # an orchestrator task plus the helpers it waits on
SHORT = 1200

# Director's standard for scene_draft. The franchise style skill and the craft skills live in the repo (skills/).
SCENE_CRAFT = (
    "Image handoff: you prepare the image brief, not the user. Supply a complete first_frame for every shot. "
    "For imagined-world shots set fantasy=true and supply scene.fantasy_world.description: a complete English brief "
    "for one consistent empty world, plus its real base_asset_id when known. Always supply franchise_id. "
    "Studio sends these fields, the story, references and franchise passport to the image-making chat automatically. "
    "Never tell the user to retype the story, image prompt, visual standards or franchise selection. "
    "Scene method: (1) franchise_list → franchise_profile and franchise_entities with type character, then "
    "location (the full bible is too long to read at once): heroes, costumes, props, locations, rules. "
    "For an idea or a scene based on an idea, search franchise_entities type=idea in the SAME franchise, "
    "open the matching franchise_entity and inspect its asset_ids with view_image before drafting. "
    "Use an idea image for analysis only. Never carry a two-panel or split concept image into scene or shot "
    "references, including role Composition: it would contaminate the frame and character scale. Use separate "
    "single-frame location assets for real and fantasy spaces; keep hero identity references from the "
    "character/profile cards. An idea image does not approve the story or replace hero/location identity. "
    "If no matching idea or usable image exists, say so rather than borrowing another franchise's picture. "
    "Read the franchise's own style skill when it has one (see skills/README.md) plus "
    "skills/minimax-video-production. "
    "(2) franchise_resolve_shots gives each shot its exact reference files and roles; the scene shares one "
    "reference set (≤9 images), never two outfits on one body. Every scene needs its place: a location photo "
    "(role Окружение) from the franchise location entity, else from the library (assets_list role=Окружение, check "
    "with view_image) — keyframes build the space and composition from it; when the location entity has no photo, "
    "say which library photo you used so the user can attach it to the location. (3) Write it as a short-form series: a hook in the "
    "first 2 seconds, every shot a new event or turn (goal → attempt → obstacle → new tactic → visible outcome), "
    "rising stakes, a payoff and a button at the end — a rollercoaster, never furniture staging or idle poses. "
    "Living acting: eyes, face, body and hands of every hero change thought in a cause-and-effect chain. "
    "(4) Camera with motivation: every shot is ONE continuous take with one framing, lens, angle and at most one "
    "camera move (its storyboard fields); framing sizes alternate from shot to shot (wide → close → reaction), never "
    "inside a shot. Keep the 180° axis, screen direction and eyelines. Set up the whole canvas, not only the prompts: "
    "scene mode exactly as the franchise passport's rendering (photoreal → realistic, 3D → animation3d; never change it "
    "on an ambiguous remark — ask one question), quality 0.8 MP (the Studio default, 672x1216 vertical; another size only when the user asks for it), a scene-wide director set from director_presets "
    "(camera body, lighting, color, style, acting — matching the franchise look) and a shot's own director where it "
    "differs (lens look, focus, a named camera move); every shot gets framing, angle, lens_mm and camera_motion. (5) Edit: cuts, "
    "match cuts, whip pans and smash cuts are seams BETWEEN shots (transition_in), never words inside a shot's prompt "
    "— a take told to cut, insert or show a montage comes back with hard cuts and pasted reference pictures. "
    "Each shot is a 10–15 s chapter of one continuous action (H3's native length; 4–6 s takes came back as montage), "
    "so a 30–60 s scene is 3–5 shots; a planned cut inside a take only as the official «[Shot 2] At 00:07.000, …». "
    "Write each shot as a chapter in the franchise's OWN method and style (studio_playbook names its skill when the "
    "franchise has one; never borrow another franchise's method): 400–900 words, a Subject line per hero with concrete marks and scale from that "
    "franchise's passport (for example «the cat is below the man's knee»), the room layout with left/right, "
    "the chapter's place in the story in summary, and one coherent scene image as «Кадр сцены» when a new setup starts. Seams: start_frame previous_end is the default for the next beat of one continuous action in "
    "the same place — the shot starts from the previous shot's last frame, so geography, light and props stay "
    "locked; new_keyframe only for the first shot, a new place or time, or a deliberate jump to a new camera "
    "position; at most three continuations in a row. "
    "(6) Do not type the H3 prompt: give scene_draft the scene's «place» (layout with screen left/right, what is "
    "where, light sources) and each shot's «take» fields — summary (the chapter's place in the story), cast (per hero: "
    "position, notices, eyes, decides, does, answers, ends), world (at least two of air, physics, light, background: "
    "what moves on its own around the heroes in THIS take), camera, end, lines, sound, music; every take is alive — "
    "eyes blink and check the partner, heroes answer each other, the room moves; Studio writes the official prompt from "
    "them with the heroes' passport descriptions and binds the pictures. Write a free «prompt» only when a take cannot "
    "be described by these fields; then the following applies. "
    "Prompts in English per the official MiniMax H3 format. Do NOT write <Picture N> lines for heroes, places or "
    "objects: Studio builds subject_definitions from the reference roles (a standalone <Picture N> means a frame to "
    "show, and H3 pastes it into the take). Each shot's [Shot 1] text opens with framing and angle, names every hero "
    "in view and gives 60–120 words of acting: where each one stands, what they notice, what they decide and do, how "
    "their face and body change, the end state; sound and English dialogue after it. Write what happens, never "
    "prohibitions («never», «no», «without» name the very thing H3 then draws). A scene-wide rule (for example: a "
    "fantasy enters by a push into the hero's eyes) goes only into the shot that shows the fantasy. An insert of an object "
    "with no hero in view gets no hero pictures. Studio rejects a scene that breaks these rules "
    "(local_studio/shot_contract.py) — fix the shot, do not work around the check. "
    "(7) Fix every shot_lint error/warning before you finish. "
    "Never queue renders or keyframes yourself in this flow: the scene appears as a card, the user applies it to "
    "the canvas and presses «Собрать сцену» — say so, do not claim it is already on the canvas.")

# How H3 actually reads a prompt: code facts plus attention probes (docs/reference/H3_ENCODER_RESEARCH.md).
H3_READING = (
    "How H3 reads your text (code facts; measured points are from attention probes on one scene, so treat "
    "them as current best practice, not law): Studio has Ref2VA only — at least one picture or video. The encoder is "
    "causal Qwen3-VL: each word carries only what came before it, references come first, and the last sentences "
    "carry the whole prompt. Prompts you queue (render_enqueue, composer) go to H3 as written; Studio adds only a "
    "reference-role line after them — so write camera, lens, light, sound and acting yourself, each once. Measured: "
    "the middle of the model listens to place and action (who stands where, what the body does), the deep layers to "
    "camera move, overall style, acting and the spoken line; attention per word drops as the prompt grows, so keep "
    "acting and the decisive beat dense and near the end next to the dialogue, and do not pad the environment. "
    "Name camera moves with the words H3 was trained on (h3-camera-shots) and state each setting once — repeated "
    "camera text is weighted again. Control map (16 one-phrase changes, same seed, docs/reference/"
    "H3_ENCODER_RESEARCH.md): camera moves work (static, push-in, pull-out, orbit ≈90–120°); light and time of day "
    "work strongest and override the reference picture's time; lens names (24 mm, 85 mm), depth of field, bokeh "
    "and rack focus are ignored — everything stays sharp, so do not spend words on optics. A shot size holds only "
    "if every action of the shot reads in it: an extreme close-up with hands on a rope or wiping the face comes out "
    "as a medium shot, a wide shot with detailed facial acting comes out medium; give a close-up only eye/face "
    "acting, a wide only body actions, or reach the size by a camera move (pushes in to…, pulls out to…). Each "
    "emotion change needs its own explicit phrase (removing that phrase's code removed the whole emotional arc; "
    "neighbouring sentences do not rebuild it), and a repeated light event without times (lightning) happens once "
    "at the start. Same seed and settings give a bit-identical video: rerolling with the same seed changes nothing. "
    "Cuts inside a take only in MiniMax's own form «[Shot 2] At 00:01.630, the shot cuts to …» — a range like "
    "«[Shot 2: 1.63–3.40s]» is not read as a cut (it landed 0.5–1.8 s late). Emotion beats can be timed «At 0:02 his "
    "eyes widen. At 0:03 …» (±0.5 s). Name the props a hero holds in every shot that has them. The frame can be "
    "16:9, 9:16, 4:3, 3:4 or 1:1; a video reference renders in its own format. Two ways to use a video reference "
    "(user decision): replace only the named characters and keep everything else of the source, or reskin the "
    "whole world on the source's mapping (camera, motion, layout, poses, timing) in the franchise's look. "
    "Full table: docs/guides/H3_PROMPT_GUIDE.md section 0. ")

# Editor's standard for edit sheets (edl_*): the engine in local_studio/edl.py, craft numbers after Ponder's skills.
EDIT_CRAFT = (
    "Edit method (edl_toolkit, edl_from_shots, edl_plan, edl_render): about 9 of 10 seams are straight cuts; "
    "cut on action about a third into the move; shorts average 1–3 s per shot with a visual change every 2–3 s, "
    "the strongest moment at the open or about two thirds in, end 1–2 s after the payoff. Seams: whip_pan, "
    "pass_through, morph only where the shots were shot for them; smash_cut into a loud beat; sound_bridge as a "
    "J-cut 0.3–1 s. transition_effect picks the look of a seam (zoomin, fadewhite flash, hblur, slides, pixelize…). "
    "Accents: shake with flash on an impact, glitch for a hacker/digital beat, chroma for a jolt; freeze_end 1–2 s "
    "for a freeze-frame character intro. auto_sfx true: whoosh on whips, impact with a riser into a smash, shutter "
    "and hit on freezes — few and earned, each tied to something visible, never the same effect back to back; "
    "add bank sounds by name only for a visible moment. Music dips under speech (duck), loudness -14 LUFS, true "
    "peak -1 dBTP. Graphics (edl_toolkit → graphics): NameCard on a freeze frame (anchor freeze) to introduce a "
    "hero, KineticTitle for a hook or a chapter, LowerThird for a new character, TitleCard for the episode title, "
    "Captions for spoken English lines; all text English, short (≤ 6 words a title), held at least 2 s, inside "
    "the safe box; at most one graphic on screen. Colour: the franchise look from its profile (edl_toolkit → looks: "
    "teal_orange, warm_film…) at 0.4–0.7, match_color on so H3 shots share one tone, a shot's own "
    "grade.exposure only to fix a dark or hot shot. After edl_render, check the plan warnings, shot_review and "
    "color_qc and fix them. When the user wants to finish by hand in Premiere Pro or DaVinci Resolve, edl_export the "
    "same sheet (and edl_export_resolve to open it in a running Resolve). Work from the film assembly on the "
    "story canvas; the separate «Монтаж» page is retired from Studio navigation. Preserve saved edit sheets and "
    "the user's own edits. Picture accents: shake, zoom (punch-in), flash, glitch, chroma, vhs, strobe, blur, bw, "
    "invert — 0.1–0.5 s on a beat, never wall to wall. Use edl_plan before edl_render and inspect the result.")

# How to behave in the Studio: the user dictates by voice and expects the work done, not a dialogue about it.
STUDIO_MANNER = (
    "The user dictates by voice: messages arrive as rough speech-to-text («айс дефенсы» = «ассеты»). Infer the intent from the screen, the franchise and the conversation; ask only when two "
    "readings lead to different work, and then offer the options in one line. Do the work instead of describing it: "
    "use the h3-studio tools for Studio data (franchises, profile, entities, assets, jobs) — not shell searches; "
    "for a production task, call studio_playbook with its workflow and franchise, use studio_readiness for the live "
    "queue/GPU/receipt preflight, then read only the relevant listed "
    "skill. Reuse deterministic Studio tools for queue, canvas, montage and QC; do not invent shell commands or "
    "rerender a failed part before reviewing its full MP4. Story approval: when an episode's story is not yet approved in "
    "docs/DECISIONS.md, show the story in 3–5 lines and ask «Одобряешь этот сюжет?»; when the user says yes in any "
    "words, record it with story_approval_record (their words verbatim) and continue in the same turn. A "
    "scene_draft or composer_draft is a proposal the user reviews, not a render: build it right away. In "
    "«Может действовать» you have the canvas itself: studio_apply_scene puts a scene on the open Studio canvas (no "
    "click needed), studio_board reads it, studio_build_scene presses «Собрать сцену» (GPU: only when the user asked "
    "to render or said yes), studio_reset_scene clears the active scene when the user asks. After «Собрать сцену» "
    "the Mac builds the scene by itself (keyframes, a stop for the user's «✓ Принять кадр», renders, last frames, the "
    "glue): do not poll or press anything for it; scene_run tells its state when the user asks, stop / resume it. "
    "A video the user attaches: look at it with asset_frames first. When they want someone in it replaced by their "
    "hero (or the hero to move like it), use it as «Исходное видео» with performers pairs (who in the video becomes "
    "which hero, named by place and action like 'the man on the left'; a look word only to tell two performers apart, and never repeat the performer's clothes or hair anywhere in the prompt) — or as «Движение» / «Движение и камера» to take only the motion — and the hero's own photos; "
    "pick the matching accepted recipe if the user has one (studio_accepted_recipes; it may be empty — then the "
    "built-in settings apply) and fill the form (composer_draft / "
    "studio_apply_draft). "
    "Photos the user attaches are in the Studio library (asset_id given): look at each one first. When a photo is "
    "a look to match (style, light, texture), read it like a cinematographer — light source, direction, softness, "
    "contrast and colour temperature; palette and saturation; texture, grain and sharpness; lens, depth of field "
    "and framing; rendering style — say it in a few concrete lines, map it to director_presets ids (lighting, "
    "color, style, camera, lens, focus) and say which ones and why. Apply it where the user means: the open scene "
    "(studio_apply_scene with that director set, the photo as a reference with role Стиль, Свет or Цвет), a clip "
    "(composer_draft), or the whole franchise (franchise_profile_update look) so every scene keeps the same look. "
    "A photo of a hero, costume, place or prop becomes a reference with its role and owner. Never copy people, "
    "logos or text from a look photo into the prompt. You review finished shots yourself (there is no separate "
    "QC model): job_frames gives the contact sheet, full decode, sound and hard cuts — compare faces and costumes "
    "with the references (view_image), anatomy, contacts, space, the start/end state and the seam to the next "
    "shot, then say accept or retake and why. If the user ordered a finished video, fix a proven defect and render "
    "the affected shot within the task's GPU authorization; reconcile ambiguous jobs before any retry. Camera moves: write them with H3's "
    "twelve primitives (skills/h3-camera-shots); a crash zoom never snaps in the render itself, so after the take is "
    "accepted job_snap_zoom makes the snapped copy. "
    "«Создать видео»: set up the whole form, not just the prompt — references with roles and owners, the "
    "performer pairs of a source video (role Исходное видео, performers: who in the source becomes which hero), "
    "scenario, resolution, steps, cache, reference size, seed and sound. For a hero swap or any task like a case "
    "that already worked, read studio_accepted_recipes first and pass the matching recipe_id when there is one: the form then "
    "takes that recipe's proven settings (with no accepted recipe yet, keep the built-in defaults and say so); follow "
    "skills/minimax-video-production for the prompt and say that a new source still needs its own MP4 check. In «Может действовать» fill the form yourself with "
    "studio_apply_draft; otherwise propose composer_draft.")

SWAP_WORKFLOW = (
    "Parse the user's brief as: franchise, source video or from-scratch scene, performer-to-hero pairs, "
    "hero reference photos, environment reference, items that must stay, and requested output. The user may "
    "write this in ordinary Russian and attach files; resolve the assets and compose the final technical H3 "
    "prompt in English yourself. Verify the selected franchise's bible and each asset before drafting; never "
    "borrow identities, costumes or locations from another franchise. An environment picture is a target set "
    "reference, not evidence that source-footage furniture can be replaced by the standard swap route. "
    "When the user attaches a video and asks to replace its performers with franchise heroes, treat this as a "
    "character swap in the original footage unless they explicitly ask for the franchise's own environment. "
    "For a cartoon reference that the user wants adapted into their franchise, always treat the source as a "
    "performance, camera and timing guide: replace every source hero and the entire set with verified heroes "
    "and locations of that one franchise. Use the world-swap route (world_swap_auto): "
    "the source video has role 'Движение и камера', every source shot has its own franchise 'Кадр сцены' "
    "at the corresponding guide-frame time, hero face and body photos are bound from the franchise passport, "
    "and mode animation3d uses style3d. The source is a motion guide, so inspect the entire result for drift. "
    "A single environment photo is insufficient. Never use Character Swap, v1.5 or reskin for a request "
    "to change both performers and the room. Only our own GPU renders video. "
    "source audio by default unless the user asks for another audio plan, and explain that visual fidelity is checked on the whole rendered MP4, "
    "not guaranteed by the prompt. If a required hero or set reference is missing, prepare the missing "
    "reference first and leave generation unqueued. "
    "If the location changes, preserve the requested full performance, shot order and camera; source audio "
    "may remain when the user has no specific sound change. "
    "For a world change, use the world-swap route (world_swap_auto) and queue only part 1/N first; inspect its full MP4 "
    "before preparing or queuing later parts. For a literal cast-only replacement in the source room, "
    "video_swap_auto may map actions, but its Character Swap route does not change the room. It queues only "
    "the first part for visual QC; do not submit the remaining parts until that part is accepted. "
    "Write prompts by hand only to redo a "
    "rejected part. To plan without queuing, start with video_swap_plan (the source asset, the franchise, one pair per hero: who in "
    "the video becomes whom, audio=source unless the user asked for the heroes' own voices — then hero_voices and "
    "say it is experimental): it splits the WHOLE source on its cuts into parts, saves each part, picks each hero's "
    "«Персонаж целиком» and fills every part's form with the built-in swap settings; if it reports problems (a hero without "
    "a full-body photo), tell the user exactly that. Every part prompt spells out a "
    "[Shot N] map per source cut — who does what, with which prop, which expression, shot size — written as what is in "
    "the frame, not as bans (Studio refuses more than 14 no/never/not and more than 5000 compiled characters). After each part, check its frames for the source room's furniture, a second (source) animal in wide shots "
    "and source fur in paw inserts; re-render a reject with the fix written into that shot (at most twice), "
    "or cut an unfixable short insert with glue tail_frames. "
    "Use asset_frames on the whole video and on every part; "
    "write a timecoded map of every appearance, including late close-ups, paws/hands and reflections. Never take "
    "only the opening of a long video and present it as the full result. For clips over 10 seconds, cover all "
    "performer shots with sequential parts of at most 10 s (video_swap_plan cuts them; a 15 s part renders ~2.2x "
    "longer) or a documented montage in source order; label the first render part 1/N and keep "
    "keep_source_audio=true: the original track goes onto the result and Studio does not feed it into H3, which "
    "only cost render time. Never use cache_mode fast (it degrades the picture). "
    "Resolve the named franchise with franchise_list, profile and "
    "character entities; for literal cast-only GPU replacement use one verified 'Персонаж целиком' Studio "
    "photo per hero, with its own face and full costume and empty hands. Use role 'Исходное видео', explicit "
    "performer pairs, mode replace and Character Swap only when the original room should remain: BF16, "
    "24 steps, safe cache, max image "
    "reference, 0.8 megapixels unless the user asks for another size, and source audio. Write the six H3 sections; [Shot 1] has no time, later shots "
    "open with the official «[Shot 2] At 00:03.000, …»; say who appears where and does what. "
    "Require one visible source performer for each replacement hero. If a requested hero has no source "
    "performer, stop the swap draft for that hero and propose a separately staged new shot; do not add an "
    "unmapped hero to the source-video replacement. Show the full draft and mapping; "
    "in act mode apply the complete form. Queue only when the user requested generation; verify the accepted "
    "job receipt and never duplicate an active or completed job. After completion inspect the whole MP4 before "
    "calling it ready: duration, source audio, every shot, distinct faces and costumes, actions, and source "
    "logos or captions. Name defects with timestamps; do not rerun automatically. When every part is accepted, "
    "glue them with storyboard_concat in order with the plan's glue_trim_frames and glue_tail_frames (each part runs "
    "past its kept range part.keep; describe that run-on in its prompt too) and check the full film's length "
    "against the source. ")

SYSTEM = (
    "You are the producer-director assistant inside H3 Studio, talking with the user in Russian. "
    "Work through the h3-studio tools: franchises and their bibles, franchise production profiles "
    "(franchise_profile, franchise_resolve_shots), the asset library (view_image to look at pictures), "
    "shot_lint / franchise_shot_lint, edit sheets (edl_*), keyframes and the render queue. "
    "Follow AGENTS.md in this repository: no storyboard, keyframes or render of an episode before the user "
    "approved that story in docs/DECISIONS.md; renders spend GPU time, queue one only when the user asks for it "
    "in this chat. All franchise videos: English speech and text, vertical 9:16. Be concise and say what you did. "
    "When the message carries the «Создать видео» screen, the user is building a clip there: deliver the result "
    "with composer_draft (full English MiniMax H3 prompt per skills/minimax-video-production, using h3-prompt-writing "
    "for exact IR syntax, reference files with roles "
    "and owners resolved from the franchise profile, duration, 9:16) instead of only describing it; the user applies "
    "it to the form and starts the generation. "
    "When the user asks for a scene, an episode part or several shots, build the whole scene with scene_draft "
    "for the story canvas. " + STUDIO_MANNER + " " + H3_READING + SWAP_WORKFLOW + " " + SCENE_CRAFT + " " + EDIT_CRAFT)
CONTEXT_LIMIT = 12_000
MAX_ATTACHMENTS = 6
STUDIO_URL = os.environ.get("H3_STUDIO_SELF_URL", "http://127.0.0.1:18187")
READ_NOTE = (" Mode: «Только смотреть» — Studio write tools (canvas, renders, passports) are disabled. scene_draft, "
             "composer_draft and the lints are proposals, not writes: call them in this mode and fix what they report. "
             "When a write is needed, say exactly what you would do so the user can switch the chat to «Может действовать».")
ACT_NOTE = (" Mode: «Может действовать» — the h3-studio write tools are enabled. Your repository sandbox is read-only on "
            "purpose: Studio changes go through the h3-studio tools, so never tell the user the chat is read-only. "
            "When the user pastes a Vast server card and asks for H3 deployment, call compute_instances, match the exact "
            "instance ID and SSH endpoint, then call compute_deploy once. The Mac backend reads credentials from Keychain "
            "and runs the installer; never ask for, read, or print keys. Follow progress with compute_operation and report "
            "its real state. Do not start GPU rendering or destroy older instances as part of deployment.")


def write_tools() -> set[str]:
    spec = importlib.util.spec_from_file_location("h3_studio_mcp", MCP_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.WRITES)


class NewChat(BaseModel):
    provider: str = Field(pattern="^(claude|codex)$")
    model: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9._:\-\[\]]*$")
    effort: str = Field(default="", pattern=EFFORT_PATTERN)
    mode: str = Field(default="read", pattern="^(read|act)$")
    title: str = Field(default="", max_length=120)
    # One chat per Studio scene: the agent works only in that scene and its franchise (agent-dock.mjs).
    scene_id: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9_-]*$")
    franchise_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9-]*$")


SCENE_NOTE = (" This chat belongs to one Studio scene (H3_SCENE_ID): studio_board, studio_apply_scene, studio_apply_draft "
              "and studio_build_scene act in that scene only, whatever scene the user is looking at; never use "
              "studio_select_scene or studio_new_scene to work in another scene — every scene has its own chat.")


def account_of(provider: str) -> str:
    from .agent_accounts import active
    return active(provider)


def with_history(chat: dict, text: str, limit: int = 8) -> str:
    """A chat moved to another subscription starts a new CLI session: give it the conversation so far."""
    lines = [f"{'Пользователь' if m.get('role') == 'user' else 'Ассистент'}: {clip(m.get('text', ''), 800)}" +
             ''.join(f" [Studio asset_id={a['asset_id']}]" for a in m.get('attachments', []))
             for m in chat.get("messages", [])[-limit - 1:-1] if m.get("text") or m.get('attachments')]
    if not lines:
        return text
    return "[Начало этого чата (продолжение с другого аккаунта)]\n" + "\n".join(lines) + "\n\n[Новое сообщение]\n" + text


class ChatPatch(BaseModel):
    model: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9._:\-\[\]]*$")
    effort: str | None = Field(default=None, pattern=EFFORT_PATTERN)
    mode: str | None = Field(default=None, pattern="^(read|act)$")
    title: str | None = Field(default=None, max_length=120)


class Message(BaseModel):
    interaction: str = Field(default="agent", pattern="^(agent|chat|image)$")
    text: str = Field(min_length=1, max_length=50_000)
    context: dict | None = None  # what the page shows (agent-dock.mjs: the «Создать видео» form)
    # The user's photos for this message (already in the library): a look to match, a hero, a place.
    attachments: list[str] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)


HOW_TO_LOOK = {"image": "фото — view_image kind=asset, если оно не приложено к запросу",
               "video": "видео — asset_frames (лист кадров, длительность, звук, склейки)",
               "audio": "звук — по имени и длительности, как голос, музыку или атмосферу"}


def with_attachments(text: str, attachments: list[dict]) -> str:
    """The agent is told which library files came with the message and how to look at each kind."""
    if not attachments:
        return text
    lines = "\n".join(f"- {HOW_TO_LOOK[item.get('kind', 'image')].split(' — ')[0]} asset_id {item['asset_id']} "
                      f"«{item['name']}»" for item in attachments)
    kinds = "; ".join(HOW_TO_LOOK[kind] for kind in dict.fromkeys(item.get("kind", "image") for item in attachments))
    return f"{text}\n\n[Файлы пользователя к этому сообщению — посмотри каждый: {kinds}]\n{lines}"


def with_context(text: str, context: dict | None) -> str:
    """The agent sees the screen the user is looking at; the chat keeps only the user's words."""
    if not context:
        return text
    return f"[Экран Studio сейчас]\n{clip(context, CONTEXT_LIMIT)}\n\n[Сообщение пользователя]\n{text}"


def draft_of(name: str, arguments) -> dict:
    """composer_draft arguments in full (not clipped): the page renders and applies them."""
    name = name.removeprefix("mcp__h3-studio__")
    if name not in {"composer_draft", "scene_draft"}:
        return {}
    if isinstance(arguments, str):  # Codex may send the arguments as a JSON string
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return {}
    return {"draft": arguments} if isinstance(arguments, dict) else {}


def clip(value, limit: int = SHORT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def claude_command(exe: str, chat: dict, text: str, studio_url: str, writes: set[str]) -> list[str]:
    env = {"H3_STUDIO_URL": studio_url, "H3_CHAT_ID": chat["id"],
           **({"H3_SCENE_ID": chat["scene_id"]} if chat.get("scene_id") else {}),
           **({"H3_RUN_ID": chat["run_id"]} if chat.get("run_id") else {}),
           **({"H3_FRANCHISE_ID": chat["franchise_id"]} if chat.get("franchise_id") else {})}
    mcp = {"mcpServers": {"h3-studio": {"command": PYTHON, "args": [str(MCP_SCRIPT)], "env": env}}}
    cmd = [exe, "-p", text, "--output-format", "stream-json", "--verbose",
           "--mcp-config", json.dumps(mcp), "--strict-mcp-config", "--tools", "Read,Glob,Grep",
           "--allowedTools", "Read", "Glob", "Grep", "mcp__h3-studio", "--permission-mode", "dontAsk",
           "--append-system-prompt", SYSTEM + (ACT_NOTE if chat["mode"] == "act" else READ_NOTE)
           + (SCENE_NOTE if chat.get("scene_id") else "")]
    if chat["mode"] != "act":
        cmd += ["--disallowedTools", *sorted(f"mcp__h3-studio__{name}" for name in writes)]
    cmd += ["--model", chat.get("model") or CLAUDE_DEFAULT]
    if chat.get("effort") in {"low", "medium", "high"}:
        cmd += ["--effort", chat["effort"]]
    if chat.get("session_id"):
        cmd += ["--resume", chat["session_id"]]
    return cmd


def codex_command(exe: str, chat: dict, text: str, studio_url: str = STUDIO_URL, images: list[str] = ()) -> list[str]:
    cmd = [exe, "exec", "--json", "--ignore-user-config", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
           "-C", str(REPO)]
    cmd += ["-m", chat.get("model") or CODEX_DEFAULT]
    cmd += ["-c", f'model_reasoning_effort="{chat.get("effort") or CODEX_DEFAULT_EFFORT}"']
    # Every tool call resends the whole context: drop tools Studio never needs (their schemas and the agent's
    # attempts to drive Chrome cost ~60k chars per task) and compact the session before it grows past 120k.
    for feature in CODEX_OFF_FEATURES:
        cmd += ["--disable", feature]
    cmd += ["-c", f"model_auto_compact_token_limit={CODEX_COMPACT_TOKENS}"]
    # Studio chats use their own MCP server, independent of personal browser plugins.
    cmd += ["-c", f'mcp_servers.h3-studio.command={json.dumps(PYTHON)}']
    cmd += ["-c", f'mcp_servers.h3-studio.args={json.dumps([str(MCP_SCRIPT)])}']
    approval = "auto" if chat["mode"] == "act" else "writes"
    cmd += ["-c", f'mcp_servers.h3-studio.default_tools_approval_mode="{approval}"']
    cmd += ["-c", f'mcp_servers.h3-studio.env.H3_STUDIO_URL="{studio_url}"']
    cmd += ["-c", f'mcp_servers.h3-studio.env.H3_CHAT_ID="{chat["id"]}"']  # story_approval_record checks the quote
    if chat.get("run_id"):  # the task's GPU gate is checked by the MCP server (scripts/h3_studio_mcp.py)
        cmd += ["-c", f'mcp_servers.h3-studio.env.H3_RUN_ID="{chat["run_id"]}"']
    if chat.get("franchise_id"):
        cmd += ["-c", f'mcp_servers.h3-studio.env.H3_FRANCHISE_ID="{chat["franchise_id"]}"']
    if chat.get("scene_id"):
        cmd += ["-c", f'mcp_servers.h3-studio.env.H3_SCENE_ID="{chat["scene_id"]}"']
    note = (ACT_NOTE if chat["mode"] == "act" else READ_NOTE) + (SCENE_NOTE if chat.get("scene_id") else "")
    workflow = (" Studio workflow for this turn: use h3-studio MCP tools for live Studio data. "
                "Call studio_playbook to select an existing production method when the task spans multiple stages. "
                "Read the named franchise profile and relevant entities; resolve shot references before drafting. "
                "If the user asks to configure the franchise, save a verified profile with franchise_profile_update. "
                "For a requested clip use composer_draft; for a requested scene use scene_draft, then fix its lint findings. "
                "These draft tools show review cards and do not start GPU work. "
                "The read-only repository sandbox does not disable Studio MCP writes in act mode. "
                "Never claim this chat is read-only when its mode is act. For an attached source video and a hero "
                "replacement request, follow the character-swap workflow in the repository skill and inspect the "
                "full video with asset_frames before drafting or queuing. "
                "If story approval is required for an episode, present the proposal and ask only for that approval; "
                "do not ask the user to switch modes when mode is act.")
    # The user's photos go to the model itself; «--image=» keeps the multi-value flag off the prompt.
    attach = [f"--image={path}" for path in images]
    if chat.get("session_id"):
        # codex exec resume carries no system prompt: every turn restates the mode, the rest is in the session.
        return cmd + ["resume", *attach, chat["session_id"], f"[Studio:{note}{workflow}]\n\n{text}"]
    return cmd + [*attach, f"{SYSTEM}{note}{workflow}\n\n---\n\n{text}"]


def claude_events(line: dict) -> list[dict]:
    kind = line.get("type")
    if kind == "system" and line.get("session_id"):
        return [{"kind": "session", "id": line["session_id"]}]
    if kind == "assistant":
        out = []
        for part in line.get("message", {}).get("content", []):
            if part.get("type") == "text" and part.get("text", "").strip():
                out.append({"kind": "text", "text": part["text"]})
            elif part.get("type") == "tool_use":
                name = part.get("name", "").removeprefix("mcp__h3-studio__")
                out.append({"kind": "tool", "id": part.get("id", ""), "name": name,
                            "input": clip(part.get("input", {}), 400), "status": "started",
                            **draft_of(name, part.get("input"))})
        return out
    if kind == "user":
        out = []
        for part in line.get("message", {}).get("content", []):
            if isinstance(part, dict) and part.get("type") == "tool_result":
                content = part.get("content")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "[картинка]") if isinstance(c, dict) else str(c) for c in content)
                out.append({"kind": "tool", "id": part.get("tool_use_id", ""),
                            "status": "error" if part.get("is_error") else "done", "output": clip(content or "")})
        return out
    if kind == "result":
        out = [{"kind": "session", "id": line["session_id"]}] if line.get("session_id") else []
        if line.get("is_error"):
            message = str(line.get("result") or line.get("subtype") or "ошибка")
            if any(word in message.lower() for word in ("401", "revoked", "authenticat", "log in", "login")):
                message += " — выполни в терминале: claude auth login"
            out.append({"kind": "error", "text": message})
        return out
    return []


def codex_events(line: dict) -> list[dict]:
    kind = line.get("type")
    if kind == "thread.started":
        return [{"kind": "session", "id": line.get("thread_id", "")}]
    if kind in {"turn.failed", "error"}:
        error = line.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        return [{"kind": "error", "text": message or line.get("message", "ошибка Codex")}]
    if kind not in {"item.started", "item.completed"}:
        return []
    item = line.get("item", {})
    done = kind == "item.completed"
    if item.get("type") == "agent_message" and done:
        return [{"kind": "text", "text": item.get("text", "")}]
    if item.get("type") == "mcp_tool_call":
        event = {"kind": "tool", "id": item.get("id", ""), "name": item.get("tool", ""),
                 "status": ("error" if item.get("error") or item.get("status") == "failed" else "done") if done else "started"}
        if not done:
            event["input"] = clip(item.get("arguments", {}), 400)
            event.update(draft_of(event["name"], item.get("arguments")))
        else:
            event["output"] = clip(item.get("error") or item.get("result") or "")
        return [event]
    if item.get("type") == "command_execution":
        return [{"kind": "tool", "id": item.get("id", ""), "name": "shell", "input": clip(item.get("command", ""), 300),
                 "status": "started" if not done else ("done" if item.get("exit_code") == 0 else "error"),
                 **({"output": clip(item.get("aggregated_output", ""))} if done else {})}]
    if item.get("type") == "error":
        return [{"kind": "error", "text": item.get("message", "")}]
    return []


def turn_usage(provider: str, line: dict) -> dict | None:
    """CLI reported token counts for one completed turn; missing data stays unknown."""
    if provider == "claude" and line.get("type") == "result":
        raw = line.get("usage")
    elif provider == "codex" and line.get("type") == "turn.completed":
        raw = (line.get("usage") or {}).get("last_token_usage") or line.get("usage")
    else:
        return None
    if not isinstance(raw, dict):
        return None
    fields = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens")
    counts = {key: raw[key] for key in fields if type(raw.get(key)) is int and raw[key] >= 0}
    return counts or None


def codex_effort() -> str:
    """model_reasoning_effort from ~/.codex/config.toml: what «По умолчанию» means for Codex."""
    try:
        for line in CODEX_CONFIG.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "model_reasoning_effort":
                return value.strip().strip('"')
    except OSError:
        pass
    return ""


def codex_models() -> tuple[list[tuple[str, str]], str]:
    models, default = [], ""
    try:
        data = json.loads(CODEX_MODELS.read_text(encoding="utf-8"))
        items = data.get("models", data) if isinstance(data, dict) else data
        models = [(m["slug"], m.get("display_name") or m["slug"]) for m in items
                  if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        for line in CODEX_CONFIG.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("model") and "=" in line and line.split("=", 1)[0].strip() == "model":
                default = line.split("=", 1)[1].strip().strip('"')
                break
    except OSError:
        pass
    return models, default


class Run:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.changed = asyncio.Event()
        self.done = False
        self.process: asyncio.subprocess.Process | None = None
        self.stopped = False

    def push(self, event: dict) -> None:
        self.events.append(event)
        self.changed.set()


def merge_tool(events: list[dict], event: dict) -> None:
    """Started/finished halves of one tool call become one entry."""
    for existing in reversed(events):
        if existing.get("kind") == "tool" and existing.get("id") and existing["id"] == event.get("id"):
            existing.update({k: v for k, v in event.items() if v not in ("", None)})
            return
    events.append(dict(event))


def make_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/agent-chat")
    root = store.root / "agent_chats"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    runs: dict[str, Run] = {}
    on_done: list = []
    slots = asyncio.Semaphore(MAX_RUNS)
    scene_chat_lock = asyncio.Lock()
    status_cache: dict[str, tuple[float, dict]] = {}

    def path(chat_id: str) -> Path:
        if len(chat_id) != 32 or not all(c in "0123456789abcdef" for c in chat_id):
            raise HTTPException(404, "Чат не найден")
        return root / f"{chat_id}.json"

    def load(chat_id: str) -> dict:
        file = path(chat_id)
        if not file.is_file():
            raise HTTPException(404, "Чат не найден")
        return json.loads(file.read_text(encoding="utf-8"))

    def save(chat: dict) -> None:
        chat["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        temp = root / f".{chat['id']}.tmp"
        temp.write_text(json.dumps(chat, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, root / f"{chat['id']}.json")

    def public(chat: dict) -> dict:
        return {**chat, "running": chat["id"] in runs}

    async def login(provider: str) -> dict:
        cached = status_cache.get(provider)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        exe = executable(provider)
        info = {"installed": bool(exe), "logged_in": False, "detail": ""}
        if exe:
            args = [exe, "auth", "status"] if provider == "claude" else [exe, "login", "status"]
            process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                           stderr=asyncio.subprocess.STDOUT, env=clean_env())
            try:
                out, _ = await asyncio.wait_for(process.communicate(), 20)
            except TimeoutError:
                process.kill()
                out = b""
            text = out.decode("utf-8", "replace")
            if provider == "claude":
                try:
                    info["logged_in"] = bool(json.loads(text).get("loggedIn"))
                except ValueError:
                    info["logged_in"] = False
                info["detail"] = "" if info["logged_in"] else "Войди: claude auth login"
            else:
                info["logged_in"] = process.returncode == 0 and "Logged in" in text
                info["detail"] = text.strip()[:120] if info["logged_in"] else "Войди: codex login"
        status_cache[provider] = (time.monotonic(), info)
        return info

    @router.get("/providers")
    async def providers() -> dict:
        claude, codex = await asyncio.gather(login("claude"), login("codex"))
        models, default = codex_models()
        return {"claude": {**claude, "label": "Claude Code", "models": CLAUDE_MODELS, "default": CLAUDE_DEFAULT,
                           "efforts": EFFORTS["claude"], "default_effort": ""},
                "codex": {**codex, "label": "Codex", "models": models, "default": CODEX_DEFAULT, "efforts": EFFORTS["codex"],
                          "default_effort": CODEX_DEFAULT_EFFORT}}

    @router.get("/chats")
    async def list_chats(scene_id: str = "") -> list[dict]:
        chats = []
        for file in root.glob("*.json"):
            try:
                chat = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if scene_id and chat.get("scene_id") != scene_id:
                continue
            chats.append({k: chat.get(k) for k in ("id", "title", "provider", "model", "effort", "mode", "updated_at",
                                                    "run_id", "parent_run", "role", "scene_id", "franchise_id")} |
                         {"running": chat.get("id") in runs, "messages": len(chat.get("messages", []))})
        return sorted(chats, key=lambda c: ((c.get("updated_at") or ""), (root / f"{c['id']}.json").stat().st_mtime_ns), reverse=True)

    def new_chat(provider: str, model: str = "", mode: str = "read", title: str = "", effort: str = "", **extra) -> dict:
        chat = {"id": uuid.uuid4().hex, "title": title or "Новый чат", "provider": provider, "model": model, "effort": effort,
                "mode": mode, "session_id": "", "messages": [], "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), **extra}
        save(chat)
        return chat

    @router.post("/chats", status_code=201)
    async def create_chat(payload: NewChat) -> dict:
        extra = {key: value for key, value in (("scene_id", payload.scene_id), ("franchise_id", payload.franchise_id)) if value}
        return public(new_chat(payload.provider, payload.model, payload.mode, payload.title, payload.effort, **extra))

    @router.post("/scene-chats/{scene_id}")
    async def get_or_create_scene_chat(scene_id: str, payload: NewChat) -> dict:
        """Open a scene's persistent chat once, even when several tabs discover it together."""
        if not scene_id or len(scene_id) > 80 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in scene_id):
            raise HTTPException(422, "Некорректный ID сцены")
        async with scene_chat_lock:
            existing = await list_chats(scene_id)
            if existing:
                chat = load(existing[0]["id"])
                if payload.franchise_id and chat.get("franchise_id") and chat["franchise_id"] != payload.franchise_id:
                    raise HTTPException(409, "Чат сцены привязан к другой франшизе")
                return public(chat)
            return public(new_chat(payload.provider, payload.model, payload.mode, payload.title, payload.effort,
                                   scene_id=scene_id, **({"franchise_id": payload.franchise_id} if payload.franchise_id else {})))

    @router.get("/chats/{chat_id}")
    async def get_chat(chat_id: str) -> dict:
        chat = load(chat_id)
        run = runs.get(chat_id)
        if run:
            chat["live"] = run.events
        return public(chat)

    @router.patch("/chats/{chat_id}")
    async def patch_chat(chat_id: str, payload: ChatPatch) -> dict:
        chat = load(chat_id)
        if chat_id in runs:
            raise HTTPException(409, "Агент ещё отвечает — дождись или нажми «Стоп»")
        for key, value in payload.model_dump(exclude_none=True).items():
            chat[key] = value
        save(chat)
        return public(chat)

    @router.delete("/chats/{chat_id}", status_code=204, response_class=Response)
    async def delete_chat(chat_id: str) -> Response:
        chat = load(chat_id)
        if chat_id in runs or chat.get("subscription_pending"):
            raise HTTPException(409, "Сначала дождись завершения сообщения")
        path(chat_id).unlink(missing_ok=True)
        return Response(status_code=204)

    @router.delete("/chats")
    async def delete_all_chats() -> dict:
        """Clear the chat list; chats whose agent is still answering stay until it is stopped."""
        deleted = kept = 0
        for file in root.glob("*.json"):
            if file.stem in runs or json.loads(file.read_text()).get("subscription_pending"):
                kept += 1
                continue
            file.unlink(missing_ok=True)
            deleted += 1
        return {"deleted": deleted, "kept": kept}

    async def execute(chat: dict, text: str, run: Run, studio_url: str, images: list[str] = ()) -> None:
        reply = {"role": "assistant", "text": "", "events": [], "provider": chat["provider"], "model": chat.get("model", "")}
        started = time.monotonic()
        try:
            exe = executable(chat["provider"])
            if not exe:
                raise RuntimeError("CLI не найден: установи Claude Code или Codex")
            account = account_of(chat["provider"])
            if chat.get("session_id") and chat.get("account", "main") != account:
                # The CLI session lives in the previous subscription's folder: start anew with the chat so far.
                chat["session_id"] = ""
                text = with_history(chat, text)
            if chat.pop("subscription_context_changed", False):
                text = with_history(chat, text)
            chat["account"] = account
            if chat["provider"] == "claude":
                cmd, parse = claude_command(exe, chat, text, studio_url, write_tools()), claude_events
            else:
                cmd, parse = codex_command(exe, chat, text, studio_url, images), codex_events
            async with slots:
                run.process = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(REPO), env=clean_env(), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True,
                    limit=16 * 1024 * 1024)

                async def read_stdout():
                    async for raw in run.process.stdout:
                        try:
                            line = json.loads(raw)
                        except ValueError:
                            continue
                        usage = turn_usage(chat["provider"], line) if isinstance(line, dict) else None
                        if usage:
                            reply["usage"] = usage
                        for event in parse(line) if isinstance(line, dict) else []:
                            if event["kind"] == "session":
                                if event["id"]:
                                    chat["session_id"] = event["id"]
                                continue
                            if event["kind"] == "text":
                                reply["text"] = (reply["text"] + "\n\n" + event["text"]).strip()
                            elif event["kind"] == "tool":
                                merge_tool(reply["events"], event)
                            elif event["kind"] == "error":
                                if reply.get("error") == event["text"]:
                                    continue  # Codex reports a failed turn twice (error + turn.failed)
                                reply["error"] = event["text"]
                            run.push(event)

                tail = bytearray()

                async def read_stderr():
                    # Read stderr while the agent runs: Codex logs a lot (other MCP servers) and a full
                    # pipe would block it forever. Only the tail is kept for the error message.
                    while chunk := await run.process.stderr.read(65536):
                        tail.extend(chunk)
                        del tail[:-8192]

                await asyncio.wait_for(asyncio.gather(read_stdout(), read_stderr(), run.process.wait()), RUN_TIMEOUT_S)
                stderr = tail.decode("utf-8", "replace")
                if run.stopped:
                    reply["error"] = "Остановлено"
                    run.push({"kind": "error", "text": reply["error"]})
                elif run.process.returncode and not reply["text"] and "error" not in reply:
                    lines = [line for line in stderr.strip().splitlines()
                             if line.strip() and "Reading additional input" not in line]
                    reply["error"] = clip(lines[-1] if lines else f"CLI завершился с кодом {run.process.returncode}", 600)
                    run.push({"kind": "error", "text": reply["error"]})
        except TimeoutError:
            reply["error"] = "Агент думал дольше 30 минут — остановлен"
            run.push({"kind": "error", "text": reply["error"]})
            stop(run)
        except Exception as exc:  # noqa: BLE001 — every failure must reach the chat
            reply["error"] = str(exc)
            run.push({"kind": "error", "text": reply["error"]})
        finally:
            if run.process and run.process.returncode is None:
                stop(run)
            if reply.get("error") and reply["text"].strip() and reply["error"].startswith(reply["text"].strip()[:80]):
                reply["text"] = ""  # Claude repeats an API error as its answer text
            if chat["provider"] == "claude" and "claude auth login" in reply.get("error", ""):
                status_cache["claude"] = (time.monotonic(), {"installed": True, "logged_in": False,
                                                             "detail": "Вход устарел: claude auth login"})
            reply["seconds"] = round(time.monotonic() - started, 1)
            fresh = load(chat["id"]) if path(chat["id"]).is_file() else chat
            fresh["session_id"] = chat.get("session_id", "")
            fresh["account"] = chat.get("account", "main")
            fresh.pop("subscription_context_changed", None)
            fresh["messages"].append(reply)
            save(fresh)
            run.done = True
            run.push({"kind": "done", "seconds": reply["seconds"]})
            runs.pop(chat["id"], None)
            for hook in on_done:  # orchestrator: an agent turn ended (local_studio/orchestrator.py)
                try:
                    hook(fresh, reply)
                except Exception:  # noqa: BLE001 — a hook must not break the chat
                    logging.getLogger("h3.studio").exception("agent chat hook failed")

    def stop(run: Run) -> None:
        if run.process and run.process.returncode is None:
            try:
                os.killpg(run.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    async def stream(run: Run, after: int = 0):
        index = after
        while True:
            while index < len(run.events):
                yield json.dumps(run.events[index], ensure_ascii=False) + "\n"
                index += 1
            if run.done:
                return
            run.changed.clear()
            try:
                await asyncio.wait_for(run.changed.wait(), 15)
            except TimeoutError:
                yield json.dumps({"kind": "ping"}) + "\n"

    def photos(asset_ids: list[str]) -> list[dict]:
        """Attached photos, videos and sounds must be in the Studio library; the path stays on the server."""
        out = []
        for asset_id in dict.fromkeys(asset_ids):
            asset = store.get_asset(asset_id)
            path = Path(asset["path"]) if asset and asset.get("path") else None
            if not asset or asset.get("kind") not in HOW_TO_LOOK or not path or not path.is_file():
                raise HTTPException(422, f"Файла {asset_id[:8]} нет в библиотеке Studio")
            out.append({"asset_id": asset_id, "kind": asset["kind"], "name": asset.get("original_name") or "файл",
                        "path": str(path)})
        return out

    async def start(chat_id: str, text: str, context: dict | None = None, studio_url: str = "",
                    author: str = "user", attachments: list[str] = ()) -> Run:
        """One agent turn in the background; `author` "studio" marks a message the Studio sent itself."""
        chat = load(chat_id)
        if chat_id in runs or chat.get("subscription_pending"):
            raise HTTPException(409, "В чате ещё выполняется предыдущее сообщение")
        attached = photos(list(attachments))
        if chat["provider"] == "claude" and not (await login("claude"))["logged_in"]:
            status_cache.pop("claude", None)
            if not (await login("claude"))["logged_in"]:
                raise HTTPException(409, "Claude Code не вошёл в подписку. В терминале: claude auth login")
        chat["messages"].append({"role": "user", "text": text, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 **({"from": author} if author != "user" else {}),
                                 **({"attachments": [{k: item[k] for k in ("asset_id", "kind", "name")} for item in attached]}
                                    if attached else {})})
        if chat["title"] == "Новый чат":
            chat["title"] = text.strip().splitlines()[0][:60]
        save(chat)
        run = Run()
        runs[chat_id] = run
        asyncio.create_task(execute(chat, with_context(with_attachments(text, attached), context), run,
                                    studio_url or STUDIO_URL, [item["path"] for item in attached if item["kind"] == "image"]))
        return run

    @router.post("/chats/{chat_id}/messages")
    async def send(chat_id: str, payload: Message, request: Request) -> StreamingResponse:
        if payload.interaction != "agent":
            raise HTTPException(409, "Этот маршрут запускает только агента. Для подписочного чата используйте subscription-bridge.")
        run = await start(chat_id, payload.text, payload.context, f"http://127.0.0.1:{request.url.port or 18187}",
                          attachments=payload.attachments)
        return StreamingResponse(stream(run), media_type="application/x-ndjson")

    @router.get("/chats/{chat_id}/stream")
    async def reconnect(chat_id: str, after: int = 0) -> StreamingResponse:
        load(chat_id)
        run = runs.get(chat_id)
        if not run:
            raise HTTPException(404, "Агент сейчас не отвечает")
        return StreamingResponse(stream(run, max(after, 0)), media_type="application/x-ndjson")

    @router.post("/chats/{chat_id}/stop")
    async def stop_chat(chat_id: str) -> dict:
        load(chat_id)
        run = runs.get(chat_id)
        if run:
            run.stopped = True
            stop(run)
        return {"stopped": bool(run)}

    def halt(chat_id: str) -> bool:
        run = runs.get(chat_id)
        if run:
            run.stopped = True
            stop(run)
        return bool(run)

    # For the orchestrator (local_studio/orchestrator.py): run agent turns and hear when they end.
    router.hub = SimpleNamespace(start=start, load=load, save=save, new_chat=new_chat, runs=runs, on_done=on_done,
                                 stop=halt)
    return router
