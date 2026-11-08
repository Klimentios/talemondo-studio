import test from 'node:test';
import assert from 'node:assert/strict';
import { libraryItems } from './library-picker.mjs';

const assets = [
  { asset_id: 'a1', kind: 'image', role: 'Лицо героя', original_name: 'putin.jpg', created_at: '3' },
  { asset_id: 'a2', kind: 'image', role: 'Костюм', original_name: 'suit.png', created_at: '2' },
  { asset_id: 'v1', kind: 'video', role: 'Движение', original_name: 'dance.mp4', created_at: '1' },
];
const jobs = [{ job_id: 'j1', state: 'completed', original_prompt: 'BORIS sings', created_at: '2026-10-08 19:47' },
  { job_id: 'j2', state: 'failed', original_prompt: 'x' }];

test('sections pick by role; finished videos come from the queue; search by name', () => {
  assert.deepEqual(libraryItems('image', assets, [], 'heroes', '').map((item) => item.value), ['a1']);
  assert.deepEqual(libraryItems('image', assets, [], 'outfit', '').map((item) => item.value), ['a2']);
  assert.deepEqual(libraryItems('image', assets, [], 'all', 'SUI').map((item) => item.value), ['a2']);
  assert.deepEqual(libraryItems('video', assets, jobs, 'results', '').map((item) => item.value), ['job:j1']);
  assert.deepEqual(libraryItems('video', assets, jobs, 'motion', '').map((item) => item.value), ['v1']);
  assert.deepEqual(libraryItems('video', assets, jobs, 'edits', '', [{edit_id:'e1',state:'completed'},{edit_id:'e2',state:'running'}]).map(i=>i.value),['edit:e1']);
});
