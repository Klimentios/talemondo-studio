import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyAppliedScene } from './ui-bridge-model.mjs';

test('agent scene is verified against the actual canvas before claiming success', () => {
  const scene = { title: 'Начало', shots: [{ title: 'Вход', prompt: ' NOVA enters ', duration: 5,
    framing: 'wide', lens_mm: 35, camera_motion: 'static' }] };
  const board = { active_scene: 's1', scenes: [{ id: 's1', title: 'Начало' }],
    shots: [{ title: 'Вход', prompt: 'NOVA enters', duration: 5, framing: 'wide', lens_mm: 35,
      camera_motion: 'static' }] };
  assert.deepEqual(verifyAppliedScene(scene, board), { ok: true, differences: [] });
  const changed = { ...board, shots: [{ ...board.shots[0], lens_mm: 50 }] };
  assert.deepEqual(verifyAppliedScene(scene, changed), { ok: false, differences: ['шот 1: lens_mm'] });
});
