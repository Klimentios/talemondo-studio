import test from 'node:test';
import assert from 'node:assert/strict';
import { productionFrameRequest } from './production-frame.mjs';
import { autoKeyframeRequest, shotPlan } from './storyboard-model.mjs';
const spec=()=>({characters:[{id:'Milo',identity:'Adult cat',reference_asset_ids:['hero']}],
 shot:{state_in:'At the closed door',state_out:'Outside',first_frame_description:'Paw raised before contact',spatial_layout:'Door left, cat right',beats:[]},
 visual_profile:{frame_direction:'Readable silhouette',video_direction:'React after the sound',reference_asset_ids:['look']}});
const refs=[{id:'hero',kind:'image',role:'Персонаж целиком'}, {id:'look',kind:'image',role:'Стиль'}, {id:'room',kind:'image',role:'Окружение'}];
test('staged keyframe carries distinct layout, identity and style inputs',()=>{
 const cut={productionSpec:spec()};const result=productionFrameRequest(cut,refs);
 assert.equal(result.ok,true);assert.deepEqual(result.asset_ids,['room','hero','look']);
 assert.deepEqual(result.roles,['Окружение','Персонаж целиком','Стиль']);
 assert.match(result.prompt,/Paw raised before contact/);assert.match(result.prompt,/Door left/);assert.match(result.prompt,/Readable silhouette/);
 assert.doesNotMatch(result.prompt,/React after the sound|Outside/);
 assert.deepEqual(autoKeyframeRequest([cut],0,{},refs),result);
});
test('required image overflow fails rather than weakening identities',()=>{
 const s=spec();s.characters.push({id:'Teo',identity:'Human',reference_asset_ids:['human']});
 const result=productionFrameRequest({productionSpec:s},[...refs,{id:'human',kind:'image',role:'Лицо героя'}]);
 assert.equal(result.ok,false);assert.match(result.message,/лимит/);
});
test('changed staging cannot reuse an old start frame as if current',()=>{
 const result=shotPlan([{prompt:'Cat',duration:5,startFrame:{source:'library',asset_id:'old'},productionFrameChanged:true}],0,new Map(),{});
 assert.equal(result.ok,false);assert.match(result.message,/постановка изменилась/);
});
