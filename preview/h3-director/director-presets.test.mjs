import test from 'node:test';
import assert from 'node:assert/strict';
import { DIRECTOR_CATEGORIES, DIRECTOR_PRESETS, compileDirectorState, getPreset, validateDirectorState, displayDirectorPreset } from './director-presets.mjs';
import { normalizeDirectorParameters } from './director-config.mjs';
import { existsSync } from 'node:fs';

test('every visible preset has a real category, prompt and structured parameters', () => {
  const categories = new Set(DIRECTOR_CATEGORIES.map(([key]) => key));
  const ids = new Set();
  for (const preset of DIRECTOR_PRESETS) {
    assert.ok(categories.has(preset.category));
    assert.ok(preset.prompt.length > 20);
    assert.ok(Object.keys(preset.parameters).length > 0);
    assert.ok(!ids.has(preset.id)); ids.add(preset.id);
  }
});

test('director compilation preserves user action and adds only selected category cues', () => {
  const action = 'NOVA enters the room, pauses, then speaks.';
  const compiled = compileDirectorState(action, { lens: 'lens.50', lighting: 'lighting.golden', movement: 'movement.push' });
  assert.ok(compiled.startsWith(action));
  assert.match(compiled, /50 mm lens/);
  assert.match(compiled, /warm sun/);
  assert.match(compiled, /dolly-in/);
  assert.equal(compileDirectorState(action, { lens: 'invalid' }), action);
  assert.equal(compileDirectorState('', { lens: 'lens.50' }), '');
});

test('custom presets remain selectable and a category mismatch cannot inject another cue', () => {
  const custom = [{ id: 'custom.1', category: 'lighting', name: 'NOVA night', prompt: 'Soft blue practical light.', parameters: { user_defined: true } }];
  assert.equal(getPreset('custom.1', custom)?.name, 'NOVA night');
  assert.match(compileDirectorState('Scene', { lighting: 'custom.1' }, custom), /Soft blue practical light/);
  assert.equal(compileDirectorState('Scene', { color: 'custom.1' }, custom), 'Scene');
});

test('incompatible deep focus and macro gives an explicit warning', () => {
  assert.equal(validateDirectorState({ focus: 'focus.deep', lens: 'lens.macro' }).length, 1);
  assert.equal(validateDirectorState({ focus: 'focus.eyes', lens: 'lens.85' }).length, 0);
});

test('new shot and focus grammar has distinct illustrations and survives server payload normalization', () => {
  const ids = ['shot.two_shot', 'shot.point_of_view', 'shot.insert', 'shot.overhead', 'shot.dutch',
    'focus.foreground', 'focus.background', 'focus.near_to_far', 'focus.far_to_near'];
  const seen = new Set();
  for (const id of ids) {
    const preset = getPreset(id);
    assert.ok(preset?.image, `missing image for ${id}`);
    assert.ok(existsSync(new URL(preset.image, import.meta.url)), `image file missing for ${id}`);
    assert.ok(!seen.has(preset.image), `duplicate image for ${id}`);
    seen.add(preset.image);
    assert.deepEqual(normalizeDirectorParameters(preset.category, preset.parameters), preset.parameters,
      `server payload would drop a value from ${id}`);
  }
  assert.deepEqual(getPreset('focus.near_to_far').parameters, {
    behavior: 'rack', from: 'foreground', to: 'background', depth: 'shallow',
  });
});

test('every shot and focus card has a concise Russian explanation without changing its generation cue', () => {
  for (const preset of DIRECTOR_PRESETS.filter(({ category }) => category === 'shot' || category === 'focus')) {
    assert.ok(preset.descriptionRu?.length > 20, `missing Russian explanation for ${preset.id}`);
    assert.ok(preset.descriptionRu.length < 100, `explanation too long for ${preset.id}`);
    assert.equal(preset.description, preset.prompt);
  }
});
// Catalog descriptions are UI copy; changing them must not change the H3 instruction.
test('movement catalog has Russian descriptions and saved legacy presets remain readable', () => {
  for (const preset of DIRECTOR_PRESETS.filter((item) => item.category === 'movement')) {
    const display = displayDirectorPreset(preset, 'ru');
    assert.match(display.description, /[а-яё]/i, preset.id);
    if (!preset.parameters.camera_move) assert.equal(preset.legacy, true, preset.id);
  }
  const saved = getPreset('movement.side');
  assert.equal(saved.parameters.movement, 'truck');
  assert.match(displayDirectorPreset(saved, 'ru').description, /Прежний/);
});
