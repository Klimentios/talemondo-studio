"""Blur the performer's head (face + hair) in a movement reference video; body motion stays.

Runs in the LivePortrait venv (insightface SCRFD det_10g + onnxruntime, CPU so the render keeps the GPU):
    python motion_face_blur.py IN.mp4 OUT.mp4 --models /workspace/h3/LivePortrait/pretrained_weights/insightface

Why: with a «Движение» video the model saw two faces — the hero's picture and the performer — and blended
them (2026-10-05: Anna's face lost its features; long hair leaked earlier). The approved picture-only
render had no video face to compete with. Only the head region is hidden; arms, hands and the body path,
which carry the choreography, are untouched.
"""
from __future__ import annotations

import argparse
import sys

import cv2
import numpy as np

HOLD_FRAMES = 12          # keep the last head box this long when a frame misses the face (turns, blur)
DET_SIZE = (640, 640)


def head_box(face_box, width: int, height: int) -> tuple[int, int, int, int]:
    """Face box → head box with hair: wider, and extended above and a little below."""
    x1, y1, x2, y2 = face_box
    w, h = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2
    hw = w * 1.0                       # half width: face width each side → ears and hair volume
    top, bottom = y1 - h * 0.9, y2 + h * 0.45
    return (max(0, int(cx - hw)), max(0, int(top)), min(width, int(cx + hw)), min(height, int(bottom)))


def hide_head(frame: np.ndarray, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    if x2 - x1 < 4 or y2 - y1 < 4:
        return
    region = frame[y1:y2, x1:x2]
    small = cv2.resize(region, (max(1, (x2 - x1) // 16), max(1, (y2 - y1) // 16)), interpolation=cv2.INTER_AREA)
    flat = cv2.GaussianBlur(cv2.resize(small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR), (0, 0), 6)
    mask = np.zeros(region.shape[:2], np.float32)
    cv2.ellipse(mask, ((x2 - x1) // 2, (y2 - y1) // 2), ((x2 - x1) // 2, (y2 - y1) // 2), 0, 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), max(2, (x2 - x1) / 12))[..., None]
    frame[y1:y2, x1:x2] = (flat * mask + region * (1 - mask)).astype(np.uint8)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("src")
    parser.add_argument("dst")
    parser.add_argument("--models", required=True, help="folder that contains models/buffalo_l/det_10g.onnx")
    args = parser.parse_args()
    sys.path.insert(0, "/workspace/h3/LivePortrait")
    from src.utils.dependencies.insightface.app import FaceAnalysis  # vendored insightface

    analysis = FaceAnalysis(name="buffalo_l", root=args.models, allowed_modules=["detection"],
                            providers=["CPUExecutionProvider"])
    analysis.prepare(ctx_id=-1, det_size=DET_SIZE)
    video = cv2.VideoCapture(args.src)
    fps = video.get(cv2.CAP_PROP_FPS) or 24.0
    width, height = int(video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
    writer = cv2.VideoWriter(args.dst, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    tracks: list[tuple[tuple[int, int, int, int], int]] = []   # (box, frames since seen)
    frames = hidden = 0
    while True:
        ok, frame = video.read()
        if not ok:
            break
        frames += 1
        faces = analysis.get(frame)
        boxes = [head_box(face.bbox, width, height) for face in faces if face.det_score >= 0.35]
        if boxes:
            tracks = [(box, 0) for box in boxes]
        else:
            tracks = [(box, age + 1) for box, age in tracks if age + 1 <= HOLD_FRAMES]
        for box, _ in tracks:
            hide_head(frame, box)
        hidden += bool(tracks)
        writer.write(frame)
    writer.release()
    video.release()
    print(f"frames={frames} heads_hidden={hidden}")
    return 0 if frames else 1


if __name__ == "__main__":
    sys.exit(main())
