import {test} from 'node:test';
import assert from 'node:assert/strict';
import {promptForShot,sceneRequests,settingsForShot,submitScenes} from './scene-queue.mjs';
test('all scenes retain prompt, own duration and shared native settings',async()=>{
 const cuts=[{id:'a',prompt:'one',duration:5},{id:'b',prompt:'two',duration:15}];
 const requests=sceneRequests(cuts,{style_adapter:'style3d',cache_mode:'safe'});
 const events=[];
 await submitScenes(requests,async r=>{events.push(r.prompt);return r.id},async(r,j)=>events.push(j));
 assert.deepEqual(events,['one','a','two','b']);
 assert.equal(requests[1].settings.duration,15);
 assert.equal(requests[1].settings.style_adapter,'style3d');
 assert.equal(cuts.length,2);
});
test('invalid later scene prevents submission preparation',()=>{
 assert.throws(()=>sceneRequests([{prompt:'one',duration:5},{prompt:'',duration:15}],{}),/Сцена 2/);
});
test('a retimed cut gets its own local timecode before submission',()=>{
 const cut={id:'cut-2',duration:5,prompt:'detailed_description:\n[Shot 2: 4.5-9s] NOVA turns.'};
 assert.match(sceneRequests([cut],{})[0].prompt,/\[Shot 1: 0-5s\]/);
 assert.match(cut.prompt,/\[Shot 2: 4\.5-9s\]/,'saved draft remains unchanged');
 assert.throws(()=>sceneRequests([{...cut,prompt:'[Shot 1: 0-2s] A. [Shot 2: 2-5s] B.'}],{}),/несколько таймкодов/);
});
test('per-shot direction reaches the generated job without replacing the saved prompt',()=>{
 const cut={id:'cut-7',prompt:'subject_definitions:\nNOVA is a cat.\n\nsummary:\n[reference generation + keyframe completion] NOVA sees a door.\nretention_analysis:\nNOVA: keep face.\ndetailed_description:\n[Shot 1] NOVA sees a door.\noverall_soundscape:\nQuiet room.\nnon_diegetic_music:\nNone.',duration:6,
   sceneShot:{action:'NOVA turns toward MARK.',framing:'close',lens_mm:85,camera_motion:'tracking'}};
 const request=sceneRequests([cut],{director_config:{shot:{id:'shot.medium',parameters:{framing:'medium'}}}})[0];
 assert.match(request.prompt,/NOVA turns toward MARK/);
 assert.match(cut.prompt,/summary:/,'saved prompt remains unchanged');
 assert.equal(request.settings.director_config.shot.parameters.framing,'close');
 assert.equal(request.settings.director_config.lens.parameters.focal_length_mm,85);
 assert.equal(request.settings.director_config.movement.parameters.movement,'tracking');
 assert.equal(settingsForShot({},{}).director_config && Object.keys(settingsForShot({},{}).director_config).length,0);
 assert.match(promptForShot(cut),/Shot-specific action direction/);
});
test('official H3 shot action stays inside detailed_description',()=>{
 const prompt='subject_definitions:\nNOVA: cat\nsummary:\n[reference generation + keyframe completion] NOVA walks.\nretention_analysis:\nNOVA: retain face.\ndetailed_description:\n[Shot 1] NOVA walks.\noverall_soundscape:\nQuiet room.\nnon_diegetic_music:\nNone.';
 const result=promptForShot({prompt,sceneShot:{action:'NOVA closes the door.'}});
 assert.ok(result.indexOf('NOVA closes the door.')>result.indexOf('detailed_description:'));
 assert.ok(result.indexOf('NOVA closes the door.')<result.indexOf('overall_soundscape:'));
 assert.equal(promptForShot({prompt:result,sceneShot:{action:'NOVA closes the door.'}}),result);
});
test('each queued shot compiles its own director presets',()=>{
 const cuts=[
  {id:'a',prompt:'one',duration:5,director:{lighting:'lighting.neon',focus:'focus.eyes'}},
  {id:'b',prompt:'two',duration:5,director:{lighting:'lighting.golden',focus:'focus.deep'}},
 ];
 const requests=sceneRequests(cuts,{director_config:{lighting:{id:'lighting.natural',parameters:{source:'sun'}}}});
 assert.equal(requests[0].settings.director_config.lighting.id,'lighting.neon');
 assert.equal(requests[1].settings.director_config.lighting.id,'lighting.golden');
 assert.equal(requests[0].settings.director_config.focus.id,'focus.eyes');
 assert.equal(requests[1].settings.director_config.focus.id,'focus.deep');
});
test('partial failure acknowledges only confirmed scenes',async()=>{
 const saved=[];
 await assert.rejects(submitScenes([{id:1},{id:2},{id:3}],async r=>{if(r.id===2)throw Error('network');return r.id},async r=>saved.push(r.id)),/network/);
 assert.deepEqual(saved,[1]);
});

test('production staging survives agent cuts and scene requests and rejects duration drift', async () => {
 const { sceneCuts } = await import('./agent-draft-model.mjs');
 const spec = {franchise_id:'f1',characters:[],shot:{id:'s1',revision:1,duration:5,beats:[]}};
 const cuts = sceneCuts({shots:[{title:'Shot',duration:5,prompt:'A cat',production_spec:spec}]});
 const request = sceneRequests(cuts,{})[0];
 assert.deepEqual(request.production_spec,spec);
 cuts[0].productionSpec.shot.revision=2;
 assert.equal(request.production_spec.shot.revision,1);
 cuts[0].duration=6;
 assert.throws(()=>sceneRequests(cuts,{}),/обнови постановку/);
});
test('the cut\'s own preset keeps the prompt speed and end framing the snapshot compiled; another preset is compiled',()=>{
 const snapshot={director_config:{movement:{id:'movement.static',parameters:{movement:'static',speed:'fast',end_framing:'close'}}}};
 assert.deepEqual(settingsForShot(snapshot,{director:{movement:'movement.static'}}).director_config.movement.parameters,
   {movement:'static',speed:'fast',end_framing:'close'});
 assert.equal(settingsForShot(snapshot,{director:{movement:'movement.orbit'}}).director_config.movement.id,'movement.orbit');
});
