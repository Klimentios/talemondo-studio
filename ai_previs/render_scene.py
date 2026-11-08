"""Render the saved Blender scene and package its motion reference for MiniMax."""

from __future__ import annotations

import shutil
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
PROMPT = """Reference video 1 is a Blender 3D previs used exclusively as the spatial, motion, blocking, timing and camera reference.
Preserve the camera trajectory, camera orientation, shot composition, character trajectory, character orientation, action timing, spatial relationships and object permanence shown in the reference video.
Transform the primitive 3D proxy environment into a photorealistic cinematic environment.
Transform the proxy block into the requested realistic character.
Do not reproduce the primitive gray Blender appearance.
Do not introduce camera cuts.
Do not change screen direction.
Do not teleport the character or objects.
Maintain the reference video's continuous camera movement and overall choreography.
"""


def build_minimax_package():
    source = OUTPUT / "previs.mp4"
    if not source.exists() or source.stat().st_size == 0:
        raise RuntimeError(f"Rendered video is missing or empty: {source}")
    folder = OUTPUT / "minimax"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, folder / "reference_video.mp4")
    (folder / "prompt.txt").write_text(PROMPT, encoding="utf-8")
    (folder / "README.txt").write_text(
        "reference_video.mp4 is the Blender motion and spatial reference for MiniMax H3.\n"
        "Use prompt.txt as the scene conversion guidance and add the desired character, "
        "environment, and appearance details before generation.\n"
        "camera.json, objects.json and scene.json are local pipeline/debug files and are not uploaded.\n",
        encoding="utf-8",
    )


def render_all():
    scene = bpy.context.scene
    if scene.camera is None or scene.camera.name != "CAM_Main":
        raise RuntimeError("CAM_Main must be the active scene camera")
    if scene.render.image_settings.file_format != "FFMPEG":
        raise RuntimeError("Scene must be configured for FFmpeg video rendering")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(animation=True)
    build_minimax_package()
    print("====================================")
    print("AI VIDEO PREVIS COMPLETE")
    print(f"BLEND: {OUTPUT / 'scene.blend'}")
    print(f"REFERENCE VIDEO: {OUTPUT / 'previs.mp4'}")
    print(f"CAMERA DATA: {OUTPUT / 'camera.json'}")
    print(f"OBJECT DATA: {OUTPUT / 'objects.json'}")
    print(f"SCENE DATA: {OUTPUT / 'scene.json'}")
    print(f"MINIMAX PACKAGE: {OUTPUT / 'minimax'}")
    print("FPS: 24")
    print("FRAMES: 240")
    print("DURATION: 10 seconds")
    print("====================================")


if __name__ == "__main__":
    render_all()
