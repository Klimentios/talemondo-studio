import test from 'node:test';
import assert from 'node:assert/strict';
import { removeBeat } from './production-beats-editor.mjs';

test('removal cannot orphan a reaction or empty a shot',()=>{
 const beats=[{id:'cause'},{id:'reaction',cause_beat_id:'cause'}];
 assert.throws(()=>removeBeat(beats,'cause'),/Сначала измени причину/);
 assert.throws(()=>removeBeat([beats[0]],'cause'),/хотя бы одно/);
 assert.deepEqual(removeBeat(beats,'reaction'),[{id:'cause'}]);
 assert.equal(beats.length,2);
});
