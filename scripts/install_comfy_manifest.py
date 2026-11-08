#!/usr/bin/env python3
"""Install the Studio ComfyUI manifest on a GPU server: pinned node packs and SHA-256-checked models.

  python3 install_comfy_manifest.py --comfy /workspace/h3/ComfyUI --python /workspace/h3/venv/bin/python \
      --manifest manifest.json [--apply]

Without --apply prints the plan only. Idempotent: an existing pack must sit on its pinned commit and an
existing model must match its SHA-256, otherwise it stops (never overwrites what someone else put there).
Prints CHANGED=<n> last; exit 0 = done, 3 = conflict, 2 = bad manifest. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

COMMIT = re.compile(r"[0-9a-f]{40}")
DIR_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
PIP_PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.+!_-]+")
REPO = re.compile(r"https://(?:github\.com|gitlab\.com|huggingface\.co|codeberg\.org)/[\w.-]+/[\w.-]+?(?:\.git)?")
FOLDER = re.compile(r"[a-z0-9_]{2,40}")
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,180}\.(?:safetensors|ckpt|pt|pth|bin|gguf|onnx|sft|yaml|json)")
SHA = re.compile(r"[0-9a-f]{64}")


class Conflict(Exception):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(args: list[str], **kw) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout.strip()


def check_manifest(data: dict) -> list[str]:
    problems = []
    for node in data.get("nodes", []):
        if not (DIR_NAME.fullmatch(node.get("dir", "")) and REPO.fullmatch(node.get("repo", ""))
                and COMMIT.fullmatch(node.get("commit", ""))
                and all(PIP_PIN.fullmatch(pin) for pin in node.get("pip", []))):
            problems.append(f"node entry {node.get('id')}: dir/repo/commit/pip invalid")
    for model in data.get("models", []):
        if not (FOLDER.fullmatch(model.get("folder", "")) and FILENAME.fullmatch(model.get("filename", ""))
                and ".." not in model.get("filename", "") and str(model.get("url", "")).startswith("https://")
                and SHA.fullmatch(model.get("sha256", ""))):
            problems.append(f"model entry {model.get('id')}: folder/filename/url/sha256 invalid")
    return problems


def plan_node(comfy: Path, node: dict) -> str:
    target = comfy / "custom_nodes" / node["dir"]
    if not target.exists():
        return "install"
    if not (target / ".git").exists():
        raise Conflict(f"{target} exists but is not a git checkout")
    head = run(["git", "-C", str(target), "rev-parse", "HEAD"])
    if head != node["commit"]:
        raise Conflict(f"{target} is on {head[:12]}, manifest pins {node['commit'][:12]}")
    return "ok"


def install_node(comfy: Path, python: str, node: dict) -> None:
    target = comfy / "custom_nodes" / node["dir"]
    partial = target.with_name(f".{target.name}.partial")
    if partial.exists():
        subprocess.run(["rm", "-rf", str(partial)], check=True)
    run(["git", "clone", "--quiet", node["repo"], str(partial)], timeout=900)
    run(["git", "-C", str(partial), "checkout", "--quiet", "--detach", node["commit"]])
    if node.get("pip"):
        run([python, "-m", "pip", "install", "--disable-pip-version-check", *node["pip"]], timeout=1800)
    partial.rename(target)


def plan_model(comfy: Path, model: dict) -> str:
    target = comfy / "models" / model["folder"] / model["filename"]
    if not target.exists():
        return "download"
    if sha256(target) != model["sha256"]:
        raise Conflict(f"{target} exists with a different SHA-256")
    return "ok"


def install_model(comfy: Path, model: dict) -> None:
    target = comfy / "models" / model["folder"] / model["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    subprocess.run(["curl", "-fL", "--retry", "8", "--retry-delay", "5", "-C", "-", "-o", str(part), model["url"]],
                   check=True, timeout=6 * 3600)
    got = sha256(part)
    if got != model["sha256"]:
        part.unlink(missing_ok=True)
        raise Conflict(f"SHA-256 mismatch for {model['filename']}: got {got[:12]}")
    part.rename(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--comfy", type=Path, required=True)
    parser.add_argument("--python", required=True, help="python of ComfyUI's venv (pip pins go there)")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not (args.comfy / "custom_nodes").is_dir():
        parser.error("--comfy must be a ComfyUI folder")
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    problems = check_manifest(data)
    if problems:
        print("\n".join(f"FAIL {p}" for p in problems))
        return 2
    changed = 0
    try:
        for node in data.get("nodes", []):
            action = plan_node(args.comfy, node)
            print(f"node  {node['dir']:<40} {node['commit'][:12]}  {action}")
            if action == "install" and args.apply:
                install_node(args.comfy, args.python, node)
                changed += 1
                print(f"      installed {node['dir']}")
        for model in data.get("models", []):
            action = plan_model(args.comfy, model)
            print(f"model {model['folder'] + '/' + model['filename']:<40} {action}")
            if action == "download" and args.apply:
                install_model(args.comfy, model)
                changed += 1
                print(f"      downloaded and verified {model['filename']}")
    except Conflict as exc:
        print(f"CONFLICT {exc}")
        print(f"CHANGED={changed}")
        return 3
    except subprocess.SubprocessError as exc:
        print(f"FAIL {exc}")
        print(f"CHANGED={changed}")
        return 1
    if not args.apply:
        print("DRY RUN: nothing installed (add --apply)")
    print(f"CHANGED={changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
