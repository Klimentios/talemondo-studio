import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { officialPromptSettings } from './official-prompt-settings.mjs';

test('official prompt keeps camera controls and Studio direction while avoiding a second Qwen pass', () => {
  const camera = { version: 2, keys: [{ t: 0 }, { t: 15 }] };
  const optics = { focal_length_mm: 50 };
  const result = officialPromptSettings({ camera_state: camera, camera_optics: optics,
    director_config: { style: 'cinematic' }, emotional_direction: true, steps: 24 });
  assert.equal(result.camera_state, camera);
  assert.equal(result.camera_optics, optics);
  assert.deepEqual(result.director_config, { style: 'cinematic' });
  assert.equal(result.emotional_direction, false);
  assert.equal(result.steps, 24);
});

test('different written shots resolve their cameras without sharing a trajectory or changing other controls', () => {
  const original = { camera_state: { version: 2, keys: [] }, camera_optics: { focal_length_mm: 85 },
    director_config: { movement: { id: 'movement.push' }, shot: { id: 'shot.close' },
      acting: { id: 'acting.physical_theatre' } }, style_adapter: 'combat', steps: 30 };
  const result = officialPromptSettings(original, '[Shot 1] A push in. [Shot 2] A cut to an orbit.');
  assert.equal(result.camera_state, null); assert.equal(result.camera_optics, null);
  assert.deepEqual(result.director_config, { acting: { id: 'acting.physical_theatre' } });
  assert.equal(result.style_adapter, 'combat'); assert.equal(result.steps, 30);
  assert.ok(original.director_config.movement);
  assert.equal(officialPromptSettings(original, '[Shot 1] start [Shot 1] sound').camera_state, original.camera_state);
});
