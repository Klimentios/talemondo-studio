#!/usr/bin/env python3
"""weights.tsv for an image built offline (infra/h3_image_build.sh): what the server must fetch.

    python3 infra/h3_image_weights.py --old OLD/weights.tsv --recorded expected-weights.tsv --out weights.tsv

Inputs: the previous snapshot's inventory (path, size, sha256 — taken from a live server) and the lines
the installers recorded with H3_IMAGE_BUILD=1 (path, size|-, sha256|-, source, revision, source_path).
Output columns (tab-separated, the first three as before, see infra/h3_snapshot_spec.sh):

    path  size  sha256  source  revision  source_path

source = Hugging Face repo id, a direct https URL (revision/source_path "-") or "-" (only from a
server/R2 copy). Missing sizes/hashes are filled from the old inventory (same path) or from the
public Hugging Face API (LFS size + SHA-256; metadata only, no weight is downloaded).

Rules: ComfyUI models, the HF cache and LivePortrait weights are kept only if an installer still
fetches them (that is how removed weights such as SDPose/RT-DETR drop out); ComfyUI's own model
configs and the audio checkpoints (installed separately, untouched by the image build) stay.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

REMOVED = ("sdpose", "rt_detr", "depth_anything", "depth-anything", "controlnet", "da3_")
MANAGED = ("ComfyUI/models/", "hf-cache/", "LivePortrait/pretrained_weights/", "ComfyUI/custom_nodes/")
ALWAYS = ("ComfyUI/models/configs/", "audio/")
# Sources of entries that come from installers outside the image build (scripts/install_h3_audio_*.sh).
AUDIO_SOURCES = (
    ("audio/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B/", "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
     "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"),
    ("audio/ace-checkpoints/acestep-v15-sft/", "ACE-Step/acestep-v15-sft", "c410d249e71ea9385a7b586865e65b1473e1098d"),
    ("audio/ace-checkpoints/", "ACE-Step/Ace-Step1.5", "19671f406d603126926c1b7e2adc169acbcade22"),
)
COMFY_COMMIT = "b5cc8830279eae909a59de030af1e50761c36751"


def hf_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "h3-image-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


_trees: dict[tuple[str, str], dict[str, dict]] = {}


def hf_tree(repo: str, revision: str) -> dict[str, dict]:
    """path -> entry (size, oid, lfs) of every file of repo@revision (public API, paginated)."""
    key = (repo, revision)
    if key not in _trees:
        files: dict[str, dict] = {}
        url = (f"https://huggingface.co/api/models/{repo}/tree/{urllib.parse.quote(revision, safe='')}"
               "?recursive=true&expand=false&limit=1000")
        while url:
            request = urllib.request.Request(url, headers={"User-Agent": "h3-image-build"})
            with urllib.request.urlopen(request, timeout=60) as response:
                for entry in json.load(response):
                    if entry.get("type") == "file":
                        files[entry["path"]] = entry
                match = re.search(r'<([^>]+)>;\s*rel="next"', response.headers.get("Link", ""))
                url = match.group(1) if match else ""
        _trees[key] = files
    return _trees[key]


def read_tsv(path: str) -> list[list[str]]:
    with open(path, encoding="utf-8") as handle:
        return [line.rstrip("\n").split("\t") for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--old", required=True)
    parser.add_argument("--recorded", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    old = {row[0]: row for row in read_tsv(args.old)}
    recorded = read_tsv(args.recorded)
    rows: dict[str, list[str]] = {}
    problems: list[str] = []

    snapshots = [r for r in recorded if r[5] == "*"]
    for path, size, sha, source, revision, source_path in (r for r in recorded if r[5] != "*"):
        rows[path] = [path, size, sha, source, revision, source_path]

    # Whole HF snapshots (snapshot_download): the files the reference server had, mapped to the repo.
    for prefix, _, _, repo, revision, _ in snapshots:
        prefix = prefix.rstrip("/") + "/"
        tree = hf_tree(repo, revision)
        by_blob = {}
        for name, entry in tree.items():
            by_blob[entry.get("oid")] = name
            if entry.get("lfs"):
                by_blob[entry["lfs"].get("oid")] = name
        for path, row in old.items():
            if not path.startswith(prefix):
                continue
            blob = path.rsplit("/", 1)[1]
            name = by_blob.get(blob, "-") if "/blobs/" in path else "-"
            rows[path] = [path, row[1], row[2], repo if name != "-" else "-", revision if name != "-" else "-", name]

    for path, row in old.items():
        if path in rows:
            continue
        if path.startswith(ALWAYS):
            source, revision, source_path = "-", "-", "-"
            if path.startswith("ComfyUI/models/configs/"):
                source = (f"https://raw.githubusercontent.com/Comfy-Org/ComfyUI/{COMFY_COMMIT}/"
                          + path.removeprefix("ComfyUI/"))
            for prefix, repo, rev in AUDIO_SOURCES:
                if path.startswith(prefix) and "/.cache/" not in path:
                    source, revision, source_path = repo, rev, path.removeprefix(prefix)
                    break
            rows[path] = [path, row[1], row[2], source, revision, source_path]
        elif path.startswith(MANAGED):
            print(f"  removed (no installer fetches it): {path}", file=sys.stderr)
        else:
            problems.append(f"unknown old weight: {path}")

    # Fill size/sha256: same path in the old inventory, else the published LFS entry.
    for path, row in rows.items():
        if path in old:
            if row[1] in ("-", "") or row[1] == old[path][1]:
                row[1] = old[path][1]
            if row[2] in ("-", ""):
                row[2] = old[path][2]
        if (row[1] == "-" or row[2] == "-") and "/" in row[3] and not row[3].startswith("https://"):
            try:
                entry = hf_tree(row[3], row[4]).get(row[5])
            except Exception as exc:  # gated repo or no network: reported below
                entry = None
                print(f"  HF API {row[3]}@{row[4]}: {exc}", file=sys.stderr)
            if entry:
                lfs = entry.get("lfs") or {}
                if row[1] == "-":
                    row[1] = str(lfs.get("size") or entry.get("size"))
                if row[2] == "-" and lfs.get("oid"):
                    row[2] = lfs["oid"]
                elif row[2] != "-" and lfs.get("oid") and lfs["oid"] != row[2]:
                    problems.append(f"{path}: installer pins {row[2]}, Hugging Face has {lfs['oid']}")
        if row[1] == "-" and row[3].startswith("https://"):
            request = urllib.request.Request(row[3], method="HEAD", headers={"User-Agent": "h3-image-build"})
            with urllib.request.urlopen(request, timeout=60) as response:
                row[1] = response.headers.get("Content-Length", "-")
        if not row[1].isdigit() or not re.fullmatch(r"[0-9a-f]{64}", row[2]):
            problems.append(f"{path}: size {row[1]!r} / sha256 {row[2]!r} unknown")
        if any(word in path.rsplit("/", 1)[-1].lower() for word in REMOVED):
            problems.append(f"{path}: removed component still listed")

    if problems:
        print("\n".join("STOP: " + p for p in problems), file=sys.stderr)
        return 3
    with open(args.out, "w", encoding="utf-8") as out:
        for path in sorted(rows):
            out.write("\t".join(rows[path]) + "\n")
    total = sum(int(r[1]) for r in rows.values())
    new = sorted(set(rows) - set(old))
    print(f"  weights.tsv: {len(rows)} files, {total / 1e9:.1f} GB ({len(new)} new, "
          f"{len(set(old) - set(rows))} dropped from the old inventory)")
    for path in new:
        print(f"    + {path} ({int(rows[path][1]) / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
