import assert from 'node:assert/strict';
import { test } from 'node:test';
import { sceneOverviewRows } from './scene-overview.mjs';

test('film strip distinguishes rendered, accepted and stale continuations', () => {
  const cuts = [
    { id: 'a', duration: 5, prompt: 'Enter', shotJobId: 'j1',
      shotQcReview: 'accepted', shotQc: { job_id: 'j1', decision: 'accept' },
      sceneShot: { state_before: 'at door', state_after: 'inside room' } },
    { id: 'b', duration: 5, prompt: 'Turn', shotJobId: 'j2', startFrame: { source: 'previous', from_job: 'old' },
      shotQcReview: 'accepted', shotQc: { job_id: 'j2', decision: 'accept' } },
    { id: 'c', duration: 5, prompt: 'Sit', shotJobId: 'j3' },
    { id: 'd', duration: 5, prompt: 'Leave', shotJobId: 'j4',
      shotQcReview: 'rejected', shotQc: { job_id: 'j4', decision: 'regenerate' } },
  ];
  const jobs = new Map(['j1', 'j2', 'j3', 'j4'].map((id) => [id, { state: 'completed' }]));
  const rows = sceneOverviewRows(cuts, jobs, { linkPrevious: true });
  assert.equal(rows[0].phase, 'accepted');
  assert.equal(rows[0].after, 'inside room');
  assert.equal(rows[1].phase, 'stale');
  assert.equal(rows[1].approved, false);
  assert.equal(rows[1].transition, 'Продолжение с конца предыдущего');
  assert.equal(rows[2].phase, 'review');
  assert.match(rows[2].next, /QC/);
  assert.equal(rows[3].phase, 'blocked');
  assert.match(rows[3].next, /пересними/);
});

test('keyframe acceptance expires when reference bindings change', () => {
  const cut = { id: 'c', prompt: 'Enter', startFrame: { source: 'keyframe', asset_id: 'frame' },
    frameAcceptance: { asset_id: 'frame', spec_revision: null, reference_ids: ['r1'],
      reference_bindings: [['r1', 'Персонаж целиком', 'NOVA']] } };
  const refs = [{ id: 'r1', role: 'Персонаж целиком', owner: 'NOVA' }];
  assert.equal(sceneOverviewRows([cut], new Map(), {}, refs)[0].frameApproved, true);
  const changed = [{ ...refs[0], owner: 'ORION' }];
  const row = sceneOverviewRows([cut], new Map(), {}, changed)[0];
  assert.equal(row.frameApproved, false);
  assert.equal(row.phase, 'review');
  assert.match(row.next, /прими опорный кадр/);
});
