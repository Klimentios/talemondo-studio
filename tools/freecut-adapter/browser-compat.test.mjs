import {test} from 'node:test';import assert from 'node:assert/strict';
import {installIdleCallbacks} from './browser-compat.js';
test('fallback schedules and cancels work without replacing native implementation',()=>{
 let now=0,scheduled,id=0;const host={performance:{now:()=>now},setTimeout:fn=>(scheduled=fn,++id),clearTimeout:n=>{assert.equal(n,id);scheduled=null;}};
 installIdleCallbacks(host);let deadline;host.requestIdleCallback(d=>deadline=d,{timeout:5});now=6;scheduled();assert.equal(deadline.didTimeout,true);assert.equal(deadline.timeRemaining(),8);now=9;assert.equal(deadline.timeRemaining(),5);
 const next=host.requestIdleCallback(()=>assert.fail());host.cancelIdleCallback(next);assert.equal(scheduled,null);
 const native=()=>1;host.requestIdleCallback=native;installIdleCallbacks(host);assert.equal(host.requestIdleCallback,native);
});
