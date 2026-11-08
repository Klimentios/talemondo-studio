import test from 'node:test';
import assert from 'node:assert/strict';
import { extractPaletteFromPixels } from './palette-extract.mjs';

test('extracts ordered distinct colors from opaque image pixels', () => {
  const colors = [[20, 30, 40], [190, 80, 30], [240, 220, 180]];
  const data = new Uint8ClampedArray(colors.flatMap(([r, g, b]) => Array(32).fill([r, g, b, 255]).flat()));
  const palette = extractPaletteFromPixels({ data, width: 96, height: 1 });
  assert.deepEqual(palette, ['#141e28', '#be501e', '#f0dcb4']);
});

test('rejects transparent images and invalid pixel data', () => {
  assert.throws(() => extractPaletteFromPixels({ data: new Uint8ClampedArray(4), width: 2, height: 2 }));
  assert.throws(() => extractPaletteFromPixels({ data: new Uint8ClampedArray(4), width: 1, height: 1 }), /непрозрачных/);
});
