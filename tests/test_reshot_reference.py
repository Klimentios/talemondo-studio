"""The Studio adapter stores one immutable motion reference and never starts an H3 job."""
import hashlib
import subprocess
from pathlib import Path

from local_studio.reshot_reference import MotionReferenceRequest, prepare_motion_reference
from local_studio.store import StudioStore


def test_motion_reference_is_cached_without_changing_source(tmp_path):
    store = StudioStore(tmp_path)
    source = store.assets_dir / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=24:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    asset = store.add_asset(kind='video', role='Исходное видео', original_name='source.mp4', mime='video/mp4',
                            size=source.stat().st_size, sha256=digest, path=source)
    fake = tmp_path / 'fake-reshot'
    fake.write_text('#!/bin/sh\ncp "$1" "$3"\n', encoding='utf-8')
    fake.chmod(0o755)

    first = prepare_motion_reference(store, asset['asset_id'], MotionReferenceRequest(control='depth'), binary=fake)
    second = prepare_motion_reference(store, asset['asset_id'], MotionReferenceRequest(control='depth'), binary=fake)
    assert first['asset_id'] == second['asset_id']
    assert first['role'] == 'Движение и камера' and second['motion_reference']['cached'] is True
    assert Path(first['path']).is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == digest
