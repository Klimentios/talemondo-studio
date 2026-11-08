import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { PALETTE_CATALOG } from './assets/palettes/catalog.mjs';

test('built-in color palettes have individual images, useful swatches and valid config', () => {
  assert.ok(PALETTE_CATALOG.length >= 10);
  const ids = new Set();
  const images = new Set();
  for (const palette of PALETTE_CATALOG) {
    assert.ok(!ids.has(palette.id), `duplicate palette id ${palette.id}`);
    assert.ok(!images.has(palette.image), `reused preview ${palette.image}`);
    ids.add(palette.id);
    images.add(palette.image);
    assert.ok(existsSync(fileURLToPath(new URL(palette.image, import.meta.url))));
    assert.equal(palette.swatches.length, 8);
    assert.ok(palette.swatches.every((hex) => /^#[0-9a-f]{6}$/i.test(hex)));
    assert.ok(['warm','neutral','cool','split'].includes(palette.config.temperature));
    assert.ok(['none','low','natural','moderate','high'].includes(palette.config.saturation));
    assert.ok(['low','medium','high'].includes(palette.config.contrast));
    assert.ok(palette.prompt.length > 50);
  }
});
