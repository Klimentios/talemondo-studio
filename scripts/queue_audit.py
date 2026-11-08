#!/usr/bin/env python3
"""Read-only audit of the Studio queue BEFORE the GPU spends time on it.

Takes broken at submission (v1.5 / reskin swaps, the whole episode plot in a part prompt, parts cut on round seconds,
no costume or place reference) are visible in the job record; this script lists them so the supervising agent stops the queue and asks the user.

    python3 scripts/queue_audit.py              # queued jobs (queued_local / queued_gpu / running)
    python3 scripts/queue_audit.py --all        # + jobs finished in the last 24 h
    python3 scripts/queue_audit.py --json

Exit code 1 when any job has a BLOCK finding. Nothing is changed: no cancel, no pause, no GPU call.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

STUDIO = "http://127.0.0.1:18187"
DB = Path(os.environ.get("H3_MAC_DATA_DIR", str(Path.home() / "Library/Application Support/H3 Studio"))) / "studio.sqlite3"
ACTIVE = {"queued_local", "uploading", "queued_gpu", "running"}
AB_ADAPTERS = {"character_swap_v15"}
SOURCE_ROLES = {"Исходное видео", "Движение и камера"}
EDGE_S = 0.5  # a source cut this close to a part edge = a tail of the neighbouring shot (R3)
PART = re.compile(r"(?P<src>[\w-]+)\s+part\s+(?P<n>\d+)(?:\s+of\s+(?P<m>\d+))?", re.IGNORECASE)


def api(path: str):
    with urllib.request.urlopen(STUDIO + path, timeout=20) as response:
        return json.load(response)


def asset_paths(ids: list[str]) -> dict[str, str]:
    if not ids or not DB.exists():
        return {}
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as db:
        marks = ",".join("?" * len(ids))
        return dict(db.execute(f"SELECT asset_id, path FROM assets WHERE asset_id IN ({marks})", ids).fetchall())


def edge_cuts(path: str) -> list[float]:
    """Hard cuts within EDGE_S of the start or end of a source video (ffmpeg scene score > 0.35)."""
    try:
        duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                         "csv=p=0", path], capture_output=True, text=True, timeout=30).stdout)
        found = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an", "-vf",
                                "select='gt(scene,0.35)',showinfo", "-f", "null", "-"],
                               capture_output=True, text=True, timeout=120).stderr
    except (subprocess.SubprocessError, ValueError, OSError):
        return []
    cuts = [float(t) for t in re.findall(r"pts_time:([\d.]+)", found)]
    return [round(c, 2) for c in cuts if c < EDGE_S or c > duration - EDGE_S]


def summary_of(prompt: str) -> str:
    match = re.search(r"summary:\s*\n(.+?)(?:\n\s*\n|\n[a-z_]+:\s*\n)", prompt, re.S)
    return " ".join((match.group(1) if match else "").split())


def audit(job: dict) -> list[tuple[str, str]]:
    settings = json.loads(job.get("settings_json") or "{}")
    manifest = json.loads(job.get("manifest_json") or "{}")
    prompt = job.get("original_prompt") or job.get("prompt") or ""
    out: list[tuple[str, str]] = []
    ab = [name for name, on in (("v1.5", settings.get("style_adapter") in AB_ADAPTERS or settings.get("swap_lora") == "v1.5"),
                                ("reskin", bool(settings.get("reskin"))),
                                ("маска", bool(settings.get("swap_mask")))) if on]
    if ab and not (settings.get("ab_user_request") or settings.get("experiment_request")):
        out.append(("BLOCK", f"R1 A/B без просьбы пользователя: {', '.join(ab)} — не принятый рецепт"))
    videos = [v for v in manifest.get("videos", []) if v.get("role") in SOURCE_ROLES]
    images = manifest.get("images", [])
    if videos:
        owners = {p.get("owner") for v in videos for p in (v.get("performers") or []) if p.get("owner")}
        for owner in sorted(owners):
            roles = {i.get("role") for i in images if i.get("owner") == owner}
            if not {"Лицо героя", "Персонаж целиком"} <= roles:
                out.append(("WARN", f"{owner}: нет пары «Лицо героя» + «Персонаж целиком»"))
        if not any(i.get("role") in {"Кадр сцены", "Окружение"} for i in images):
            out.append(("WARN", "R5/R6 нет «Кадра сцены»/«Окружения»: фон референса и комната будут плыть"))
        summary = summary_of(prompt)
        if PART.search(summary) and len(re.findall(r"[.!?](?:\s|$)", summary)) > 3:
            out.append(("BLOCK", f"R2 в summary части {len(summary.split())} слов и >3 предложений — похоже на "
                                 "сюжет всего ролика; часть описывает только свои секунды"))
        paths = asset_paths([v["asset_id"] for v in videos if v.get("asset_id")])
        for asset_id, path in paths.items():
            cuts = edge_cuts(path) if Path(path).exists() else []
            if cuts:
                out.append(("BLOCK", f"R3 склейка исходника у края части {cuts} с — обрывок соседнего шота"))
    if ab and settings.get("mode") == "replace" and len([i for i in images if i.get("owner")]) <= len(
            {i.get("owner") for i in images if i.get("owner")}):
        out.append(("WARN", "замена по одному фото на героя: на исходнике в одежде/другой породы не перебивает"))
    return out


def series_key(job: dict) -> str | None:
    match = PART.search(summary_of(job.get("original_prompt") or job.get("prompt") or ""))
    return match.group("src").upper() if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="также задания, завершённые за 24 ч")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [j for j in api("/api/jobs") if j["state"] in ACTIVE
            or (args.all and j["state"] in {"completed", "failed", "interrupted"} and j["updated_at"] >= since)]
    report, series = [], defaultdict(list)
    for row in rows:
        job = api(f"/api/jobs/{row['job_id']}")
        findings = audit(job)
        key = series_key(job)
        if key:
            series[key].append(job)
        report.append({"job_id": job["job_id"], "state": job["state"], "series": key,
                       "summary": summary_of(job.get("original_prompt") or "")[:90], "findings": findings})
    for key, jobs in series.items():
        queued = [j for j in jobs if j["state"] in ACTIVE]
        done = [j for j in jobs if j["state"] == "completed"]
        if len(queued) > 1 and not done:
            for item in report:
                if item["series"] == key and item["state"] in ACTIVE:
                    item["findings"].append(("WARN", f"серия {key}: {len(queued)} частей в очереди без просмотренной "
                                                     "первой — сначала одна проба"))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        blocks = sum(1 for r in report for level, _ in r["findings"] if level == "BLOCK")
        print(f"Заданий: {len(report)}, с BLOCK: {sum(any(l == 'BLOCK' for l, _ in r['findings']) for r in report)}"
              f" ({blocks} нарушений)")
        for item in report:
            mark = "✗" if any(l == "BLOCK" for l, _ in item["findings"]) else ("!" if item["findings"] else "✓")
            print(f"{mark} {item['job_id'][:8]} {item['state']:<12} {item['summary']}")
            for level, text in item["findings"]:
                print(f"     {level}: {text}")
    return 1 if any(l == "BLOCK" for r in report for l, _ in r["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
