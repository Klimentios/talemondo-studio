import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Colours live in tokens.css only. Page CSS may use var(--k-*) and the old alias names,
// but must not define them again: that is how the Studio ended up with eight palettes.
const root = dirname(fileURLToPath(import.meta.url));
const PAGES = ['index.html', 'audio-studio.html',
  'editor/index.html', 'franchise-board/index.html', 'franchise-profile.html', 'agent-chat.html', 'analytics/index.html'];
const tokens = readFileSync(join(root, 'tokens.css'), 'utf8');
const tokenNames = new Set([...tokens.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
const cssFiles = ['', 'editor', 'analytics']
  .flatMap((dir) => readdirSync(join(root, dir)).filter((f) => f.endsWith('.css')).map((f) => join(dir, f)))
  .filter((f) => f !== 'tokens.css');

test('no stylesheet redefines a token or one of its aliases', () => {
  const redefined = [];
  for (const file of cssFiles) {
    const css = readFileSync(join(root, file), 'utf8');
    for (const [, name] of css.matchAll(/(?<![\w-])(--[\w-]+)\s*:/g)) if (tokenNames.has(name)) redefined.push(`${file}: ${name}`);
  }
  assert.deepEqual(redefined, []);
});

for (const page of PAGES) {
  test(`${page} loads tokens.css first and components.css`, () => {
    const html = readFileSync(join(root, page), 'utf8');
    const sheets = [...html.matchAll(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)].map((m) => m[1])
      .filter((href) => !href.startsWith('https://'));
    assert.match(sheets[0] || '', /tokens\.css/);
    assert.ok(sheets.some((href) => /components\.css/.test(href)), 'components.css missing');
  });
}
