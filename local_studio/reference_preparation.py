"""Content-addressed CPU evidence cache, independent of scene instructions and owners."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path

from app.context_ir.schema import digest
from .reference_segments import file_hash

VERSION = 'reference-previews-1'  # Bump when resolution, frame positions or encoding changes.


def prepared_evidence(root: Path, path: Path, sha256: str, kind: str, duration, build):
    """Reuse only exact input bytes + extraction recipe; never cache a model's judgement here."""
    if file_hash(path) != sha256:
        raise ValueError('Референс изменился перед подготовкой')
    spec = {'version': VERSION, 'sha256': sha256, 'kind': kind,
            'duration': duration if kind == 'video' else None}
    key = digest(spec)
    cache = root / 'reference-previews'
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (key + '.json')
    with (cache / '.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_file():
            try:
                saved = json.loads(target.read_text())
                if saved['spec'] == spec and saved['checksum'] == digest(saved['value']):
                    return saved['value']
            except (ValueError, KeyError, TypeError):
                pass  # A partial/corrupt local preview is disposable; source and jobs are not.
        value = build()
        if not value:
            raise ValueError('Не удалось подготовить референс')
        if file_hash(path) != sha256:
            raise ValueError('Референс изменился во время подготовки')
        data = json.dumps({'spec': spec, 'checksum': digest(value), 'value': value}, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(mode='w', dir=cache, suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return value
