#!/usr/bin/env python3
"""Durable FIFO dispatcher for H3 jobs staged from Mac Studio."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

ROOT = Path(os.environ.get("H3_STAGED_QUEUE", "/workspace/h3/staged-jobs/queue"))
API = os.environ.get("H3_GPU_API_URL", "http://127.0.0.1:18189").rstrip("/")
COMFY = os.environ.get("H3_COMFY_URL", "http://127.0.0.1:18188").rstrip("/")
# needs_reconciliation is parked for an operator: it must neither be resent
# automatically nor block every later job in the FIFO.
PARKED_STATES = {"completed", "failed", "cancelled", "needs_reconciliation"}
UNKNOWN_STATUS_LIMIT_SECONDS = 1800


def atomic_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def form_data(job: dict) -> tuple[dict, list[tuple[str, tuple]]]:
    settings = json.loads(job["settings_json"])
    manifest = json.loads(job["manifest_json"])
    by_id = {item["asset_id"]: item for item in job["files"]}
    fields = {
        "client_job_id": job["job_id"], "manifest_json": job["manifest_json"],
        "prompt": job["compiled_prompt"],
        "precompiled_prompt": "true",
        "duration": str(settings["duration"]), "aspect_ratio": settings["aspect_ratio"],
        "quality": settings["quality"], "megapixels": str(settings.get("megapixels") or ""),
        "profile": "bf16", "steps": str(settings["steps"]), "seed": str(settings["seed"]),
        "ref_image_size": settings["ref_image_size"],
        "use_video_audio": str(bool(settings.get("use_video_audio", False))).lower(),
        "control_type": "none",  # legacy field; structural ControlNet was removed
        "mode": settings.get("mode", "auto"),
        "camera_state_json": json.dumps(settings["camera_state"], ensure_ascii=False)
            if settings.get("camera_state") else "",
        "guide_frames_json": json.dumps(settings.get("guide_frames", [])),
        "style_adapter": settings.get("style_adapter", "none"),
        "cache_mode": settings.get("cache_mode", "safe"),
    }
    uploads = []
    for group in ("images", "videos", "audios"):
        for entry in manifest[group]:
            asset = by_id[entry["asset_id"]]
            path = Path(job["_folder"]) / asset["path"]
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"SHA-256 mismatch: {entry['asset_id']}")
            uploads.append((group, (asset["filename"], data, asset["mime"])))
    return fields, uploads


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / ".dispatcher.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        while True:
            folders = sorted(p for p in ROOT.iterdir() if p.is_dir() and (p / "job.json").exists())
            folders = [p for p in folders if not (p / "receipt.json").exists()
                       or json.loads((p / "receipt.json").read_text()).get("state") not in PARKED_STATES]
            if not folders:
                time.sleep(5); continue
            folder = folders[0]
            job = json.loads((folder / "job.json").read_text())
            job["_folder"] = str(folder)
            receipt_path = folder / "receipt.json"
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {"state": "staged"}
            client_id = job["job_id"]
            if float(receipt.get("next_attempt_at", 0)) > time.time():
                time.sleep(min(10, float(receipt["next_attempt_at"]) - time.time())); continue
            with httpx.Client(timeout=httpx.Timeout(1800, connect=20)) as client:
                if receipt.get("state") in PARKED_STATES:
                    time.sleep(1); continue
                try:
                    lookup = client.get(f"{API}/api/jobs/by-client/{client_id}")
                    if lookup.status_code == 200:
                        record = lookup.json()
                        remote_id = record.get("job_id")
                        if not remote_id:
                            atomic_json(receipt_path, {"state": "needs_reconciliation", "client_job_id": client_id,
                                "error": "Server registry has no remote job ID; automatic resend prohibited"})
                            time.sleep(30); continue
                        receipt = {"state": "accepted", "client_job_id": client_id, "remote_job_id": remote_id}
                        atomic_json(receipt_path, receipt)
                    elif lookup.status_code == 404:
                        if receipt.get("state") == "submitting":
                            atomic_json(receipt_path, {"state": "needs_reconciliation", "client_job_id": client_id,
                                "error": "Interrupted submit; registry 404 is not proof that no prompt was queued"})
                            time.sleep(30); continue
                        fields, uploads = form_data(job)
                        retry_count = int(receipt.get("retry_count", 0))
                        atomic_json(receipt_path, {"state": "submitting", "client_job_id": client_id,
                                                   "retry_count": retry_count})
                        response = client.post(f"{API}/api/jobs", data=fields, files=uploads)
                        if response.status_code >= 400:
                            detail = response.text[:2000]
                            after_error = client.get(f"{API}/api/jobs/by-client/{client_id}")
                            if after_error.status_code == 200 and after_error.json().get("job_id"):
                                payload = after_error.json()
                            elif after_error.status_code == 404 and (
                                    response.status_code in {408, 429, 500, 502, 504}
                                    # 503 is temporary only when the API says so (Qwen/ComfyUI
                                    # not ready). A 503 without Retry-After is a rejected plan.
                                    or (response.status_code == 503 and "retry-after" in response.headers)):
                                retry_count += 1
                                if retry_count <= 20:
                                    delay = min(300, 5 * (2 ** min(retry_count - 1, 6)))
                                    atomic_json(receipt_path, {"state": "staged", "client_job_id": client_id,
                                        "retry_count": retry_count, "next_attempt_at": time.time() + delay,
                                        "last_http_status": response.status_code, "last_error": detail})
                                    print(f"temporary HTTP {response.status_code}; safely retrying {client_id} in {delay}s", flush=True)
                                    time.sleep(delay)
                                    continue
                                atomic_json(receipt_path, {"state": "failed", "client_job_id": client_id,
                                    "http_status": response.status_code, "error": detail,
                                    "retry_count": retry_count})
                                continue
                            elif after_error.status_code == 404:
                                # Rejected before acceptance (4xx or a rejected plan): nothing queued.
                                atomic_json(receipt_path, {"state": "failed", "client_job_id": client_id,
                                    "http_status": response.status_code, "error": detail})
                                continue
                            else:
                                atomic_json(receipt_path, {"state": "needs_reconciliation", "client_job_id": client_id,
                                    "http_status": response.status_code, "error": detail})
                                time.sleep(30); continue
                        else:
                            payload = response.json()
                        remote_id = payload.get("job_id")
                        if not remote_id:
                            atomic_json(receipt_path, {"state": "needs_reconciliation", "client_job_id": client_id,
                                "error": "API accepted request without a remote ID"})
                            time.sleep(30); continue
                        receipt = {"state": "accepted", "client_job_id": client_id, "remote_job_id": remote_id,
                                   "director": payload.get("director")}
                        atomic_json(receipt_path, receipt)
                    else:
                        time.sleep(5); continue
                    remote_id = receipt["remote_job_id"]
                    status = client.get(f"{API}/api/jobs/{remote_id}")
                    if status.status_code != 200:
                        time.sleep(10); continue
                    remote = status.json()
                    if remote.get("status") == "success":
                        atomic_json(receipt_path, {**receipt, "state": "completed", "completed_at": time.time()})
                    elif remote.get("status") == "error":
                        atomic_json(receipt_path, {**receipt, "state": "failed", "error": remote.get("error", "GPU error")})
                    elif remote.get("status") == "unknown":
                        first_unknown = float(receipt.get("unknown_since") or time.time())
                        if time.time() - first_unknown >= UNKNOWN_STATUS_LIMIT_SECONDS:
                            atomic_json(receipt_path, {**receipt, "state": "needs_reconciliation",
                                "error": "ComfyUI lost the prompt ID (restart or history eviction)"})
                        else:
                            atomic_json(receipt_path, {**receipt, "unknown_since": first_unknown})
                            time.sleep(10)
                    else:
                        receipt.pop("unknown_since", None)
                        atomic_json(receipt_path, {**receipt, "state": remote.get("status", "accepted")})
                        time.sleep(10)
                except Exception as exc:
                    # Keep accepted IDs durable; never resubmit after an ambiguous exception.
                    if receipt.get("state") == "submitting":
                        atomic_json(receipt_path, {"state": "needs_reconciliation", "client_job_id": client_id,
                                                   "error": str(exc)[:2000]})
                    time.sleep(10)


if __name__ == "__main__":
    main()
