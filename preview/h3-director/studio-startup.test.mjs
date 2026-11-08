import test from 'node:test';
import assert from 'node:assert/strict';
import { startStudioLoad } from './studio-startup.mjs';

test('a stalled GPU check does not block loading local references and draft', async () => {
  const calls = [];
  let releaseHealth;
  const pending = new Promise(resolve => { releaseHealth = resolve; });
  const load = startStudioLoad(path => {
    calls.push(path);
    return path === '/api/health' ? pending : Promise.resolve(path === '/api/assets' ? [{ asset_id: 'face' }] : { project: { references: ['face'] } });
  });
  const [assets, draft] = await load.workspace;
  assert.equal(assets[0].asset_id, draft.project.references[0]);
  assert.deepEqual(calls, ['/api/health', '/api/assets', '/api/draft']);
  releaseHealth({ gpu_reachable: true });
  assert.equal((await load.health).gpu_reachable, true);
});

test('remote check failure stays offline; local draft failure still rejects hydration', async () => {
  const load = startStudioLoad(path => path === '/api/assets' ? Promise.resolve([]) : Promise.reject(new Error('offline')));
  await assert.rejects(load.workspace, /offline/);
  assert.equal((await load.health).gpu_reachable, false);
});
