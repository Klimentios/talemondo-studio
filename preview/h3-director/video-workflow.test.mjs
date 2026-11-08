import test from 'node:test';
import assert from 'node:assert/strict';
import { videoWorkflowState } from './video-workflow.mjs';

const image = { kind: 'image', role: 'Лицо героя' };
const video = role => ({ kind: 'video', role });

test('motion-only reference offers the supported realistic mode before submission', () => {
  const state = videoWorkflowState([image, video('Движение')], 'replace');
  assert.equal(state.tone, 'warning');
  assert.deepEqual(state.actions[0], ['realistic', 'Выбрать «Реализм»']);
  assert.deepEqual(state.actions.slice(1).map(([action]) => action), ['motion-template-source','motion-template-scene','motion-template-only']);
  assert.match(videoWorkflowState([image, video('Движение')], 'realistic').detail, /не покадровое копирование/);
});

test('unassigned source video asks for an explicit role', () => {
  const state = videoWorkflowState([image, video('Авто — определить по содержимому')], 'replace');
  assert.deepEqual(state.actions.map(([action]) => action), ['motion', 'motion-camera', 'source-scene']);
});

test('multi-performer Character Swap remains explicitly experimental', () => {
  const paired = { ...video('Движение и камера'), performers: { pairs: [
    { owner: 'ANNA', performer: 'left' }, { owner: 'ALEX', performer: 'right' },
  ] } };
  const state = videoWorkflowState([image, paired], 'replace');
  assert.equal(state.tone, 'warning');
  assert.match(state.title, /Экспериментальный перенос нескольких героев/);
  assert.doesNotMatch(state.detail, /Character Swap/);
});

test('motion-camera does not advertise an adapter that is not applied', () => {
  const state = videoWorkflowState([image, video('Движение и камера')], 'replace');
  assert.equal(state.tone, 'ready');
  assert.doesNotMatch(state.title, /Character Swap/);
  assert.deepEqual(state.actions.map(([action]) => action), ['motion-template-source','motion-template-scene','motion-template-only','source-scene']);
  assert.match(state.detail, /«Вся сцена»/);
});

test('paired source scene in replace mode is the proven swap route', () => {
  const paired = { ...video('Исходное видео'), performers: { pairs: [{ owner: 'ORION', performer: 'the cat behind the bags' }] } };
  const state = videoWorkflowState([image, paired], 'replace');
  assert.equal(state.tone, 'ready');
  assert.match(state.detail, /Персонаж целиком/);
  assert.equal(videoWorkflowState([image, video('Исходное видео')], 'replace').tone, 'info');
});

test('long movement source explains that only the opening fits the shot', () => {
  const state = videoWorkflowState([image, { ...video('Движение'), sourceDuration: 31.46 }], 'realistic', 15);
  assert.equal(state.tone, 'warning');
  assert.match(state.detail, /31,5 с, кадр 15,0 с/);
  assert.match(state.detail, /только первые 15,0 с/);
});
