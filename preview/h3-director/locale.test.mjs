import test from 'node:test';
import assert from 'node:assert/strict';

test('locale persists the choice and ignores unsupported languages', async () => {
  const values = new Map();
  globalThis.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const locale = await import(`./locale.mjs?test=${Date.now()}`);
  assert.equal(locale.getLocale(), 'ru');
  assert.equal(locale.setLocale('en'), 'en');
  assert.equal(values.get('h3-studio-locale'), 'en');
  assert.equal(locale.setLocale('de'), 'en');
  assert.equal(values.get('h3-studio-locale'), 'en');
  assert.equal(locale.setLocale('ru'), 'ru');
  delete globalThis.localStorage;
});
