// The staging editor shows validation errors in its own field names, not as raw API JSON.
import test from 'node:test';
import assert from 'node:assert/strict';
import { specFieldErrors } from './production-spec-editor.mjs';

test('validation errors name the action, the hero and the field the user must fill', () => {
  assert.equal(specFieldErrors([
    { type: 'string_too_short', loc: ['body', 'shot', 'beats', 0, 'cause'] },
    { type: 'string_too_short', loc: ['body', 'characters', 1, 'identity'] },
    { type: 'missing', loc: ['body', 'franchise_id'] },
  ]), 'Действие 1 · Что вызвало действие — не заполнено; Герой 2 · Описание героя — не заполнено; Франшиза — не заполнено');
});
