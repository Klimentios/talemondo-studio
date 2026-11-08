import test from 'node:test';import assert from 'node:assert/strict';
import {promptSettingsPackage} from './prompt-settings-package.mjs';
test('writer package owns native settings and preserves adapters and removes stale grading',()=>{
 const text='subject_definitions:\nA hero\n```studio-settings\n{"version":1,"settings":{"duration":5,"steps":30}}\n```';
 const result=promptSettingsPackage(text,{duration:15,style_adapter:'auto',film_look:{contrast:.22}});
 assert.equal(result.settings.duration,5);assert.equal(result.settings.steps,30);
 assert.equal(result.settings.style_adapter,'auto');assert.equal(result.settings.film_look,null);
 assert.ok(!result.prompt.includes('studio-settings'));
 assert.throws(()=>promptSettingsPackage(text.replace('"duration":5','"turbo":true')),/Неподдерживаемые/);
});
