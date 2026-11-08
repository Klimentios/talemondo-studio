#!/usr/bin/env python3
"""Collect probe-all data from the GPU server and pair it with the Studio job (docs/reference/H3_ENCODER_RESEARCH.md).

  python3 scripts/h3_probe_collect.py            # once
  python3 scripts/h3_probe_collect.py --loop 600 # every 10 min

Copies /workspace/h3/api-data/probes/<client_job_id>/ to logs/probes-all/<job_id>/ (skips folders already
complete) and writes job.json next to it: Studio prompt, compiled prompt, settings, references (role, owner),
state, timing and the result path — everything an analysis needs without the Studio database.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs/probes-all"
STUDIO = "http://127.0.0.1:18187"
TUNNEL = Path.home() / "Library/Application Support/MiniMax H3/tunnel.sh"
KEY = Path.home() / ".ssh/id_ed25519"


def server() -> str:
    return re.search(r'^SERVER_HOST="([^"]+)"', TUNNEL.read_text(), re.M).group(1)


def once() -> int:
    host = server()
    listing = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-i", str(KEY), f"root@{host}",
                              "cd /workspace/h3/api-data/probes 2>/dev/null && for d in */; do "
                              "printf '%s %s\\n' \"${d%/}\" \"$(ls \"$d\" | tr '\\n' ',')\"; done"],
                             capture_output=True, text=True, timeout=60)
    if listing.returncode:
        print("сервер недоступен:", listing.stderr.strip()[:200])
        return 0
    with urllib.request.urlopen(f"{STUDIO}/api/jobs", timeout=20) as resp:
        jobs = {j["job_id"]: j for j in json.load(resp)}
    new = 0
    for line in listing.stdout.splitlines():
        name, files = line.split(" ", 1)
        job = jobs.get(name)
        if job and job["state"] not in ("completed", "failed", "interrupted", "cancelled"):
            continue  # unfinished Studio job
        if "code.pt" not in files or (not job and "job.done" not in files and "h3_input.pt" not in files
                                      and "attn_s" not in files):
            continue
        # Research renders (A/B harness, probes) belong to the code library too (user 06.11: «во время рендера
        # сразу запускать зонды для сбора данных кода Qwen — библиотека языка Qwen»); they have no Studio job.
        target = OUT / name
        if (target / "job.json").exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        copy = subprocess.run(["scp", "-q", "-r", "-o", "BatchMode=yes", "-i", str(KEY),
                               f"root@{host}:/workspace/h3/api-data/probes/{name}/.", str(target)], timeout=600)
        if copy.returncode:
            continue
        if not job:
            (target / "job.json").write_text(json.dumps({"job_id": name, "research": True}, ensure_ascii=False),
                                             encoding="utf-8")
            new += 1
            print("собран (исследование)", name)
            continue
        manifest = json.loads(job["manifest_json"])
        (target / "job.json").write_text(json.dumps({
            "job_id": name, "state": job["state"], "created_at": job["created_at"], "updated_at": job["updated_at"],
            "original_prompt": job["original_prompt"], "compiled_prompt": job["compiled_prompt"],
            "settings": json.loads(job["settings_json"]),
            "references": [{"kind": kind, "number": e.get("display_number"), "role": e.get("role"),
                            "owner": e.get("owner"), "asset_id": e["asset_id"]}
                           for kind in ("images", "videos", "audios") for e in manifest[kind]],
            "budget": json.loads(job["budget_json"]) if job.get("budget_json") else None,
            "output_path": job.get("output_path"), "error": job.get("error"),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        new += 1
        print("собран", name)
    return new


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", type=int, default=0, help="повторять каждые N секунд")
    args = parser.parse_args()
    while True:
        try:
            n = once()
            print(time.strftime("%H:%M"), f"новых: {n}, всего: {len(list(OUT.glob('*/job.json')))}", flush=True)
        except Exception as exc:  # keep collecting through outages
            print(time.strftime("%H:%M"), "ошибка сбора:", exc, flush=True)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())
