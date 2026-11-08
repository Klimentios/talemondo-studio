import test from 'node:test';
import assert from 'node:assert/strict';
import { DIRECTOR_PROFILES } from './director-profiles.mjs';
import { DIRECTOR_CATEGORIES, DIRECTOR_PRESETS } from './director-presets.mjs';

test('ten complete visual profiles reference only existing category presets', () => {
  assert.equal(DIRECTOR_PROFILES.length, 10);
  for (const profile of DIRECTOR_PROFILES) {
    assert.deepEqual(Object.keys(profile.director).sort(), DIRECTOR_CATEGORIES.filter(([key]) => key !== 'acting').map(([key]) => key).sort());
    for (const [category, id] of Object.entries(profile.director)) {
      assert.ok(DIRECTOR_PRESETS.some((preset) => preset.category === category && preset.id === id), `${profile.id}: ${id}`);
    }
  }
  assert.equal(DIRECTOR_PROFILES.find((profile) => profile.id === 'fantasy-3d').mode, 'animation3d');
});
