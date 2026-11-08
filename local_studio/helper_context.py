"""Small evidence-addressed helper reports. No media paths or model calls are accepted here."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_FIELDS = ("verdict", "findings", "evidence", "uncertainties", "suggested_fix")
_VERDICTS = {"pass", "fail", "uncertain"}


def build_check_key(*, franchise_id: str, role: str, task: str, provider: str,
                    evidence_hash: str, skill_version: str, model_identity: str,
                    model_pinned: bool = False) -> str | None:
    """Return a stable key only for explicit evidence, skill and pinned model identity.

    model_pinned is supplied by the caller after resolving an immutable model version;
    a user-facing alias/default model must not be marked pinned.
    """
    fields = (franchise_id, role, task, provider, skill_version, model_identity)
    if not model_pinned or not all(isinstance(value, str) and value.strip() for value in fields):
        return None
    if not isinstance(evidence_hash, str) or not _SHA256.fullmatch(evidence_hash):
        return None
    if model_identity.strip().lower() in {"default", "auto", "latest"}:
        return None
    payload = {"schema": 1, "franchise_id": franchise_id, "role": role, "task": task,
               "provider": provider, "evidence_hash": evidence_hash,
               "skill_version": skill_version, "model_identity": model_identity}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def parse_helper_response(text: str) -> dict:
    """Accept one JSON object (optionally fenced); preserve older plain-text replies."""
    candidate = text.strip()
    if candidate.startswith("```json\n") and candidate.endswith("```"):
        candidate = candidate[8:-3].strip()
    try:
        value = json.loads(candidate)
    except (ValueError, TypeError):
        value = None
    valid = (isinstance(value, dict) and value.get("verdict") in _VERDICTS
             and all(isinstance(value.get(field), list) for field in ("findings", "evidence", "uncertainties"))
             and isinstance(value.get("suggested_fix"), str))
    if not valid:
        return {"structured": False, "text": text}
    return {"structured": True, **{field: value[field] for field in _FIELDS}}


def load_check(root: Path, key: str | None) -> dict | None:
    """Read a previously validated report; missing or damaged entries are cache misses."""
    if not isinstance(key, str) or not _SHA256.fullmatch(key):
        return None
    try:
        entry = json.loads((root / "helper_checks" / f"{key}.json").read_text(encoding="utf-8"))
        if entry.get("key") != key:
            return None
        report = parse_helper_response(json.dumps(entry["report"], ensure_ascii=False))
        return report if report["structured"] else None
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return None


def save_check(root: Path, key: str | None, report: dict) -> bool:
    """Atomically publish structured reports only; never cache legacy prose."""
    if not isinstance(key, str) or not _SHA256.fullmatch(key) or report.get("structured") is not True:
        return False
    validated = parse_helper_response(json.dumps(report, ensure_ascii=False))
    if not validated["structured"]:
        return False
    folder = root / "helper_checks"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".check-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"key": key, "report": validated}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, folder / f"{key}.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True
