"""Bounded temporal previews; never modify or replace the H3 reference video."""
import base64
import shutil
import subprocess
from pathlib import Path


def video_frames(path: Path, duration: float) -> list[dict]:
    # Six observations, including opening and ending; this is semantic analysis,
    # not pose extraction. The original video still reaches the native Ref2VA input.
    ffmpeg = shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
    times = [round(max(0, duration - 0.1) * i / 5, 3) for i in range(6)]
    frames = []
    for timestamp in times:
        result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-ss', str(timestamp),
            '-i', str(path), '-frames:v', '1', '-vf', "scale='min(512,iw)':-2", '-q:v', '4',
            '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'], capture_output=True, timeout=30, check=False)
        if result.returncode or not result.stdout:
            raise ValueError(f'Не удалось прочитать видео на {timestamp} с')
        frames.append({'time_seconds': timestamp, 'jpeg_base64': base64.b64encode(result.stdout).decode('ascii')})
    return frames
