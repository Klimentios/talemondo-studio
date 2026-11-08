import test from 'node:test';
import assert from 'node:assert/strict';
import { checkShots } from './story-canvas-blockout.mjs';

test('the check reads each shot card; a shot without one gets a plain medium on the heroes its text names', () => {
  const shots = checkShots([
    { duration: 11, prompt: 'x', sceneShot: { framing: 'wide', angle: 'low', lens_mm: 24, camera_motion: 'dolly', characters: ['ALEX', 'NOVA'] } },
    { duration: 30, prompt: 'NOVA jumps onto the sofa while Alex reads.' },
  ], ['ALEX', 'NOVA']);
  assert.deepEqual(shots[0], { duration: 11, framing: 'wide', angle: 'low', lens_mm: 24, camera_motion: 'dolly', characters: ['ALEX', 'NOVA'] });
  assert.deepEqual(shots[1], { duration: 15, framing: 'medium', angle: 'eye', lens_mm: 35, camera_motion: 'static', characters: ['ALEX', 'NOVA'] });
});
