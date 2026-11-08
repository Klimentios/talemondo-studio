import test from 'node:test';
import assert from 'node:assert/strict';
import { applySceneDraft } from './agent-draft.mjs';

test('missing scene reference leaves the current canvas untouched', async () => {
  const previousFetch = globalThis.fetch;
  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  globalThis.fetch = async () => ({ ok: true, json: async () => [] });
  globalThis.window = {};
  globalThis.document = { querySelector: () => null };
  let created = 0;
  const studio = {
    canWriteDraft: () => true,
    project: () => ({}),
    sceneState: () => ({ scenes: [], activeSceneId: "s1" }),
    cuts: () => [{ prompt: 'existing shot' }],
    createScene: () => { created += 1; },
  };
  try {
    await assert.rejects(() => applySceneDraft({ title: 'new', references: [{ asset_id: 'missing' }],
      shots: [{ duration: 5 }] }, studio), /Нет в библиотеке/);
    assert.equal(created, 0);
  } finally {
    globalThis.fetch = previousFetch;
    globalThis.window = previousWindow;
    globalThis.document = previousDocument;
  }
});

test('invalid production staging is rejected before canvas mutation', async () => {
 const oldFetch=globalThis.fetch;
 globalThis.fetch=async()=>({ok:true,json:async()=>({valid:false})});
 let changed=false;
 const studio={canWriteDraft:()=>true,project:()=>({}),sceneState:()=>({scenes:[]}),
   cuts:()=>[],createScene:()=>{changed=true;},editCuts:()=>{changed=true;}};
 try {
  await assert.rejects(()=>applySceneDraft({franchise_id:'f1',shots:[{duration:5,
   production_spec:{franchise_id:'f1',characters:[],shot:{duration:5}}}]},studio),/Некорректная постановка/);
  assert.equal(changed,false);
 } finally {globalThis.fetch=oldFetch;}
});
