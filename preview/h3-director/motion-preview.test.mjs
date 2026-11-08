import test from 'node:test';
import assert from 'node:assert/strict';
import { MOTION_VARIANTS, motionVariant } from './motion-preview.mjs';
import { DIRECTOR_PRESETS } from './director-presets.mjs';

test('all built-in camera moves have a local category preview', () => {
  const movements = DIRECTOR_PRESETS.filter((preset) => preset.category === 'movement');
  const cameraMoves = movements.filter((preset) => preset.parameters.camera_move);
  assert.equal(cameraMoves.length, 60);
  assert.equal(movements.length, 70);
  assert.deepEqual(movements.slice(0, 10).map((preset) => motionVariant(preset.id)), Object.keys(MOTION_VARIANTS));
  assert.ok(cameraMoves.every((preset) => Object.hasOwn(MOTION_VARIANTS, motionVariant(preset.id))));
  assert.equal(motionVariant('lens.35'), null);
  assert.equal(motionVariant('movement.unknown'), null);
});
