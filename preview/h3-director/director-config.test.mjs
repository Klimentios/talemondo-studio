import test from 'node:test';
import assert from 'node:assert/strict';
import { directorConfigForState, directorOptionLabel, directorParameterSummary, normalizeDirectorParameters } from './director-config.mjs';
import { DIRECTOR_PRESETS } from './director-presets.mjs';
import { PALETTE_CATALOG } from './assets/palettes/catalog.mjs';

test('selected presets produce category-specific structured server payload', () => {
  const state = { lens: 'lens.35', movement: 'movement.orbit', color: 'color.teal_orange' };
  const config = directorConfigForState(state, [], DIRECTOR_PRESETS);
  assert.equal(config.lens.parameters.focal_length_mm, 35);
  assert.equal(config.movement.parameters.movement, 'orbit');
  assert.equal(config.color.parameters.swatches.length, 8);
  assert.deepEqual(Object.keys(config).sort(), ['color', 'lens', 'movement']);
});

test('Auto removes the category and custom values are constrained', () => {
  const custom = [{ id: 'custom.11111111-1111-4111-8111-111111111111', category: 'lens', parameters: {
    focal_length_mm: 42, aperture_f: 2.4, look: 'natural', bogus: 'ignored' } }];
  const config = directorConfigForState({ lens: custom[0].id }, custom, DIRECTOR_PRESETS);
  assert.deepEqual(config.lens.parameters, { focal_length_mm: 42, aperture_f: 2.4, look: 'natural' });
  assert.deepEqual(directorConfigForState({}, custom, DIRECTOR_PRESETS), {});
  assert.deepEqual(normalizeDirectorParameters('lens', { focal_length_mm: 900, aperture_f: -1, look: 'hack' }), {});
});

test('palette only carries validated hex colors', () => {
  assert.deepEqual(normalizeDirectorParameters('color', {
    temperature: 'split', swatches: ['#112233', 'red', '#AABBCC'], unwanted: 'x',
  }), { temperature: 'split', swatches: ['#112233', '#AABBCC'] });
});

test('operator angle choices keep server keys and readable Russian labels', () => {
  assert.deepEqual(normalizeDirectorParameters('shot', { framing: 'two_shot', angle: 'overhead' }),
    { framing: 'two_shot', angle: 'overhead' });
  assert.equal(directorOptionLabel('overhead'), 'Сверху');
  assert.equal(directorOptionLabel('point_of_view'), 'От первого лица');
  assert.equal(directorParameterSummary('shot', { framing: 'two_shot', angle: 'overhead' }),
    'Крупность: Двое в кадре · Ракурс: Сверху');
});

test('built-in creative settings survive normalization; camera body facts do not become controls', () => {
  const style = DIRECTOR_PRESETS.find((preset) => preset.id === 'style.neon_thriller');
  assert.deepEqual(normalizeDirectorParameters('style', style.parameters), style.parameters);
  const rack = DIRECTOR_PRESETS.find((preset) => preset.id === 'focus.rack');
  assert.deepEqual(normalizeDirectorParameters('focus', rack.parameters), rack.parameters);
  const camera = DIRECTOR_PRESETS.find((preset) => preset.id === 'camera.alexa35');
  const result = normalizeDirectorParameters('camera', camera.parameters);
  assert.equal(result.reference_camera, undefined);
  assert.equal(result.sensor_width_mm, undefined);
});

test('curated palette stills retain matching swatches and full server config', () => {
  assert.equal(PALETTE_CATALOG.length, 12);
  assert.equal(new Set(PALETTE_CATALOG.map((entry) => entry.image)).size, 12);
  for (const entry of PALETTE_CATALOG) {
    const preset = DIRECTOR_PRESETS.find((item) => item.id === `color.${entry.id}`);
    assert.ok(preset?.image);
    assert.deepEqual(normalizeDirectorParameters('color', preset.parameters), entry.config);
    assert.equal(entry.swatches.length, 8);
  }
});

test('lighting recipes keep server controls and illustrated photos remain distinct', () => {
  const lighting = DIRECTOR_PRESETS.filter((preset) => preset.category === 'lighting');
  assert.ok(lighting.length >= 12);
  const illustrated = lighting.filter((preset) => preset.image);
  assert.equal(new Set(illustrated.map((preset) => preset.image)).size, illustrated.length);
  for (const preset of lighting) {
    if (preset.image) assert.ok(preset.image.startsWith('./assets/lighting/'));
    else assert.ok(preset.descriptionRu?.length > 20, `${preset.id} needs an explanation without a photo`);
    assert.deepEqual(normalizeDirectorParameters('lighting', preset.parameters), preset.parameters,
      `${preset.id} has unsupported lighting controls`);
  }
});
