import test from 'node:test';
import assert from 'node:assert/strict';
import { nextLocalizedState, translateAppText } from './app-i18n.mjs';

test('localizes dynamic queue and scene labels without changing identifiers', () => {
  assert.equal(translateAppText('Кадр 03', 'en'), 'Shot 03');
  assert.equal(translateAppText('3 в работе/ожидании · 55 всего', 'en'), '3 running/waiting · 55 total');
  assert.equal(translateAppText('Сэмплинг 50% · 13/26 шагов', 'en'), 'Sampling 50% · 13/26 steps');
  assert.equal(translateAppText('Фото · NOVA-front.png', 'en'), 'Photo · NOVA-front.png');
  assert.equal(translateAppText('Кадр 03', 'ru'), 'Кадр 03');
});

test('never rewrites arbitrary prompt, filename or H3 reference tag', () => {
  const prompt = 'NOVA идёт по городу, <Picture 1> / do not change';
  assert.equal(translateAppText(prompt, 'en'), prompt);
  assert.equal(translateAppText('NOVA-front.png', 'en'), 'NOVA-front.png');
  assert.equal(translateAppText('<Video 2>', 'en'), '<Video 2>');
});

test('a reused text node or aria label accepts a newer runtime state', () => {
  let text = nextLocalizedState(null, 'Ждёт запуска на GPU', 'en');
  assert.equal(text.lastApplied, 'Waiting to start on GPU');
  text = nextLocalizedState(text, 'Генерация на GPU', 'en');
  assert.equal(text.lastApplied, 'Generating on GPU');
  text = nextLocalizedState(text, text.lastApplied, 'ru');
  assert.equal(text.lastApplied, 'Генерация на GPU');

  let aria = nextLocalizedState(null, 'Просмотреть NOVA-front.png', 'en');
  assert.equal(aria.lastApplied, 'Preview NOVA-front.png');
  aria = nextLocalizedState(aria, 'Выбрать NOVA-front.png', 'en');
  assert.equal(aria.lastApplied, 'Select NOVA-front.png');
  aria = nextLocalizedState(aria, aria.lastApplied, 'ru');
  assert.equal(aria.lastApplied, 'Выбрать NOVA-front.png');
});
