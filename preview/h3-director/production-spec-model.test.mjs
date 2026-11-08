import test from 'node:test';
import assert from 'node:assert/strict';
import { newProductionSpec } from './production-spec-model.mjs';
import { revisedSpec } from './production-spec-editor.mjs';

test('new staging uses assigned identities and leaves actions to the author',()=>{
 const cut={id:'cut-1',duration:5};
 const spec=newProductionSpec(cut,[{id:'a',kind:'image',owner:'Milo',role:'Персонаж целиком'},
 {id:'b',kind:'image',owner:'Milo',role:'Лицо героя'},
 {id:'c',kind:'image',owner:'',role:'Окружение'}],'f1');
 assert.equal(spec.characters.length,1);
 assert.deepEqual(spec.characters[0].reference_asset_ids,['a','b']);
 assert.equal(spec.shot.beats[0].observed_body_action,'');
 const saved=revisedSpec(spec,{shot:{purpose:'Watch'},characters:[{identity:'Adult cat'}],beats:spec.shot.beats},5);
 assert.equal(saved.shot.revision,1);assert.equal(saved.characters[0].revision,1);
 assert.equal(cut.productionSpec,undefined);
});
test('unassigned photographs cannot silently become an invented character',()=>{
 assert.throws(()=>newProductionSpec({id:'c',duration:5},[{id:'a',kind:'image',role:'Лицо героя'}]),/Назначь имя героя/);
});
