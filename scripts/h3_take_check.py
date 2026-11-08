#!/usr/bin/env python3
"""Plan vs result for finished H3 takes: does the MP4 follow the shot plan it was given?

  python3 scripts/h3_take_check.py JOB_ID [JOB_ID…]
  python3 scripts/h3_take_check.py --since "2026-11-05 20:00"      # every completed take since (UTC)

Per take: planned shot starts from the prompt ([Shot N: a–b s] or [Shot N] At MM:SS.mmm), hard cuts in the
source video reference and in the MP4 (ffmpeg scene score), cut drift, missing/extra cuts and shots shorter
than 1 s. It measures timing only — identity, costume, physics and sound still need the contact sheet and
a full watch (docs/guides/PRODUCER_QC.md).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import os
from pathlib import Path

RUNTIME = Path(os.environ.get("H3_MAC_DATA_DIR", str(Path.home() / "Library/Application Support/H3 Studio")))
DB = RUNTIME / "studio.sqlite3"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_studio.take_timing import MIN_SHOT, compare, cuts, duration, plan  # noqa: E402

DRIFT_OK = 0.35       # s; a cut further than this from its planned time counts as drifted


def check(db: sqlite3.Connection, job_id: str) -> dict:
    job = db.execute("SELECT job_id, state, compiled_prompt, settings_json, manifest_json, output_path FROM jobs "
                     "WHERE job_id LIKE ?", (job_id + "%",)).fetchone()
    if not job or job[1] != "completed" or not job[5] or not Path(job[5]).exists():
        return {"job": job_id, "error": "нет готового MP4"}
    jid, _, prompt, settings, manifest, output = job
    settings, manifest = json.loads(settings), json.loads(manifest)
    length = duration(output)
    shots = plan(prompt, length)
    measured = compare(shots, cuts(output))
    planned, got, unmatched = measured["planned"], measured["cuts"], measured["extra"]
    drift = [tuple(x) for x in measured["drift"]]
    source = None
    if manifest["videos"]:
        row = db.execute("SELECT path, original_name FROM assets WHERE asset_id=?",
                         (manifest["videos"][0]["asset_id"],)).fetchone()
        if row and Path(row[0]).exists():
            source = {"name": row[1], "cuts": cuts(row[0]), "role": manifest["videos"][0].get("role")}
    short = [(n, round(b - a, 2)) for n, a, b in shots if 0 < b - a < MIN_SHOT]
    problems = []
    if short:
        problems.append("шоты < 1 с: " + ", ".join(f"{n}={d}" for n, d in short))
    bad = [(p, d) for p, d in drift if d is None or abs(d) > DRIFT_OK]
    if bad:
        problems.append("склейки не по плану: " + ", ".join(f"{p}→{'нет' if d is None else f'{d:+}'}" for p, d in bad))
    if unmatched:
        problems.append("лишние склейки: " + ", ".join(map(str, unmatched)))
    dup = [a for a in {a for _, a, _ in shots} if sum(1 for _, x, _ in shots if x == a) > 1]
    if dup:
        problems.append("шоты с одинаковым началом: " + ", ".join(map(str, sorted(dup))))
    if source and len(source["cuts"]) + 1 > len(shots):
        problems.append(f"в исходнике {len(source['cuts']) + 1} планов, в промпте {len(shots)} шотов")
    return {"job": jid, "length": round(length, 2), "mode": settings.get("mode"),
            "route": settings.get("swap_lora") or settings.get("style_adapter"),
            "reskin": (settings.get("reskin") or {}).get("strength"),
            "shots": len(shots), "planned_cuts": planned, "mp4_cuts": got,
            "source": source, "drift": drift, "problems": problems}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs", nargs="*")
    parser.add_argument("--since", help="every completed take updated after this UTC time")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ids = list(args.jobs)
    if args.since:
        ids += [r[0] for r in db.execute("SELECT job_id FROM jobs WHERE state='completed' AND updated_at > ? "
                                         "ORDER BY updated_at", (args.since,))]
    results = [check(db, j) for j in ids]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return
    for r in results:
        if "error" in r:
            print(r["job"][:8], r["error"])
            continue
        src = r["source"]
        print(f"{r['job'][:8]} {r['length']:5.2f}с {r['mode']}/{r['route']}{' reskin ' + str(r['reskin']) if r['reskin'] else ''}"
              f" | шотов {r['shots']} план {r['planned_cuts']} | mp4 {r['mp4_cuts']}"
              + (f" | исходник {src['cuts']}" if src else ""))
        print("    " + ("; ".join(r["problems"]) if r["problems"] else "тайминг по плану"))
    bad = sum(1 for r in results if r.get("problems"))
    print(f"\nс проблемами тайминга: {bad} из {len(results)}")


if __name__ == "__main__":
    sys.exit(main())
