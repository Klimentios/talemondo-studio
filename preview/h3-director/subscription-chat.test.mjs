import test from 'node:test';
import assert from 'node:assert/strict';
import { referenceIds, subscriptionTurn } from './subscription-chat.mjs';

test('named hero uses a face reference for a portrait and body reference otherwise', () => {
  const profile = { roles: [{ name: 'NOVA', aliases: ['Нова'], assets: [
    { asset_id: 'face', role: 'Лицо героя' }, { asset_id: 'body', role: 'Персонаж целиком' },
  ] }] };
  assert.deepEqual(referenceIds('Лицо Нова', profile), ['face']);
  assert.deepEqual(referenceIds('Нова на диване', profile), ['body']);
  assert.deepEqual(referenceIds('Алекс на диване', profile), []);
});

test('offline subscription connector makes no agent calls and sends no generation', async () => {
  const calls = [];
  await assert.rejects(subscriptionTurn({ chat: { id: 'c' }, text: 'hi', attachments: [], mode: 'chat',
    fetcher: async (url) => { calls.push(url); return { ok: true, json: async () => ({ connected: false }) }; },
  }), /расширение/);
  assert.deepEqual(calls, ['/api/subscription-bridge/status']);
});

test('an older Studio server gets a specific version error instead of Not Found', async () => {
  await assert.rejects(subscriptionTurn({ chat: { id: 'c' }, text: 'hi', attachments: [], mode: 'chat',
    fetcher: async () => ({ ok: false, status: 404, json: async () => ({ detail: 'Not Found' }) }),
  }), /перезапуск службы/);
});
