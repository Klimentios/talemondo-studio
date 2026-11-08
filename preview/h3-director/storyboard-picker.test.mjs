import test from 'node:test';
import assert from 'node:assert/strict';
import { startFrameChoices } from './storyboard-picker.mjs';

test('start frame picker searches curated names and omits hidden copies', () => {
  const assets = [
    {asset_id:'a',kind:'image',sha256:'one',role:'Окружение',original_name:'raw.jpg'},
    {asset_id:'b',kind:'image',sha256:'one',role:'Окружение',original_name:'raw-copy.jpg'},
    {asset_id:'c',kind:'image',sha256:'two',role:'Костюм',original_name:'costume.jpg'},
    {asset_id:'d',kind:'video',sha256:'three',role:'Движение',original_name:'clip.mp4'},
  ];
  const library = {a:{name:'Киото · храмовая дорожка',type:'locations',category:'Город / Япония'},
    b:{name:'Копия Киото',type:'locations',category:'Город / Япония'},c:{hidden:true,type:'clothes'}};
  assert.deepEqual(startFrameChoices(assets,library,{query:'киото',type:'locations'}).map(({asset})=>asset.asset_id),['a']);
  assert.equal(startFrameChoices(assets,library).length,1);
});
