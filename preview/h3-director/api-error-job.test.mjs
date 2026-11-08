import test from 'node:test';
import assert from 'node:assert/strict';
import { jobErrorText } from './api-error.mjs';

test('torch traceback becomes one readable sentence', () => {
  const raw = 'ACE-Step не создала музыку: Traceback (most recent call last): File "/workspace/h3/api/app/audio_acestep_music.py", line 57, in <module> main() File "/x.py", line 14, in <module> import torch ModuleNotFoundError: No module named \'torch\'';
  assert.match(jobErrorText(raw), /PyTorch/);
});

test('unknown traceback keeps only the final error line', () => {
  const raw = 'Traceback (most recent call last):\n  File "/a.py", line 1, in <module>\n    boom()\nValueError: bad frame count 17';
  assert.equal(jobErrorText(raw), 'ValueError: bad frame count 17');
});

test('plain messages pass through', () => {
  assert.equal(jobErrorText('Нет исходного видео'), 'Нет исходного видео');
  assert.equal(jobErrorText(''), '');
});
