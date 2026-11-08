"""Resolve completed production results without copying them into the render queue."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactKind = Literal['job', 'concat', 'edl', 'motion', 'asset']


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ArtifactKind
    id: str = Field(min_length=1, max_length=120)


def result_record(store, artifact_id: str, kind: ArtifactKind = 'job') -> dict:
    if kind == 'job':
        job = store.get_job(artifact_id)
        if not job or job.get('state') != 'completed' or not job.get('output_path'):
            raise ValueError('Завершённый результат не найден')
        return job
    if kind == 'motion':
        try:
            with store.connect() as db:
                row = db.execute('SELECT state,output_path,output_sha256,scene_id,revision FROM motion_jobs '
                                 'WHERE motion_id=?', (artifact_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            raise ValueError('Квитанция Motion Director недоступна') from exc
        if not row or row['state'] != 'completed' or not row['output_path']:
            raise ValueError('Завершённый результат Motion Director не найден')
        record = dict(row)
        root = store.results_dir
    elif kind == 'asset':
        asset = store.get_asset(artifact_id)
        if not asset or asset.get('kind') != 'video':
            raise ValueError('Видео результата не найдено в библиотеке')
        record = {'state': 'completed', 'output_path': asset['path'], 'output_sha256': asset['sha256']}
        root = store.assets_dir
    else:
        record = None
    if record is not None:
        if not Path(record['output_path']).resolve().is_relative_to(root.resolve()):
            raise ValueError('Результат находится вне хранилища Studio')
        return {**record, 'artifact_kind': kind}
    if kind not in {'concat', 'edl'} or not re.fullmatch(r'[a-f0-9]{32}', artifact_id):
        raise ValueError('Неверный идентификатор результата')
    folder = store.root / ('storyboards' if kind == 'concat' else 'edl_renders')
    receipt = folder / (artifact_id + ('.json' if kind == 'concat' else '.state'))
    if not receipt.is_file():
        raise ValueError('Квитанция результата не найдена')
    data = json.loads(receipt.read_text(encoding='utf-8'))
    if kind == 'edl' and (data.get('state') != 'completed' or data.get('draft') is not False):
        raise ValueError('Для приёмки фильма требуется завершённый чистовой экспорт')
    path = folder / (artifact_id + '.mp4')
    if not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Результат находится вне хранилища Studio')
    return {'state': 'completed', 'output_path': str(path),
            'output_sha256': data.get('output_sha256'), 'artifact_kind': kind}
