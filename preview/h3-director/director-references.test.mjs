import test from 'node:test';
import assert from 'node:assert/strict';
import { presetPhotoReferences, colorGradeForState } from './director-references.mjs';
import { rgbToLab, labStatsFromPixels } from './photo-analyze.mjs';

const light = { id: 'custom.l', category: 'lighting', imageAssetId: 'asset-light' };
const color = { id: 'custom.c', category: 'color', imageAssetId: 'asset-color', image: '/api/assets/asset-color/file', parameters: { grade_strength: 0.7 } };

test('preset photos become numbered image references with their role', () => {
  const existing = [{ id: 'hero', kind: 'image', number: 1 }, { id: 'clip', kind: 'video', number: 1 }];
  const extra = presetPhotoReferences({ lighting: 'custom.l', color: 'custom.c' }, [light, color], existing);
  assert.deepEqual(extra.map((r) => [r.id, r.number, r.role]), [['asset-light', 2, 'Свет'], ['asset-color', 3, 'Цвет']]);
});

test('no duplicates, no missing assets, H3 limit of 9 images respected', () => {
  assert.deepEqual(presetPhotoReferences({ lighting: 'custom.l' }, [light], [{ id: 'asset-light', kind: 'image', number: 1 }]), []);
  assert.deepEqual(presetPhotoReferences({ lighting: 'custom.l' }, [light], [], () => false), []);
  const nine = Array.from({ length: 9 }, (_, i) => ({ id: `i${i}`, kind: 'image', number: i + 1 }));
  assert.deepEqual(presetPhotoReferences({ lighting: 'custom.l' }, [light], nine), []);
});

test('color grade uses Lab stats of the preset photo and its strength; 0 disables', async () => {
  const pixels = { width: 1, height: 1, data: new Uint8ClampedArray([255, 255, 255, 255]) };
  const grade = await colorGradeForState({ color: 'custom.c' }, [color], async () => pixels);
  assert.equal(grade.strength, 0.7);
  assert.ok(Math.abs(grade.target_mean[0] - 100) < 0.1);
  const off = await colorGradeForState({ color: 'custom.c' }, [{ ...color, parameters: { grade_strength: 0 } }], async () => pixels);
  assert.equal(off, null);
});

test('Lab conversion matches reference values', () => {
  const [L, a, b] = rgbToLab(1, 0, 0);
  assert.ok(Math.abs(L - 53.24) < 0.05 && Math.abs(a - 80.09) < 0.1 && Math.abs(b - 67.2) < 0.1);
  const stats = labStatsFromPixels({ width: 2, height: 1, data: new Uint8ClampedArray([0, 0, 0, 255, 255, 255, 255, 255]) });
  assert.ok(Math.abs(stats.mean[0] - 50) < 0.1 && Math.abs(stats.std[0] - 50) < 0.1);
});

test('built-in palette without explicit strength does not grade', async () => {
  const pixels = { width: 1, height: 1, data: new Uint8ClampedArray([10, 20, 30, 255]) };
  const builtin = { id: 'color.x', category: 'color', image: './assets/palettes/x.jpg', parameters: {} };
  assert.equal(await colorGradeForState({ color: 'color.x' }, [builtin], async () => pixels), null);
  const numbered = presetPhotoReferences({ lighting: 'custom.l' }, [light], [{ id: 'a', kind: 'image', number: 4 }]);
  assert.equal(numbered[0].number, 5);
});

test('lighting without photo asks for a rendered reference; photo or opt-out does not', async () => {
  const { lightReferenceRequest, withLightReferenceAsset } = await import('./director-references.mjs');
  const builtin = { id: 'lighting.golden', category: 'lighting', parameters: { source: 'sun', temperature: 'warm' } };
  const request = lightReferenceRequest({ lighting: 'lighting.golden' }, [builtin]);
  assert.match(request.key, /source=sun/);
  assert.equal(lightReferenceRequest({ lighting: 'custom.l' }, [light]), null);
  assert.equal(lightReferenceRequest({ lighting: 'x' }, [{ ...builtin, id: 'x', parameters: { reference_image: false } }]), null);
  const presets = withLightReferenceAsset([builtin], 'lighting.golden', 'asset-render');
  const extra = presetPhotoReferences({ lighting: 'lighting.golden' }, presets, []);
  assert.deepEqual(extra.map((r) => [r.id, r.role]), [['asset-render', 'Свет']]);
});

test('camera preset becomes film look; digital clean camera does not', async () => {
  const { filmLookForState } = await import('./director-references.mjs');
  const film = [{ id: 'custom.f', category: 'camera', parameters: { capture: 'film16', contrast: 'high' } }];
  assert.deepEqual(filmLookForState({ camera: 'custom.f' }, film, 7), { contrast: 0.22, grain: 0.5, grain_size: 2, seed: 7 });
  const digital = [{ id: 'custom.d', category: 'camera', parameters: { capture: 'digital' } }];
  assert.equal(filmLookForState({ camera: 'custom.d' }, digital, 1), null);
});
