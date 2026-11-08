import test from 'node:test';
import assert from 'node:assert/strict';
import {collapseLibraryCopies,inferredLibraryCategory,inferredLibraryType,needsLibraryReview} from './library-quality.mjs';

test('semantic role gives a useful default category without overwriting user metadata',()=>{
  const asset={kind:'image',role:'Костюм',original_name:'image.png'};
  assert.equal(inferredLibraryType(asset),'clothes');
  assert.equal(inferredLibraryCategory(asset),'Костюмы');
  assert.equal(needsLibraryReview(asset,{name:'image.png',category:'Костюмы'}),true);
});

test('duplicate view retains one copy per source image and prefers a labeled copy',()=>{
  const assets=[
    {asset_id:'new',kind:'image',sha256:'same',role:'Окружение',original_name:'image.png'},
    {asset_id:'old',kind:'image',sha256:'same',role:'Окружение',original_name:'forest.jpg'},
    {asset_id:'prop',kind:'image',sha256:'same',role:'Предмет',original_name:'image.png'},
  ];
  const info=a=>({name:a.original_name,category:inferredLibraryCategory(a),type:inferredLibraryType(a)});
  const collapsed=collapseLibraryCopies(assets,info);
  assert.deepEqual(collapsed.assets.map(a=>a.asset_id),['old']);
  assert.equal(collapsed.collapsed,2);
  assert.equal(collapseLibraryCopies(assets,info,true).assets.length,3);
});

test('character body and wardrobe keep distinct purposes when sharing the same photo',()=>{
  const assets=[
    {asset_id:'body',kind:'image',sha256:'same',role:'Персонаж целиком',original_name:'body.png'},
    {asset_id:'body-copy',kind:'image',sha256:'same',role:'Персонаж целиком',original_name:'image.png'},
    {asset_id:'wardrobe',kind:'image',sha256:'same',role:'Костюм',original_name:'costume.png'},
  ];
  const info=a=>({name:a.original_name,category:inferredLibraryCategory(a),franchise:'Ирис и Мира',characterName:'Ирис',preferredRole:a.role});
  const result=collapseLibraryCopies(assets,info);
  assert.deepEqual(result.assets.map(a=>a.asset_id),['body','wardrobe']);
  assert.equal(result.collapsed,1);
});
