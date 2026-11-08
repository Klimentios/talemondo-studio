import assert from 'node:assert/strict';
import test from 'node:test';
import { newAudioProject, timelineRows } from './audio-studio-model.mjs';
import { placeUploadedAudio, projectForWorkflow } from './audio-studio-workflow.mjs';

test('music task excludes an unfinished voice line while preserving the draft', () => {
  const draft = newAudioProject();
  draft.lines[0].text = 'Скрытая реплика';
  draft.mix.music_prompt = 'Ambient piano';
  draft.source = { kind: 'job', id: 'source-video' };
  const task = projectForWorkflow(draft, 'music');
  assert.deepEqual(task.lines, []);
  assert.equal(task.mode, 'audio');
  assert.equal(task.output_format, 'wav');
  assert.deepEqual(task.source, { kind: 'none', id: '' });
  assert.equal(task.mix.music_prompt, 'Ambient piano');
  assert.equal(draft.lines[0].text, 'Скрытая реплика');
});

test('speech task excludes hidden music and effects', () => {
  const draft = newAudioProject();
  draft.mix.music_prompt = 'Ambient piano';
  draft.mix.music_lyrics = 'Words';
  draft.mix.effects_asset_id = 'effect-1';
  const task = projectForWorkflow(draft, 'speech');
  assert.equal(task.mix.music_prompt, '');
  assert.equal(task.mix.music_lyrics, '');
  assert.equal(task.mix.effects_asset_id, '');
  assert.equal(draft.mix.music_prompt, 'Ambient piano');
});

test('an unfinished voice card never enters the submitted mix', () => {
  const draft = newAudioProject();
  draft.lines[0].voice_mode = 'reference';
  draft.mix.effects_asset_id = 'effect-1';
  assert.deepEqual(projectForWorkflow(draft, 'mix').lines, []);
  assert.equal(draft.lines.length, 1);
});

test('voice replacement and translated dubbing use existing video with new speech only', () => {
  const draft = newAudioProject();
  draft.source = { kind: 'job', id: 'video-1' };
  draft.lines[0].text = 'Готовая реплика';
  draft.mix.source_level = 1;
  draft.mix.music_prompt = 'Hidden music';
  for (const workflow of ['voice', 'translate']) {
    const task = projectForWorkflow(draft, workflow);
    assert.equal(task.mode, 'video');
    assert.deepEqual(task.source, draft.source);
    assert.equal(task.mix.source_level, 0);
    assert.equal(task.mix.music_prompt, '');
    assert.equal(task.lines.length, 1);
    assert.equal(timelineRows(task).some((row) => row.type === 'source'), false);
  }
  assert.equal(draft.mix.source_level, 1);
});

test('uploaded music is selected only when it does not erase an unfinished song', () => {
  const draft = newAudioProject();
  assert.equal(placeUploadedAudio(draft, 'music', 'take-1'), 'music');
  assert.equal(draft.mix.music_asset_id, 'take-1');
  draft.mix.music_lyrics = 'Незаконченный куплет';
  assert.equal(placeUploadedAudio(draft, 'music', 'take-2'), 'library');
  assert.equal(draft.mix.music_asset_id, 'take-1');
  assert.equal(draft.mix.music_lyrics, 'Незаконченный куплет');
});

test('a voice sample attaches to the only speech line', () => {
  const draft = newAudioProject();
  assert.equal(placeUploadedAudio(draft, 'speech', 'voice-1'), 'voice');
  assert.equal(draft.lines[0].voice_mode, 'reference');
  assert.equal(draft.lines[0].voice_asset_id, 'voice-1');
  draft.lines.push({ ...draft.lines[0], id: 'second' });
  assert.equal(placeUploadedAudio(draft, 'speech', 'voice-2'), 'library');
  assert.equal(draft.lines[0].voice_asset_id, 'voice-1');
});
