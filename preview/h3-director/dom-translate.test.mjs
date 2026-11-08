import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadDictionary, translateText } from './dom-translate.mjs';

loadDictionary(JSON.parse(readFileSync(new URL('./i18n/en.json', import.meta.url), 'utf8')));

test('whole phrases, phrases around inserted values, HTML line breaks', () => {
  assert.equal(translateText('Сохранить'), 'Save');
  assert.match(translateText('  Сбросить  '), /^ {2}\S.* {2}$/);
  const withNumber = translateText('ревизия 4');
  assert.ok(withNumber && !/[А-я]/.test(withNumber) && withNumber.includes('4'), withNumber);
  assert.ok(translateText('Пока пусто. Импортируй JSON графа (в ComfyUI: Workflow → Export (API))\n   или нажми в ComfyUI «⇪ Воркфлоу в Studio», затем «Из ComfyUI».'));
});

test("the user's own Russian text stays as written", () => {
  assert.equal(translateText('Маша идёт по набережной и смеётся'), null);
  assert.equal(translateText('hello'), null);
});

test('dictionary has no Cyrillic in translations', () => {
  const dict = JSON.parse(readFileSync(new URL('./i18n/en.json', import.meta.url), 'utf8'));
  for (const [ru, en] of Object.entries(dict)) {
    assert.ok(!/[А-Яа-яЁё]/.test(en), `${ru} → ${en}`);
  }
});
