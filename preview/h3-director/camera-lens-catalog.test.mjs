import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { DIRECTOR_PRESETS, getPreset } from './director-presets.mjs';
import { normalizeDirectorParameters } from './director-config.mjs';

test('added camera and lens catalog has distinct art and supported generation controls', () => {
  const ids = [
    'camera.alexa_mini_lf', 'camera.red_v_raptor_x', 'camera.panavision_dxl2',
    'camera.documentary_rig', 'lens.18', 'lens.40', 'lens.75', 'lens.70_zoom',
  ];
  const images = new Set();
  for (const id of ids) {
    const preset = getPreset(id);
    assert.ok(preset, `missing preset ${id}`);
    assert.ok(preset.descriptionRu?.length > 20, `missing Russian explanation ${id}`);
    assert.ok(preset.image && existsSync(new URL(preset.image, import.meta.url)), `missing image ${id}`);
    assert.ok(!images.has(preset.image), `duplicate image ${id}`);
    images.add(preset.image);
    const normalized = normalizeDirectorParameters(preset.category, preset.parameters);
    assert.ok(Object.keys(normalized).length > 0, `no transmitted control for ${id}`);
    for (const [key, value] of Object.entries(normalized)) {
      assert.deepEqual(value, preset.parameters[key], `altered ${id}.${key}`);
    }
  }
});

test('every camera and lens card has its own available image and Russian explanation', () => {
  const seen = new Set();
  for (const preset of DIRECTOR_PRESETS.filter((item) => ['camera', 'lens'].includes(item.category))) {
    assert.ok(preset.image, `image-less card: ${preset.id}`);
    assert.ok(existsSync(new URL(preset.image, import.meta.url)), `missing file: ${preset.id}`);
    if (!preset.legacy) assert.ok(!seen.has(preset.image), `reused image: ${preset.id}`);
    seen.add(preset.image);
    assert.ok(preset.descriptionRu?.length > 20, `missing Russian explanation: ${preset.id}`);
  }
});

test('camera catalog shows equipment while old camera-look choices remain compatible', () => {
  const legacyIds = ['modern', 'film35', 'film16', 'documentary', 'vintage', 'clean'];
  for (const id of legacyIds) {
    const preset = getPreset(`camera.${id}`);
    assert.equal(preset.legacy, true);
    assert.match(preset.image, /^\.\/assets\/cameras\/(?!camera-(?:modern|film35|film16|documentary|vintage|clean)\.jpg)/);
    assert.ok(Object.keys(normalizeDirectorParameters('camera', preset.parameters)).length > 0);
  }
});
