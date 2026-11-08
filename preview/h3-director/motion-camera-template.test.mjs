import test from 'node:test';
import assert from 'node:assert/strict';
import { motionCameraTemplate } from './motion-camera-template.mjs';
const refs = [
 {id:'face',kind:'image',role:'Лицо героя',owner:'MIRA'},
 {id:'suit',kind:'image',role:'Костюм',owner:'MIRA'},
 {id:'dance',kind:'video',role:'Движение и камера',owner:'MIRA'}
];
test('compact template separates appearance from source performance and scene',()=>{
 const text=motionCameraTemplate(refs);
 assert.match(text,/MIRA, whose identity and visible anatomical features come from <Picture 1> and whose costume comes from <Picture 2>/);
 assert.match(text,/gestures, facial expressions/);
 assert.match(text,/camera path, shot sizes, lens perspective/);
 assert.match(text,/Create an original unbranded location and lighting/);
 assert.match(text,/anatomy, build, skin and hands/);
 assert.match(text,/source performer supplies the performance, not the character's appearance/);
 assert.ok(text.split(/\s+/).length<450);
});
test('scene references override only assigned source attributes',()=>{
 const text=motionCameraTemplate([...refs,{id:'room',kind:'image',role:'Окружение'},{id:'light',kind:'image',role:'Свет'}]);
 assert.match(text,/Use <Picture 3> for location and set layout; create the remaining scene attributes independently/);
 assert.match(text,/Use <Picture 4> for lighting; create the remaining scene attributes independently/);
});
test('ambiguous target and wardrobe are rejected before replacing user text',()=>{
 assert.throws(()=>motionCameraTemplate(refs.map(r=>({...r,owner:''}))),/владельца/);
 assert.throws(()=>motionCameraTemplate(refs.map(r=>r.id==='suit'?{...r,owner:'OTHER'}:r)),/костюм/);
});
test('video soundtrack audio labels do not collide with attached music',()=>{
 const text=motionCameraTemplate([...refs,{id:'song',kind:'audio',role:'Музыка'}],'',{useVideoAudio:true});
 assert.match(text,/<Audio 1> is the soundtrack of <Video 1>/);
 assert.match(text,/Use <Audio 2> for music/);
 assert.match(text,/reference generation \+ audio reference \+ audio reuse/);
});

test('own-location variant requires an explicit location photo',async()=>{
 const {motionReferenceTemplate}=await import('./motion-camera-template.mjs');
 assert.throws(()=>motionReferenceTemplate(refs,'',{variant:'motion_camera_scene'}),/локации/);
 const text=motionReferenceTemplate([...refs,{id:'room',kind:'image',role:'Окружение'}],'',{variant:'motion_camera_scene'});
 assert.match(text,/Use <Picture 3> for location and set layout/);
 assert.match(text,/Preserve the source camera path/);
});
test('motion-only keeps own camera and has no source scene or camera authority',async()=>{
 const {motionReferenceTemplate}=await import('./motion-camera-template.mjs');
 const text=motionReferenceTemplate(refs,'',{variant:'motion_only',cameraBrief:'Use a fixed wide camera.',sceneBrief:'Use a warm window light.'});
 assert.match(text,/Use a fixed wide camera/);
 assert.match(text,/Use a warm window light/);
 assert.match(text,/choreography, timing, gestures and facial expressions only/);
 assert.doesNotMatch(text,/Preserve the source camera path|Keep the source location|within its original camera/);
});
test('single explicitly selected hero owns an unnamed costume despite an old prompt alias',()=>{
 const text=motionCameraTemplate(refs.map(r=>r.id==='suit'?{...r,owner:''}:r),'<Subject 1> is OLD NAME.');
 assert.match(text,/<Subject 1> is MIRA/);
 assert.match(text,/costume comes from <Picture 2>/);
 assert.doesNotMatch(text,/OLD NAME/);
});
