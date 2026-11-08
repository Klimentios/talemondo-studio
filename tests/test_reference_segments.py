import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_studio.scene_evidence import video_evidence
from local_studio.reference_segments import SegmentRequest, reference_segment
from local_studio.store import StudioStore, _media_duration_seconds


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ReferenceSegmentTests(unittest.TestCase):
    def test_later_shot_uses_later_source_frames_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StudioStore(Path(directory))
            source = store.assets_dir / 'source.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=red:s=32x32:r=24:d=10',
                            '-f', 'lavfi', '-i', 'color=blue:s=32x32:r=24:d=10', '-filter_complex',
                            '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264',
                            '-pix_fmt', 'yuv420p', str(source)], check=True)
            frames, cuts = video_evidence(source, 20, 6, 1)
            self.assertEqual(len(frames), 6)
            self.assertTrue(any(abs(moment - 10) < 0.1 for moment in cuts))
            self.assertEqual([frame['seconds'] for frame in frames], sorted(frame['seconds'] for frame in frames))
            self.assertTrue(all(frame['video_number'] == 1 for frame in frames))
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            asset = store.add_asset(kind='video', role='Движение', original_name='source.mp4',
                                    mime='video/mp4', size=source.stat().st_size, sha256=digest, path=source)
            request = SegmentRequest(start=10, duration=5)
            segment = reference_segment(store, asset['asset_id'], request)
            pixel = subprocess.run(['ffmpeg', '-v', 'error', '-i', segment['path'], '-frames:v', '1',
                                    '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                   capture_output=True, check=True).stdout
            self.assertGreater(pixel[2], 200)
            self.assertLess(pixel[0], 30)
            self.assertAlmostEqual(_media_duration_seconds(segment['path']), 5, delta=0.1)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            self.assertEqual(reference_segment(store, asset['asset_id'], request)['asset_id'], segment['asset_id'])
            with self.assertRaisesRegex(ValueError, 'выходит за длительность'):
                reference_segment(store, asset['asset_id'], SegmentRequest(start=18, duration=5))

            Path(segment['path']).write_bytes(b'corrupt cache')
            with self.assertRaisesRegex(ValueError, 'участок изменился'):
                reference_segment(store, asset['asset_id'], request)
            source.write_bytes(b'changed source')
            with self.assertRaisesRegex(ValueError, 'Исходное видео изменилось'):
                reference_segment(store, asset['asset_id'], request)
