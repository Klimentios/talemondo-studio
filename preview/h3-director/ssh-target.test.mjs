import test from 'node:test';
import assert from 'node:assert/strict';
import { parseSshTarget } from './ssh-target.mjs';

test('a pasted provider SSH command fills host and port', () => {
  assert.deepEqual(parseSshTarget('ssh -p 41973 root@203.0.113.10 -L 8080:localhost:8080'), { host: '203.0.113.10', port: 41973 });
  assert.deepEqual(parseSshTarget('root@203.0.113.20 -p 52487'), { host: '203.0.113.20', port: 52487 });
  assert.deepEqual(parseSshTarget('203.0.113.20:52487'), { host: '203.0.113.20', port: 52487 });
  assert.deepEqual(parseSshTarget('ssh root@ssh5.vast.ai -p 12345'), { host: 'ssh5.vast.ai', port: 12345 });
  assert.equal(parseSshTarget('hello'), null);
});
