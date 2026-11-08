import test from 'node:test';
import assert from 'node:assert/strict';
import {referencePreviewItems} from './reference-preview.mjs';
test('previews use bound files and explicit role, owner and performer, not upload order',()=>{
 const refs=[{asset_id:'clip/a',kind:'video',number:1,role:'Движение',owner:'Алекс',performer:'человек справа'},
 {asset_id:'hero',kind:'image',number:1,role:'Персонаж целиком',owner:'Алекс'}];
 const items=referencePreviewItems(refs);assert.equal(items[0].url,'/api/assets/clip%2Fa/file');
 assert.equal(items[0].performer,'человек справа');assert.equal(items[1].owner,'Алекс');assert.match(items[1].label,/Фото 1/);
 assert.throws(()=>referencePreviewItems([{asset_id:'x',kind:'unknown'}]));assert.equal(refs[0].asset_id,'clip/a');
});
