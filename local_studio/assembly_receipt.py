"""Content versions for montage inputs; hashing never launches media processing."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.fingerprints import json_sha256


def file_sha256(path: Path) -> str:
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Файл изменился во время вычисления версии')
    return value


def assembly_snapshot(paths: dict[str, Path], recipe: dict) -> dict:
    sources = {key: file_sha256(Path(path)) for key, path in paths.items()}
    inputs = {'schema_version': 1, 'recipe': recipe, 'sources': sources}
    return {**inputs, 'input_fingerprint_sha256': json_sha256(inputs)}


def verify_sources(paths: dict[str, Path], snapshot: dict) -> None:
    if set(paths) != set(snapshot['sources']):
        raise ValueError('Состав исходников монтажа изменился')
    for key, path in paths.items():
        if file_sha256(Path(path)) != snapshot['sources'][key]:
            raise ValueError(f'Исходник монтажа изменился: {key}')
