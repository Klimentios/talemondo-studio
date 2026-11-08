import test from 'node:test';
import assert from 'node:assert/strict';
import { errorText } from './api-error.mjs';

test('validation lists and objects become readable text', () => {
  assert.equal(errorText({ detail: 'Нет кадра' }, 422), 'Нет кадра');
  assert.equal(errorText({ detail: [{ loc: ['body', 'references', 0, 'name'], msg: 'String should have at most 120 characters' }] }, 422),
    'references.0.name: String should have at most 120 characters');
  assert.equal(errorText({ detail: { message: 'занято' } }, 409), 'занято');
  assert.match(errorText({}, 502), /GPU-сервер не отвечает/);
  assert.equal(errorText({}, 500), 'Ошибка 500');
  assert.ok(!errorText({ detail: [{ a: 1 }] }, 422).includes('[object Object]'));
});
