import test from 'node:test';
import assert from 'node:assert/strict';
import { importFranchisePacket, promptsFromIdea } from './franchise-packet.mjs';
import { normalizeSceneProject, switchScene, saveActiveScene, addScene, deleteScene } from './scene-project-model.mjs';
const packet = {version:1,id:'packet-test',franchise_id:'f',board_id:'b',scene_id:'s',title:'Эпизод',prompt:'Действие героя',references:[{id:'hero',kind:'image',number:1,name:'hero.png',role:'Лицо героя',owner:'NOVA',manualOwner:true}]};
test('franchise import preserves legacy references and ref-dependent state across switch, reload, creation and deletion',()=>{
 const original={references:[{id:'old',kind:'image',number:1}],referenceCounters:{image:1,video:0,audio:0},useVideoAudio:true,colorMatch:{asset_id:'old'},guideFrames:[[1,0]],actingVoices:{old:{voice:'old'}}};
 const source=normalizeSceneProject({...structuredClone(original),cuts:[{id:'old-cut',prompt:'Old <Picture 1>',duration:5}]});
 source.cuts[0].prompt='Unsaved edit <Picture 1>';
 const old=source.activeSceneId;
 const imported=importFranchisePacket(source,packet);
 assert.deepEqual(source.references,original.references);
 assert.equal(imported.references[0].id,'hero');assert.equal(imported.colorMatch,null);assert.equal(imported.useVideoAudio,false);
 imported.references[0].role='Персонаж целиком';saveActiveScene(imported);
 const reloaded=normalizeSceneProject(structuredClone(imported));
 switchScene(reloaded,old);assert.equal(reloaded.cuts[0].prompt,'Unsaved edit <Picture 1>');for(const [key,value] of Object.entries(original))assert.deepEqual(reloaded[key],value);
 switchScene(reloaded,'franchise-packet-test');assert.equal(reloaded.references[0].role,'Персонаж целиком');
 assert.equal(importFranchisePacket(reloaded,packet).scenes.length,2);
 // «+ Сцена» is a separate space: it starts with no materials, and nobody else's change.
 addScene(reloaded);assert.deepEqual(reloaded.references,[]);
 switchScene(reloaded,'franchise-packet-test');deleteScene(reloaded,'franchise-packet-test');
 switchScene(reloaded,old);assert.deepEqual(reloaded.references,original.references);
});
test('import rejects ambiguous asset ownership and duplicate references',()=>{
 const source=normalizeSceneProject({cuts:[{id:'old',prompt:'old'}]});
 assert.throws(()=>importFranchisePacket(source,{...packet,references:[packet.references[0],{...packet.references[0],owner:'ALEX'}]}));
 assert.throws(()=>importFranchisePacket(source,{...packet,prompt:''}));
});
test('four H3 prompts become a 40-second scene with consecutive frame links',()=>{
 const body='ГОТОВЫЕ ПРОМПТЫ H3 · 4×10 с\n'+[1,2,3,4].map(i=>`## ${String(i).padStart(2,'0')} — Шот ${i}\n\n\`\`\`text\nH3 prompt ${i}\n\`\`\``).join('\n');
 const shots=promptsFromIdea(body);
 const imported=importFranchisePacket(normalizeSceneProject({cuts:[{id:'old',prompt:'old',duration:5}]}),{...packet,shots});
 assert.equal(imported.cuts.length,4);
 assert.equal(imported.cuts.reduce((sum,cut)=>sum+cut.duration,0),40);
 assert.deepEqual(imported.cuts.map(cut=>cut.startFrame?.source||null),[null,'previous','previous','previous']);
 assert.deepEqual(imported.cuts.map(cut=>cut.prompt),shots.map(shot=>shot.prompt));
});
test('an explicit time cut keeps the previous frame as environment instead of forcing frame zero',()=>{
 const body='ГОТОВЫЕ ПРОМПТЫ H3 · 4×10 с\n'+[1,2,3,4].map(i=>`## ${String(i).padStart(2,'0')} — Шот ${i}\n\n\`\`\`text\nH3 prompt ${i}${i===3?'\n<Picture 5>: weak_reference for an explicit cut to a different time or place.':''}\n\`\`\``).join('\n');
 const shots=promptsFromIdea(body);
 const imported=importFranchisePacket(normalizeSceneProject({cuts:[{id:'old',prompt:'old',duration:5}]}),{...packet,shots});
 assert.deepEqual(shots.map(shot=>shot.transition),['continue','continue','cut','continue']);
 assert.equal(imported.cuts[2].startFrame,null);
 assert.equal(imported.cuts[2].colorMatch,false);
 assert.equal(imported.storyboard.linkPrevious,true);
 assert.equal(imported.storyboard.colorSource,'previous');
});
