"""Read-only evidence for recreating an H3 job without consuming GPU time."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.fingerprints import json_sha256


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def job_reproducibility(store, job: dict) -> dict:
    settings = json.loads(job["settings_json"])
    manifest = json.loads(job["manifest_json"])
    references = []
    blockers = []
    for kind in ("images", "videos", "audios"):
        for entry in manifest.get(kind, []):
            asset = store.get_asset(entry["asset_id"])
            path = Path(asset["path"]) if asset else None
            actual = _digest(path) if path and path.is_file() else None
            matches = actual == entry["sha256"]
            references.append({"asset_id": entry["asset_id"], "kind": kind,
                               "expected_sha256": entry["sha256"], "actual_sha256": actual,
                               "matches": matches, "role": entry.get("role"),
                               "owner": entry.get("owner")})
            if not matches:
                blockers.append(f"reference_changed_or_missing:{entry['asset_id']}")
    output = Path(job["output_path"]) if job.get("output_path") else None
    output_hash = _digest(output) if output and output.is_file() else None
    if job["state"] == "completed" and not output_hash:
        blockers.append("completed_output_missing")
    expected_output = job.get("output_sha256")
    if expected_output and output_hash and expected_output != output_hash:
        blockers.append("output_changed")
    inputs = {"original_prompt": job["original_prompt"],
              "compiled_prompt": job["compiled_prompt"], "settings": settings,
              "manifest": manifest, "mapping": json.loads(job["mapping_json"])}
    if job.get("production_spec_json"):
        inputs["production_spec"] = json.loads(job["production_spec_json"])
    fingerprint = json_sha256(inputs)
    director = json.loads(job.get("director_json") or "{}")
    return {"job_id": job["job_id"], "state": job["state"], "input_fingerprint_sha256": fingerprint,
            "inputs": inputs, "references": references, "references_verified": not any(
                item["matches"] is False for item in references), "output_sha256": output_hash,
            "blockers": blockers,
            "recorded_output_sha256": expected_output,
            "output_integrity": ("matches" if expected_output == output_hash else "changed") if expected_output else "unknown_legacy",
            "execution_record": director.get("execution_record"),
            "execution_observation": director.get("execution_observation"),
            "environment_matches": None,
            "replay_policy": "Проверка входов не запускает генерацию. Повтор требует сверки версии GPU-графа, узлов, весов и явного разрешения на расход GPU; одинаковый seed не гарантирует идентичный MP4."}
