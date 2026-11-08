import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzePixels, colorName } from './photo-analyze.mjs';
import { normalizeDirectorParameters } from './director-config.mjs';

function image(width, height, fn) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const [r, g, b] = fn(x / width, y / height); const i = (y * width + x) * 4;
    data[i] = r; data[i + 1] = g; data[i + 2] = b; data[i + 3] = 255;
  }
  return { data, width, height };
}

test('warm side-lit photo → warm light from the side', () => {
  const p = analyzePixels('lighting', image(120, 80, (x) => { const v = 30 + 200 * x; return [v, v * 0.8, v * 0.55]; }));
  assert.equal(p.temperature, 'warm');
  assert.equal(p.direction, 'side');
});

test('bright rim around a dark center → backlight with rim', () => {
  const p = analyzePixels('lighting', image(120, 80, (x, y) => (Math.abs(x - 0.5) < 0.22 && Math.abs(y - 0.5) < 0.3 ? [25, 25, 30] : [235, 225, 210])));
  assert.equal(p.direction, 'back');
  assert.equal(p.source, 'backlight');
  assert.equal(p.rim, true);
});

test('flat cool grey → overcast, low contrast', () => {
  const p = analyzePixels('lighting', image(60, 40, (x) => [120 + 20 * x, 130 + 20 * x, 150 + 20 * x]));
  assert.equal(p.contrast, 'low');
  assert.equal(p.temperature, 'cool');
  assert.equal(p.source, 'overcast');
});

test('every analysed category passes schema validation unchanged', () => {
  const img = image(80, 60, (x, y) => [40 + 180 * x, 60 + 120 * y, 200 - 150 * x]);
  for (const category of ['lighting', 'color', 'focus', 'camera', 'style']) {
    const p = analyzePixels(category, img);
    assert.deepEqual(normalizeDirectorParameters(category, p), p, category);
  }
  assert.deepEqual(analyzePixels('lens', img), {});
});

test('color names are short English words the server accepts', () => {
  assert.match(colorName([0.05, 0.1, 0.3]), /^[a-z ]+$/);
  assert.equal(colorName([0.95, 0.9, 0.78]), 'bright warm cream');
});
