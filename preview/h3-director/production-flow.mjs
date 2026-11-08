import {referencePreview} from './reference-preview.mjs?v=20261009-1';
import { errorText } from './api-error.mjs?v=20261008-1';
const terminal = new Set(['completed','failed','cancelled','needs_input','budget_exhausted']);
export function visibleProductions(rows,expanded=false){const ordered=[...rows.filter(r=>!terminal.has(r.state)),...rows.filter(r=>terminal.has(r.state))];return expanded?ordered:ordered.slice(0,8);}
export const productionState = {
  analyzing:'Подготовка сцены · очередь Mac', rendering:'Генерация и сохранение результата',
  awaiting_qc:'Ролик сохранён · ожидает визуальной проверки', checking:'Проверка выбранных кадров и референсов',
  completed:'Результат готов к просмотру', failed:'Этап завершился ошибкой', needs_input:'Нужно уточнить задачу',
  budget_exhausted:'Достигнут лимит выполнения', cancelling:'Остановка следующих этапов', cancelled:'Остановлено',
};
async function api(url,body) {
  const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json().catch(()=>({}));
  if(!response.ok) { const error=new Error(errorText(data,response.status));error.status=response.status;throw error; }
  return data;
}
export function productionPayload(snapshot,settings,window=null) {
  const merged={...snapshot.settings,...settings};
  const reference_windows={};
  if(window){
    if(window.duration!==Number(merged.duration)) throw new Error('Длительность участка изменилась. Перестрой план сцены.');
    for(const id of window.assetIds){
      if(!snapshot.references.some(ref=>ref.asset_id===id && ref.kind==='video')) throw new Error('Исходное видео участка отсутствует.');
      reference_windows[id]={start:window.start,duration:window.duration};
    }
  }
  return {...snapshot,settings:merged,reference_windows,max_generation_jobs:4,max_execution_seconds:14400};
}
export async function submitProduction(payload) {
  // Keep the same request ID if the browser loses an acceptance response.
  const encoded=JSON.stringify(payload), key='studio-production-submit';
  let pending;try{pending=JSON.parse(localStorage.getItem(key)||'null');}catch{}
  if(pending && pending.encoded!==encoded) throw new Error('Предыдущая отправка требует сверки. Верни прежний запрос или проверь историю производства перед новой отправкой.');
  pending ||= {id:crypto.randomUUID(),encoded};
  localStorage.setItem(key,JSON.stringify(pending));
  try {
    const row=await api('/api/productions',{...payload,client_request_id:pending.id});
    localStorage.removeItem(key);
    window.dispatchEvent(new CustomEvent('studio:production'));
    return row;
  } catch(error) {
    // Validation rejection proves nothing was accepted; network/5xx keeps the immutable ID.
    if ([400,404,405,409,413,422].includes(error.status)) localStorage.removeItem(key);
    throw error;
  }
}
function node(tag,props={},text=''){return Object.assign(document.createElement(tag),props,{textContent:text});}
export function installProductionMonitor(before) {
  const box=node('section',{className:'production-monitor',ariaLabel:'Автономное производство'});
  box.append(node('h3',{},'Производство'));
  const list=node('div');box.append(list);box.hidden=true;before.before(box);
  let timer, busy=false, last='',expanded=false;
  async function poll() {
    if(busy)return;busy=true;
    try {
      const rows=await api('/api/productions');
      let pending;try{pending=JSON.parse(localStorage.getItem('studio-production-submit')||'null');}catch{}
      if(pending && rows.some(row=>row.id===pending.id)){localStorage.removeItem('studio-production-submit');pending=null;}
      const fingerprint=JSON.stringify([rows,pending]);
      if(fingerprint!==last){last=fingerprint;box.hidden=!rows.length&&!pending;list.replaceChildren();
        if(pending){const notice=node('p',{},'Проверяем сохранённую отправку: повтор использует тот же ID.'),resume=node('button',{type:'button'},'Восстановить отправку');
          resume.onclick=async()=>{resume.disabled=true;try{await submitProduction(JSON.parse(pending.encoded));last='';await poll();}catch(e){notice.textContent=e.message;resume.disabled=false;}};list.append(notice,resume);}
        for(const row of visibleProductions(rows,expanded)){
          const card=node('article',{className:'production-card'});
          card.append(node('strong',{},row.brief.slice(0,140)),node('p',{},productionState[row.state]||row.state));
          if(row.error)card.append(node('p',{role:'status'},row.error));
          const qc=row.result?.quality?.visual;
          if(qc)card.append(node('p',{},qc.status==='reviewed'?`Проверка выборки кадров: ${qc.report.finding} Это не проверка всех кадров и не автоматическая приёмка.`:`Визуальная проверка: ${qc.finding||'выполняется'}`));
          const details=node('details');details.append(node('summary',{},'План и ограничения'),
            node('p',{},row.recipe.reason),node('p',{},`Генераций: ${row.used_jobs}/${row.max_jobs}. Автоповтор при неопределённой отправке отключён.`));
          for(const ref of row.recipe.prepared_references||[]) details.append(node('p',{},`Подготовлен участок исходного видео: ${ref.start}–${ref.start+ref.duration} с. Оригинал сохранён.`));
          if(row.references?.length)details.append(referencePreview(row.references,{title:'Материалы задания'}));
          details.addEventListener('toggle',()=>{if(!details.open)for(const media of details.querySelectorAll('video,audio'))media.pause();});
          card.append(details);
          if(row.state==='completed'){
            const url=`/api/jobs/${row.child_id}/video`;
            card.append(node('video',{src:url,controls:true,preload:'metadata'}),node('p',{},'Файл прошёл технические проверки очереди. Идентичность и художественное качество требуют просмотра.'));
          }
          if(!terminal.has(row.state)){
            const cancel=node('button',{type:'button'},'Остановить дальнейшие этапы');
            cancel.onclick=async()=>{cancel.disabled=true;try{await api(`/api/productions/${row.id}/cancel`,{});last='';await poll();}catch(e){cancel.disabled=false;card.append(node('p',{},e.message));}};card.append(cancel);
          }
          list.append(card);
        }
        if(rows.length>8){const more=node('button',{type:'button'},expanded?'Свернуть историю':`Показать все задачи (${rows.length})`);more.onclick=()=>{expanded=!expanded;last='';poll();};list.append(more);}
      }
    }catch(e){if(!box.hidden)list.setAttribute('aria-label',e.message);}
    finally{busy=false;clearTimeout(timer);if(!document.hidden)timer=setTimeout(poll,4000);}
  }
  window.addEventListener('studio:production',poll);
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden)poll();});
  poll();return poll;
}

export function bindProductionMode({before,getProject,save}) {
  const label=node('label',{className:'production-mode'}),select=node('select',{ariaLabel:'Способ выполнения'});
  select.append(node('option',{value:'manual'},'Текущий процесс'),node('option',{value:'automatic'},'Автономно · новый процесс'));
  // Existing/approved jobs retain their original path until GPU A/B rollout.
  select.value=getProject().productionMode||'manual';
  label.append(node('span',{},'Выполнение'),select);before.before(label);
  select.onchange=()=>{getProject().productionMode=select.value;save();};
  return {enabled:()=>getProject().productionMode==='automatic',refresh:()=>{select.value=getProject().productionMode||'manual';}};
}
