import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// An ES module imported under two URLs (e.g. different ?v=) is evaluated twice with
// separate state. Walk each page's import graph and require one URL per file.
const root = dirname(fileURLToPath(import.meta.url));
const PAGES = ['index.html', 'audio-studio.html'];
const IMPORT = /(?:from\s*|import\s*\(\s*)['"](\.\/[^'"]+)['"]/g;
const SCRIPT = /<script[^>]+src=["']\.?\/?([^"'/][^"']*\.mjs[^"']*)["']/g;

function walk(page) {
  const urls = new Map();
  const queue = [...readFileSync(join(root, page), 'utf8').matchAll(SCRIPT)].map((m) => './' + m[1]);
  const seen = new Set();
  while (queue.length) {
    const url = queue.pop();
    if (seen.has(url)) continue;
    seen.add(url);
    const file = url.split('?')[0].replace(/^\.\//, '');
    if (!urls.has(file)) urls.set(file, new Set());
    urls.get(file).add(url);
    if (!file.endsWith('.mjs') || !existsSync(join(root, file))) continue;
    for (const match of readFileSync(join(root, file), 'utf8').matchAll(IMPORT)) queue.push(match[1]);
  }
  return urls;
}

for (const page of PAGES) {
  test(`${page} loads every module under a single URL`, () => {
    const duplicates = [...walk(page)].filter(([, set]) => set.size > 1).map(([file, set]) => `${file}: ${[...set].join(', ')}`);
    assert.deepEqual(duplicates, []);
  });
}
