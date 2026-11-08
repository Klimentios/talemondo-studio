import test from 'node:test';
import assert from 'node:assert/strict';
import {productionPayload} from './production-flow.mjs';
test('automatic workflow preserves creative snapshot and explicit settings without mutating it',()=>{
 const snapshot={brief:'ALEX waves',references:[{asset_id:'a'}],settings:{cache_mode:'safe',steps:25,seed:8}};
 const result=productionPayload(snapshot,{seed:9,cache_mode:'custom',cache_config:{threshold:0.04}});
 assert.equal(result.settings.seed,9);assert.equal(snapshot.settings.seed,8);
 assert.equal(result.settings.steps,25);assert.equal(result.references[0].asset_id,'a');
 assert.equal(result.max_generation_jobs,4);assert.equal(result.settings.cache_config.threshold,0.04);
});

test('source window is bound to an attached video and current shot duration',()=>{
 const snapshot={brief:'Wave',references:[{asset_id:'v',kind:'video'}],settings:{duration:5}};
 assert.deepEqual(productionPayload(snapshot,{}, {start:10,duration:5,assetIds:['v']}).reference_windows,
   {v:{start:10,duration:5}});
 assert.throws(()=>productionPayload(snapshot,{duration:6},{start:10,duration:5,assetIds:['v']}));
 assert.throws(()=>productionPayload(snapshot,{}, {start:10,duration:5,assetIds:['missing']}));
});

test('active tasks remain visible ahead of completed history, and expansion loses no rows',async()=>{
 const {visibleProductions}=await import('./production-flow.mjs');
 const rows=Array.from({length:12},(_,i)=>({id:i,state:'completed'}));rows.push({id:'active',state:'rendering'});
 assert.equal(visibleProductions(rows)[0].id,'active');assert.equal(visibleProductions(rows).length,8);
 assert.equal(visibleProductions(rows,true).length,13);assert.equal(rows[0].id,0);
});
