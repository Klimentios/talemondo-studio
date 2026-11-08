"""Stable JSON fingerprints shared by execution and production descriptions.

Keep this encoding compatible with existing receipts. Non-finite numbers are
rejected because they are not JSON values and cannot describe a replayable run.
"""
from __future__ import annotations

import hashlib
import json


def json_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
