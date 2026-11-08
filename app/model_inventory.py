"""Explicit disk-weight attestation with cheap later drift checks.

This describes files on disk, never tensors currently resident in a GPU process.
Building the index hashes the selected files once; reading it never hashes weights.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def file_identity(stat) -> dict:
    return {key: getattr(stat, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def weight_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
        raise ValueError('Путь веса выходит за разрешённое хранилище')
    return path


def attest_weights(root: Path, expected: dict[str, str]) -> dict:
    entries = {}
    for relative, wanted in expected.items():
        path = weight_path(root, relative)
        with path.open('rb') as stream:
            before = file_identity(os.fstat(stream.fileno()))
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = file_identity(path.stat())
        if before != after:
            raise ValueError(f'Вес изменился во время сверки: {relative}')
        if digest != wanted:
            raise ValueError(f'SHA-256 веса не совпал: {relative}')
        entries[relative] = {'sha256': digest, 'identity': after, 'resolved': str(path.resolve().relative_to(root.resolve()))}
    return {'schema_version': 1, 'verified_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'disk_files_at_attestation', 'entries': entries}


def check_weight(root: Path, inventory: dict, relative: str) -> dict:
    path = weight_path(root, relative)
    if inventory.get('schema_version') != 1:
        return {'state': 'unknown', 'sha256': None, 'reason': 'unsupported_inventory'}
    entry = inventory.get('entries', {}).get(relative)
    if not entry:
        return {'state': 'unknown', 'sha256': None, 'reason': 'not_attested'}
    try:
        unchanged = (file_identity(path.stat()) == entry['identity']
                     and str(path.resolve().relative_to(root.resolve())) == entry['resolved'])
    except OSError:
        unchanged = False
    return {'state': 'unchanged_since_attestation' if unchanged else 'changed_or_missing',
            'sha256': entry['sha256'] if unchanged else None,
            'verified_at': inventory.get('verified_at'), 'scope': 'disk_file_metadata_check'}


def write_inventory(path: Path, inventory: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(inventory, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
