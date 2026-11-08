import assert from 'node:assert/strict';
import test from 'node:test';
import { guideFramesForProject, officialSettings } from './official-controls.mjs';

const frame = (id, number) => ({ id, number, kind: 'image', role: 'Кадр сцены', name: `Frame ${number}` });

test('a scene frame defaults to frame zero and explicit anchors keep reference numbers', () => {
  const project = { references: [frame('a', 1), frame('b', 2)], guideFrames: null };
  assert.deepEqual(guideFramesForProject(project, 5), [[1, 0]]);
  project.guideFrames = [{ asset_id: 'b', frame_idx: -1 }, { asset_id: 'a', frame_idx: 22 }];
  assert.deepEqual(officialSettings(project, 5).guide_frames, [[2, -1], [1, 22]]);
});

test('continuation reserves the first 22 output frames and validates negative positions', () => {
  const project = { references: [frame('a', 1), { id: 'v', kind: 'video', role: 'Продолжение' }],
    guideFrames: [{ asset_id: 'a', frame_idx: 21 }] };
  assert.throws(() => guideFramesForProject(project, 5), /22/);
  project.guideFrames[0].frame_idx = -124;
  assert.throws(() => guideFramesForProject(project, 5), /22/);
  project.guideFrames[0].frame_idx = -1;
  assert.deepEqual(guideFramesForProject(project, 5), [[1, -1]]);
});
