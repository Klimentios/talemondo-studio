import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareReferenceExcerpts, excerptWindow } from './reference-excerpts.mjs';

test('video and audio submissions use clip IDs and keep roles, owners and source draft', async () => {
  const refs = [{ id:'photo', kind:'image' }, { id:'video', kind:'video', excerptStart:23, number:1, role:'Движение', owner:'Hero' }, { id:'audio', kind:'audio', number:1 }];
  const calls = [];
  const prepared = await prepareReferenceExcerpts(refs, 10, async (url, options) => {
    calls.push({url, body:JSON.parse(options.body)});
    return { asset_id:'clip-'+calls.length, original_name:'clip', excerpt:{duration:10} };
  });
  assert.deepEqual(calls.map(c=>c.body), [{start:23,duration:10},{start:0,duration:10}]);
  assert.deepEqual(prepared.map(r=>r.id), ['photo','clip-1','clip-2']);
  assert.equal(prepared[1].owner, 'Hero'); assert.equal(prepared[1].role, 'Движение');
  assert.equal(refs[1].id, 'video'); assert.equal(refs[1].excerptStart, 23);
});

test('failed excerpt preparation stops submission', async () => {
  await assert.rejects(prepareReferenceExcerpts([{id:'v',kind:'video'}], 15, async()=>{throw Error('clip failure');}), /clip failure/);
});

test('explicit end persists as selected duration instead of expanding to generation duration', async () => {
  let submitted;
  const refs = [{id:'v',kind:'video',excerptStart:5,excerptDuration:4}];
  const result = await prepareReferenceExcerpts(refs, 15, async (url,options) => {
    submitted=JSON.parse(options.body);
    return {asset_id:'v-5-9',original_name:'5–9',excerpt:{duration:4}};
  });
  assert.deepEqual(submitted,{start:5,duration:4});
  assert.equal(result[0].sourceDuration,4);
  assert.equal(refs[0].excerptStart,5);assert.equal(refs[0].excerptDuration,4);
  assert.throws(()=>excerptWindow({excerptDuration:0},15));
});
