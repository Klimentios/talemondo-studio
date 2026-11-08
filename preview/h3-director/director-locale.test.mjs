import test from 'node:test';
import assert from 'node:assert/strict';
import { DIRECTOR_CATEGORIES, DIRECTOR_PRESETS, displayDirectorCategory, displayDirectorPreset } from './director-presets.mjs';
import { DIRECTOR_PROFILES, displayDirectorProfile } from './director-profiles.mjs';
import { PALETTE_CATALOG, displayPalette } from './assets/palettes/catalog.mjs';
import { formatParameters } from './director-labels.mjs';

const cyrillic = /[А-Яа-яЁё]/;

test('every built-in director card has complete English display copy', () => {
  for (const [key] of DIRECTOR_CATEGORIES) {
    const label = displayDirectorCategory(key, 'en');
    assert.ok(!cyrillic.test(label.title) && !cyrillic.test(label.description));
  }
  for (const preset of DIRECTOR_PRESETS) {
    const en = displayDirectorPreset(preset, 'en-US');
    assert.ok(en.title && !cyrillic.test(en.title), `${preset.id}: English title`);
    assert.ok(en.description && !cyrillic.test(en.description), `${preset.id}: English description`);
    if (preset.spec) assert.ok(en.spec && !cyrillic.test(en.spec), `${preset.id}: English spec`);
    assert.equal(displayDirectorPreset(preset, 'ru').title, preset.name);
  }
});

test('curated profiles and palettes use locale without changing IDs or generation parameters', () => {
  for (const profile of DIRECTOR_PROFILES) {
    assert.ok(!cyrillic.test(displayDirectorProfile(profile, 'en')));
    assert.equal(displayDirectorProfile(profile, 'ru'), profile.name);
  }
  for (const palette of PALETTE_CATALOG) {
    const en = displayPalette(palette, 'en');
    assert.ok(!cyrillic.test(en.title) && !cyrillic.test(en.description));
    assert.equal(displayPalette(palette, 'ru').title, palette.title);
    assert.equal(palette.config.swatches.length, 8);
  }
});

test('director parameter labels render in chosen language while values remain unchanged', () => {
  const params = { focal_length_mm: 40, look: 'natural' };
  assert.match(formatParameters('lens', params, 'en'), /Focal length: 40 mm/);
  assert.match(formatParameters('lens', params, 'ru'), /Фокусное расстояние, мм: 40 мм/);
  assert.deepEqual(params, { focal_length_mm: 40, look: 'natural' });
});
