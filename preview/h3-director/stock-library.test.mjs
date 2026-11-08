import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {STOCK_CATALOG} from './stock-library.mjs';

test('curated catalog has local images, explicit roles and traceable origins',()=>{
  const ids=new Set();
  for(const entry of STOCK_CATALOG){
    assert.ok(entry.id && entry.title && entry.category && entry.license);
    assert.ok(!ids.has(entry.id),`duplicate catalog id ${entry.id}`);ids.add(entry.id);
    assert.equal(entry.role,{locations:'Окружение',props:'Предмет',clothes:'Костюм',styles:'Стиль'}[entry.type]);
    const url=new URL(entry.image,import.meta.url);
    const bytes=readFileSync(fileURLToPath(url));
    assert.ok(bytes.length>5000,`missing or empty image ${entry.id}`);
    assert.equal(bytes[0],0xff);assert.equal(bytes[1],0xd8);
    if(entry.origin==='STUDIO')assert.equal(entry.source,'');
    else assert.match(entry.source,/^https:\/\//);
  }
  assert.equal(ids.size,64);
  assert.deepEqual(Object.fromEntries(['locations','props','clothes','styles'].map(type=>[type,STOCK_CATALOG.filter(item=>item.type===type).length])),
    {locations:22,props:21,clothes:10,styles:11});
  assert.ok(STOCK_CATALOG.filter(item=>item.type==='props').every(item=>item.id.startsWith('prop-')));
  assert.ok(STOCK_CATALOG.filter(item=>item.type==='clothes').every(item=>item.id.startsWith('costume-')));
});
