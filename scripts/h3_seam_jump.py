#!/usr/bin/env python3
"""Jump at the join of a «Продолжение»: how much bigger the change across the seam is than a normal frame step.

    python3 scripts/h3_seam_jump.py PREVIOUS.mp4 CONTINUATION.mp4 --overlap 22

The continuation's first `overlap` frames repeat the previous clip's tail (Studio trims them when it glues
the take), so the seam is previous[-1] → continuation[overlap]. Both clips are read as small grey frames
(ffmpeg, 24 fps); the jump is the mean absolute difference across the seam divided by the median difference
between neighbouring frames in the 12 frames on each side. 1.0 = invisible, ComfyUI-H3-LongTake measured
5.4–6.8 for a keyframe anchor and 1.5–1.7 for the inpaint anchor. Standard library only, no GPU.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess

WIDTH, HEIGHT, WINDOW = 96, 54, 12


def grey_frames(path: Path) -> list[bytes]:
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
                          "-vf", f"fps=24,scale={WIDTH}:{HEIGHT}:flags=area,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    size = WIDTH * HEIGHT
    return [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]


def difference(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def seam_jump(previous: Path, continuation: Path, overlap: int) -> dict:
    before, after = grey_frames(previous), grey_frames(continuation)
    if len(before) < WINDOW + 1 or len(after) < overlap + WINDOW + 1:
        raise ValueError(f"Слишком короткие клипы: {len(before)} и {len(after)} кадров при перекрытии {overlap}")
    tail, head = before[-(WINDOW + 1):], after[overlap:overlap + WINDOW + 1]
    normal = [difference(a, b) for a, b in zip(tail, tail[1:])] + [difference(a, b) for a, b in zip(head, head[1:])]
    seam = difference(before[-1], after[overlap])
    baseline = max(statistics.median(normal), 0.05)  # a still shot must not divide by ~0
    return {"overlap": overlap, "seam": round(seam, 3), "normal_median": round(baseline, 3), "jump": round(seam / baseline, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("previous", type=Path)
    parser.add_argument("continuation", type=Path)
    parser.add_argument("--overlap", type=int, default=22, help="кадров начала продолжения, повторяющих хвост (22 или 5)")
    args = parser.parse_args()
    print(json.dumps(seam_jump(args.previous, args.continuation, args.overlap), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
