from pathlib import Path

import pytest

from local_studio.assembly_receipt import assembly_snapshot, verify_sources


def test_input_version_tracks_content_and_recipe_not_storage_location(tmp_path):
    first, copy = tmp_path / 'first.mp4', tmp_path / 'copy.mp4'
    first.write_bytes(b'original')
    copy.write_bytes(b'original')
    paths = {'job:one': first}
    snapshot = assembly_snapshot(paths, {'trim_frames': [2]})
    assert snapshot == assembly_snapshot({'job:one': copy}, {'trim_frames': [2]})
    assert snapshot != assembly_snapshot(paths, {'trim_frames': [3]})
    verify_sources(paths, snapshot)
    first.write_bytes(b'replaced')
    with pytest.raises(ValueError, match='job:one'):
        verify_sources(paths, snapshot)
    with pytest.raises(ValueError, match='Состав'):
        verify_sources({}, snapshot)


def test_edl_cannot_treat_changed_render_as_a_new_valid_source(tmp_path):
    from types import SimpleNamespace
    from local_studio.edl import Source, resolve
    from local_studio.assembly_receipt import file_sha256

    path = tmp_path / 'take.mp4'
    path.write_bytes(b'original')
    job = {'state': 'completed', 'output_path': str(path), 'output_sha256': file_sha256(path)}
    store = SimpleNamespace(results_dir=tmp_path, get_job=lambda _: job)
    assert resolve(store, Source(job_id='j1')) == path.resolve()
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='изменён'):
        resolve(store, Source(job_id='j1'))
