import test from 'node:test';
import assert from 'node:assert/strict';
import { namedReplacementRequest, resolveNamedReplacements } from './named-character-references.mjs';

const entry = (id, franchise, characterName, preferredRole, look = '') => ({
  asset: { asset_id: id, kind: 'image' }, info: { franchise, characterName, preferredRole, look },
});

test('named replacement takes portrait and matching seasonal outfit', () => {
  const entries = [entry('face', 'Алекс и Нова', 'Алекс', 'Лицо героя'), entry('halloween', 'Алекс и Нова', 'Алекс', 'Костюм', 'Хэллоуин'), entry('xmas', 'Алекс и Нова', 'Алекс', 'Костюм', 'Рождество')];
  assert.deepEqual(resolveNamedReplacements('Замени мужчину на Алекс в костюме на Хэллоуин', entries).map(x => x.asset.asset_id), ['face', 'halloween']);
  assert.deepEqual(namedReplacementRequest('Замени кота на Нова в образе супергероя'), { names: ['нова'], look: 'Супергерой', looks: ['Супергерой'] });
});

test('ambiguous franchise and missing portrait or costume stop the replacement', () => {
  const entries = [entry('real', 'Нова реал', 'Нова', 'Лицо героя'), entry('3d', 'Нова 3D', 'Нова', 'Лицо героя')];
  assert.throws(() => resolveNamedReplacements('Замени кота на Нова', entries), /несколько франшиз/);
  assert.deepEqual(resolveNamedReplacements('Замени кота на Нова 3D', entries).map(x => x.asset.asset_id), ['3d']);
  assert.throws(() => resolveNamedReplacements('Замени кота на Нова 3D на Хэллоуин', entries), /нет проверенного костюма/);
  assert.throws(() => resolveNamedReplacements('Замени кота на Рон', [entry('suit', 'Франшиза Б', 'Рон', 'Костюм')]), /нет отдельного портрета/);
  assert.deepEqual(resolveNamedReplacements('Replace the cat with NOVA from Нова 3D', entries).map(x => x.asset.asset_id), ['3d']);
});

test('two heroes in their own attached costumes are not «several looks for one replacement»', () => {
  const prompt = 'Replace the two cats with ALEX and NOVA: ALEX in the Halloween costume, NOVA in the Christmas costume.';
  const attached = [{ id: 'a', owner: 'ALEX', role: 'Персонаж целиком' }, { id: 'm', owner: 'NOVA', role: 'Персонаж целиком' }];
  assert.deepEqual(resolveNamedReplacements(prompt, [], attached), []);
  // Nothing attached for the named hero: the look is still ambiguous and must be picked.
  assert.throws(() => resolveNamedReplacements(prompt, [], []), /несколько образов: Хэллоуин, Рождество/);
});
