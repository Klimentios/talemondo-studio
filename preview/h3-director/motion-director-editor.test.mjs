import test from 'node:test';
import assert from 'node:assert/strict';
import { editedMotionPlan, reorderedSegments, receiptLine } from './motion-director-editor.mjs';

const segments = [
  { id: 'a', mode: 'r2v', prompt: 'A', duration: 5, pictures: [{ asset_id: 'x', image_file: 'x.png' }], visual_continuity: false, audio_continuity: false },
  { id: 'b', mode: 't2v', prompt: 'B', duration: 5, pictures: [], visual_continuity: true, audio_continuity: true },
];

test('moving a continuing segment first drops its continuity; copies keep refs with a fresh id', () => {
  const up = reorderedSegments(segments, 'up', 1);
  assert.deepEqual(up.map(s => s.id), ['b', 'a']);
  assert.equal(up[0].visual_continuity, false);
  assert.equal(up[0].audio_continuity, false);
  const copied = reorderedSegments(reorderedSegments(segments, 'copy', 0), 'copy', 0);
  assert.deepEqual(copied.map(s => s.id), ['a', 'a-3', 'a-2', 'b']);
  assert.deepEqual(copied[1].pictures, segments[0].pictures);
  assert.notEqual(copied[1].pictures, segments[0].pictures);
  assert.deepEqual(reorderedSegments([segments[0]], 'remove', 0).map(s => s.id), ['a']);
  assert.deepEqual(reorderedSegments(segments, 'remove', 0).map(s => s.id), ['b']);
});

test('edited plan follows the new order and the edited field values', () => {
  const order = reorderedSegments(segments, 'down', 0);
  const plan = editedMotionPlan({ seed: 1, steps: 25, width: 640, height: 1120, segments }, {
    seed: '9', steps: '30', order, segments: [{ prompt: 'B2', duration: 4, visual_continuity: false, audio_continuity: false },
      { prompt: 'A2', duration: 5, visual_continuity: true, audio_continuity: false }] });
  assert.deepEqual(plan.segments.map(s => [s.id, s.prompt]), [['b', 'B2'], ['a', 'A2']]);
  assert.equal(plan.seed, 9);
  assert.equal(plan.segments[1].pictures[0].asset_id, 'x');
});

test('receipt line shows what was regenerated and GPU time', () => {
  assert.equal(receiptLine({ revision: 4, state: 'completed', metrics_json: JSON.stringify({
    run_selection: ['c'], gpu_seconds: 600, gpu_seconds_per_video_second: 120 }) }),
    'Версия 4 · completed · перегенерированы: c · GPU 10 мин (120 с на секунду видео)');
  assert.equal(receiptLine({ revision: 1, state: 'queued_local', metrics_json: null }), 'Версия 1 · queued_local');
});

test('receipt line keeps only the exception text of a ComfyUI failure', () => {
  const error = "Ошибка исполнения Motion Director: [['execution_error', {'node_type': 'MiniMaxH3MotionDirector', 'exception_message': 'Motion Director: torchaudio is required for audio context.\\n', 'traceback': ['x']}]]";
  assert.equal(receiptLine({ revision: 2, state: 'failed', error }),
    'Версия 2 · failed · Motion Director: torchaudio is required for audio context.');
});

test('receipt line also reads a double-quoted exception text', () => {
  const error = `Ошибка: [['execution_error', {'exception_message': "unsupported operand type(s) for //: 'str' and 'int'\\n", 'traceback': []}]]`;
  assert.equal(receiptLine({ revision: 1, state: 'failed', error }), "Версия 1 · failed · unsupported operand type(s) for //: 'str' and 'int'");
});
