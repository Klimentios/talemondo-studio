// Idle scheduling fallback for WebKit. Native implementations remain untouched.
export function installIdleCallbacks(target) {
 if(typeof target.requestIdleCallback==='function')return;
 target.requestIdleCallback=(callback,options={})=>{
  const queued=target.performance.now();
  return target.setTimeout(()=>{
   const start=target.performance.now();
   callback({didTimeout:Number.isFinite(options.timeout)&&start-queued>=options.timeout,
    timeRemaining:()=>Math.max(0,8-(target.performance.now()-start))});
  },1);
 };
 target.cancelIdleCallback=id=>target.clearTimeout(id);
}
if(typeof window!=='undefined')installIdleCallbacks(window);
