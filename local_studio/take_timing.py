"""Plan vs result timing of finished H3 takes, measured once and read by the production gate.

Takes can break their shot plan (scripts/h3_take_check.py) — cuts 0.5–4 s late, merged or missing shots — and
the same parts then get rendered again. record() runs once when a take is downloaded
(worker, background thread) and stores <runtime>/take_timing/<sha256(job_id)>.json; previous_part_drift() only
reads those files (no ffmpeg in create_job) and stops a new job on a source part whose last take drifted.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

SCENE = 0.30        # ffmpeg scene score for a hard cut
DRIFT_LIMIT = 0.5   # s
MIN_SHOT = 1.0      # s
CONFIRM_KEY = "confirm_after_rejects"  # the same single user confirmation as production_gates
_RANGE = re.compile(r"\[Shot (\d+):\s*([0-9.]+)\s*[–-]\s*([0-9.]+)\s*s\]")
_AT = re.compile(r"\[Shot (\d+)\]\s*At\s+(\d+):(\d+(?:\.\d+)?)")


def cuts(path: str | Path) -> list[float]:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
                          f"select='gt(scene,{SCENE})',showinfo", "-an", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=180).stderr
    return [round(float(t), 2) for t in re.findall(r"pts_time:([0-9.]+)", out)]


def duration(path: str | Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=30).stdout.strip()
    return float(out or 0)


def plan(prompt: str, length: float) -> list[tuple[int, float, float]]:
    """[(shot, start, end)] from «[Shot N: a–b s]» or «[Shot N] At MM:SS.mmm» (shot 1 starts at 0)."""
    spans = [(int(n), float(a), float(b)) for n, a, b in _RANGE.findall(prompt)]
    if spans:
        return spans
    starts = {1: 0.0}
    for n, m, s in _AT.findall(prompt):
        starts[int(n)] = int(m) * 60 + float(s)
    order = sorted(starts.items(), key=lambda kv: kv[1])
    return [(n, a, order[i + 1][1] if i + 1 < len(order) else length) for i, (n, a) in enumerate(order)]


def compare(shots: list[tuple[int, float, float]], got: list[float]) -> dict:
    """Planned cuts vs measured cuts: drift per planned cut (None = missing), extra cuts, short shots."""
    planned = [a for _, a, _ in shots[1:]]
    left = list(got)
    drift = []
    for p in planned:
        if not left:
            drift.append([p, None])
            continue
        nearest = min(left, key=lambda c: abs(c - p))
        drift.append([p, round(nearest - p, 2)])
        left.remove(nearest)
    return {"planned": planned, "cuts": got, "drift": drift, "extra": left,
            "short": [[n, round(b - a, 2)] for n, a, b in shots if 0 < b - a < MIN_SHOT]}


def _file(root: Path, job_id: str) -> Path:
    return Path(root) / "take_timing" / (hashlib.sha256(job_id.encode()).hexdigest() + ".json")


def record(store, job_id: str) -> dict | None:
    """Measure a finished take once; never raises (it runs after the download, off the event loop)."""
    try:
        job = store.get_job(job_id)
        if not job or not job.get("output_path"):
            return None
        manifest = json.loads(job["manifest_json"])
        length = duration(job["output_path"])
        result = {"job_id": job_id, "length": round(length, 2),
                  "source_asset_id": manifest["videos"][0]["asset_id"] if manifest.get("videos") else None,
                  **compare(plan(job["compiled_prompt"], length), cuts(job["output_path"]))}
        target = _file(store.root, job_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result
    except Exception:  # research measurement: a failure must not touch the job
        return None


def problems(timing: dict) -> list[str]:
    out = []
    late = [(p, d) for p, d in timing.get("drift", []) if d is None or abs(d) > DRIFT_LIMIT]
    if late:
        out.append("склейки не по плану: " + ", ".join(f"{p} с → {'нет' if d is None else f'{d:+} с'}" for p, d in late))
    if timing.get("extra"):
        out.append("лишние склейки: " + ", ".join(f"{c} с" for c in timing["extra"]))
    return out


def previous_part_drift(store, db, manifest: dict, settings: dict) -> str | None:
    """production_gates format: why a new job on this source part should not go to the GPU, or None.

    Looks at the newest measured take that used the same source video asset; no measurement → None."""
    if settings.get(CONFIRM_KEY) is True or not manifest.get("videos"):
        return None
    source = manifest["videos"][0]["asset_id"]
    rows = db.execute("SELECT job_id FROM jobs WHERE state='completed' AND manifest_json LIKE ? "
                      "ORDER BY updated_at DESC LIMIT 5", (f'%"{source}"%',)).fetchall()
    for (job_id,) in rows:
        path = _file(store.root, job_id)
        if not path.exists():
            continue
        timing = json.loads(path.read_text(encoding="utf-8"))
        found = problems(timing)
        if not found:
            return None
        return (f"Прошлый дубль этой части исходника ({job_id[:8]}) не выполнил план шотов: " + "; ".join(found)
                + ". Сначала исправь нарезку части (целые планы исходника, шоты ≥ 1 с, план = склейки этой части), "
                f"а для повтора как есть нужен {CONFIRM_KEY} со словами пользователя")
    return None
