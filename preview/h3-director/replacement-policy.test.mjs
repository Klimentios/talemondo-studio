import test from 'node:test';
import assert from 'node:assert/strict';
import { validateReplacementMotion } from './replacement-policy.mjs';

const hero = { kind: 'image', role: 'Персонаж целиком' };
const video = (role) => ({ kind: 'video', role });

test('movement replacement asks for the «Движение и камера» role', () => {
  assert.throws(() => validateReplacementMotion({ mode: 'replace' }, [hero, video('Движение')]), /выбери роль видео «Движение и камера»/);
});

test('approximate replacement requires an explicit source video role', () => {
  assert.throws(() => validateReplacementMotion({ mode: 'replace' }, [hero, video('Авто — определить по содержимому')]), /Укажи роль/);
  assert.doesNotThrow(() => validateReplacementMotion({ mode: 'replace' }, [hero, video('Исходное видео')]));
  assert.doesNotThrow(() => validateReplacementMotion({ mode: 'replace' }, [hero, video('Движение и камера')]));
});
