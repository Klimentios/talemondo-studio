// TaleMondo adapter; upstream FreeCut is MIT. No generator or paid API is invoked here.
const mount = () => {
 const panel=document.createElement('details'); panel.style.cssText='position:fixed;right:20px;bottom:20px;z-index:10000;background:#171719;color:#f5f5ef;border:1px solid #b49a65;border-radius:12px;padding:12px;max-width:340px;max-height:55vh;overflow:auto;font:13px system-ui';
 const summary=document.createElement('summary'); summary.textContent='TaleMondo · медиатека';summary.style.cursor='pointer';panel.append(summary);
 const body=document.createElement('div');panel.append(body);let loaded=false;
 panel.addEventListener('toggle',async()=>{
  if(!panel.open||loaded)return;body.textContent='Загрузка…';
  try {const r=await fetch('/api/montages/media');if(!r.ok)throw Error('Studio недоступна');const data=await r.json();body.replaceChildren();
   for(const item of [...data.shots,...data.files]){
    const button=document.createElement('button');button.textContent='＋ '+(item.name||item.original_prompt||item.id).slice(0,65);button.style.cssText='display:block;width:100%;margin-top:8px;padding:8px;background:#282628;color:inherit;border:1px solid #514735;border-radius:6px;text-align:left';
    button.onclick=async()=>{button.disabled=true;try {const {useMediaLibraryStore}=await import('@/features/media-library/stores/media-library-store');await useMediaLibraryStore.getState().importMediaFromUrl(new URL(item.url,location.origin).href);const error=useMediaLibraryStore.getState().error;if(error)throw Error(String(error));button.textContent='✓ Импортировано';}catch(e){button.textContent=String(e);button.disabled=false;}};body.append(button);
   }loaded=true;if(!body.childNodes.length)body.textContent='Нет готовых материалов';
  }catch(e){body.textContent=String(e);}
 });document.body.append(panel);
};if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();

// Native timeline actions: never hydrate a detached project over a user's live edit.
let lease='';let lastCommand='';
async function timelineSnapshot(){
 const {useProjectStore}=await import('@/features/projects/stores/project-store');
 const project=useProjectStore.getState().currentProject;if(!project||!location.pathname.startsWith('/editor'))throw Error('Откройте проект в редакторе');
 const {buildTimelineFromStores}=await import('@/features/timeline/stores/timeline-persistence');
 const timeline=buildTimelineFromStores();const json=JSON.stringify({id:project.id,timeline});
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(json));
 return {project_id:project.id,revision:Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join(''),timeline};
}
async function pollAgent(){
 if(document.hidden){setTimeout(pollAgent,2000);return;}
 try{
 const response=await fetch('/bridge/poll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lease})});if(!response.ok)return;
 const data=await response.json();lease=data.lease;const cmd=data.command;if(!cmd||cmd.id===lastCommand)return;lastCommand=cmd.id;
 let result;
 try{
  if(cmd.action==='timeline_get')result=await timelineSnapshot();
  else {
   const before=await timelineSnapshot();
   if(cmd.project_id!==before.project_id||cmd.revision!==before.revision)throw Error('409: проект или версия изменились; прочитайте таймлайн заново');
   if(!Array.isArray(cmd.ops)||cmd.ops.length!==1)throw Error('Передайте одну операцию');
   const op=cmd.ops[0];if(!['moveItem','split','trimStart','trimEnd','removeItems','addTransition','addText'].includes(op.op))throw Error('Операция не разрешена');
   const {applyOp}=await import('@/headless/edit-ops');
   // Recheck after lazy loading: user may have edited while the chunk loaded.
   const current=await timelineSnapshot();if(current.revision!==before.revision)throw Error('409: таймлайн изменился');
   const detail=applyOp(op);
   const {saveTimeline}=await import('@/features/timeline/stores/timeline-persistence');await saveTimeline(before.project_id);
   result={detail,...await timelineSnapshot()};
  }
 }catch(error){result={error:String(error)};}
 await fetch('/bridge/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lease,id:cmd.id,result})});
 }catch{}finally{setTimeout(pollAgent,2000);}
}
void pollAgent();
