import test from 'node:test';
import assert from 'node:assert/strict';
import { draftChecklist, newAudioProject, timelineRows } from './audio-studio-model.mjs';

test('video draft distinguishes unselected source from ready speech and retains original track', () => {
  const p = newAudioProject();
  assert.ok(draftChecklist(p).includes('выбрать исходное видео'));
  p.source = { kind: 'job', id: 'source' };
  p.lines[0].text = 'Привет';
  assert.deepEqual(draftChecklist(p), []);
  assert.deepEqual(timelineRows(p).map((row) => row.type), ['source', 'speech']);
});

test('reference voice and lip sync require explicit attachments and target', () => {
  const p = newAudioProject();
  p.mode = 'audio';
  p.lines[0].voice_mode = 'reference';
  p.lines[0].text = 'Реплика';
  assert.ok(draftChecklist(p).includes('прикрепить голосовой референс'));
  p.lines[0].voice_asset_id = 'sample';
  assert.deepEqual(draftChecklist(p), []);
});

test('an empty voice card does not block an audio-only mix', () => {
  const p = newAudioProject();
  p.mode = 'audio';
  p.lines[0].voice_mode = 'reference';
  p.mix.effects_asset_id = 'effect-1';
  assert.deepEqual(draftChecklist(p), []);
});

test('music controls are retained and invalid values block submission', () => {
  const p = newAudioProject();
  p.mode = 'audio';
  p.mix.music_prompt = 'Warm piano';
  p.mix.music_bpm = 96;
  p.mix.music_keyscale = 'Am';
  p.mix.music_timesignature = '4';
  p.mix.music_seed = 42;
  assert.deepEqual(draftChecklist(p), []);
  p.mix.music_bpm = 301;
  assert.ok(draftChecklist(p).includes('темп музыки от 30 до 300 BPM'));
});
