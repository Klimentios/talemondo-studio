import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio.reference_excerpts import make_router
from local_studio.store import StudioStore, _media_duration_seconds


def test_real_video_audio_excerpt_api_and_immutable_cache():
    assert shutil.which('ffmpeg') and shutil.which('ffprobe')
    with tempfile.TemporaryDirectory() as directory:
        store = StudioStore(Path(directory))
        video = store.assets_dir / 'colors.mp4'
        audio = store.assets_dir / 'sound.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=32x32:r=24:d=3',
                        '-f','lavfi','-i','color=blue:s=32x32:r=24:d=3','-filter_complex',
                        '[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=24000:cl=mono:d=3',
                        '-f','lavfi','-i','sine=frequency=880:sample_rate=24000:duration=3','-filter_complex',
                        '[0:a][1:a]concat=n=2:v=0:a=1[a]','-map','[a]',str(audio)],check=True)
        assets = []
        for path,kind,role in [(video,'video','Движение'),(audio,'audio','Аудио')]:
            assets.append(store.add_asset(kind=kind,role=role,original_name=path.name,
                mime='video/mp4' if kind=='video' else 'audio/wav',size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),path=path))
        app = FastAPI(); app.include_router(make_router(store))
        with TestClient(app) as client:
            clips = []
            for asset in assets:
                url = '/api/assets/'+asset['asset_id']
                assert client.get(url+'/excerpt-info').json()['duration'] >= 5.9
                response = client.post(url+'/excerpt',json={'start':3,'duration':2})
                assert response.status_code == 200, response.text
                clip=response.json(); clips.append(clip)
                assert abs(_media_duration_seconds(clip['path'])-2) < .1
                assert clip['asset_id'] != asset['asset_id']
                assert client.post(url+'/excerpt',json={'start':3,'duration':2}).json()['asset_id']==clip['asset_id']
                assert hashlib.sha256(Path(asset['path']).read_bytes()).hexdigest()==asset['sha256']
                assert client.post(url+'/excerpt',json={'start':9,'duration':2}).status_code==422
            pixel=subprocess.run(['ffmpeg','-v','error','-i',clips[0]['path'],'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
            assert pixel[2]>200 and pixel[0]<30  # Selected blue segment, not original red beginning.
            samples=subprocess.run(['ffmpeg','-v','error','-i',clips[1]['path'],'-f','s16le','-ac','1','-'],capture_output=True,check=True).stdout
            assert np.abs(np.frombuffer(samples,dtype=np.int16).astype(float)).mean()>1000
            Path(clips[0]['path']).write_bytes(b'broken cache')
            assert client.post('/api/assets/'+assets[0]['asset_id']+'/excerpt',json={'start':3,'duration':2}).status_code==422
