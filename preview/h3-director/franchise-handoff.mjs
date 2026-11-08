import { validateFranchisePacket } from './franchise-packet.mjs?v=20261106-refmatch';
const PREFIX = 'studio.franchise.packet.';
export function stageFranchisePacket(packet) {
  validateFranchisePacket(packet);
  localStorage.setItem(PREFIX + packet.id, JSON.stringify(packet));
  return `/?franchisePacket=${encodeURIComponent(packet.id)}`;
}
export function installFranchiseHandoff(studio) {
  const id = new URL(location.href).searchParams.get('franchisePacket');
  if (!id) return;
  let packet;
  try { packet = validateFranchisePacket(JSON.parse(localStorage.getItem(PREFIX + id))); }
  catch (error) { const alert = document.createElement('p');alert.className='draft-stale-bar';alert.setAttribute('role','alert');alert.textContent='Не удалось открыть сцену франшизы: '+error.message;document.body.append(alert);return; }
  const sheet=document.createElement('section');sheet.className='franchise-handoff';sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');sheet.setAttribute('aria-label','Сцена из франшизы');
  const heading=document.createElement('h2');heading.textContent='Сцена из франшизы';sheet.append(heading);
  const title=document.createElement('strong');title.textContent=packet.title;sheet.append(title);
  const explain=document.createElement('p');explain.textContent='Проверь описание, роли и владельцев материалов. Сцена добавится в проект с собственными референсами. Генерацию запускаешь в Studio.';sheet.append(explain);
  const prompt=document.createElement('textarea');prompt.value=packet.prompt;prompt.rows=10;prompt.maxLength=20000;prompt.setAttribute('aria-label','Описание сцены из франшизы');
  if(packet.shots){const note=document.createElement('p');note.textContent='4 промпта по 10 секунд. Непрерывные шоты начинаются с последнего кадра; при монтажном скачке он служит референсом окружения и композиции.';sheet.append(note);prompt.readOnly=true;}
  sheet.append(prompt);
  const refs=document.createElement('div');refs.className='franchise-handoff-refs';sheet.append(refs);
  let readyRoles=false;
  const controls=[];
  for (const ref of packet.references) {
    const row=document.createElement('div');row.className='franchise-handoff-ref';
    const link=document.createElement('a');link.textContent=`${({image:'Фото',video:'Видео',audio:'Аудио'})[ref.kind]} ${ref.number} · ${ref.name || ref.id}`;link.href=`/api/assets/${encodeURIComponent(ref.id)}/file`;link.target='_blank';link.rel='noreferrer';row.append(link);
    const role=document.createElement('select');role.setAttribute('aria-label',`Роль ${ref.name || ref.id}`);
    const owner=document.createElement('input');owner.value=ref.owner;owner.maxLength=80;owner.placeholder='Владелец материала';owner.setAttribute('aria-label',`Владелец ${ref.name || ref.id}`);row.append(role,owner);
    const hint=document.createElement('small');hint.textContent=ref.ownerCandidates?.length>1?'Этот файл связан с несколькими персонажами. Укажи одного владельца или оставь пустым для общего материала.':'';row.append(hint);
    refs.append(row);controls.push({ref,role,owner});
  }
  const error=document.createElement('p');error.setAttribute('role','alert');sheet.append(error);
  const actions=document.createElement('div');actions.className='franchise-handoff-actions';
  const close=document.createElement('button');close.textContent='Закрыть';close.onclick=()=>{for(const [element,wasInert] of background) element.inert=wasInert;sheet.remove();const url=new URL(location.href);url.searchParams.delete('franchisePacket');history.replaceState(null,'',url);};
  const apply=document.createElement('button');apply.textContent='Добавить как новую сцену';apply.disabled=true;actions.append(close,apply);sheet.append(actions);const background=[...document.body.children].map(element=>[element,element.inert]);for(const [element] of background)element.inert=true;document.body.append(sheet);prompt.focus();
  let saving=false, completed=false;
  const timer=setInterval(()=>{
    if(!sheet.isConnected){clearInterval(timer);return;}
    if(studio.ready()&&!readyRoles){
      const roles=studio.referenceRoles();
      for(const {ref,role} of controls){for(const value of roles[ref.kind]||[]){const option=new Option(value,value);role.add(option);}role.value=ref.role || role.options[0]?.value || '';}
      readyRoles=true;
    }
    apply.disabled=saving||!studio.ready()||!readyRoles||!studio.canWriteDraft();
    if(studio.ready()&&!studio.canWriteDraft())error.textContent='Проект изменён в другой вкладке Studio. Обнови страницу перед добавлением сцены.';
  },200);
  apply.onclick=async()=>{
    if(completed){close.click();return;}
    if(saving)return;saving=true;prompt.disabled=true;for(const {role,owner} of controls){role.disabled=true;owner.disabled=true;}apply.disabled=true;close.disabled=true;error.textContent='Сохраняется…';
    try {
      const references=controls.map(({ref,role,owner})=>({...ref,role:role.value,owner:owner.value.trim(),manualOwner:true}));
      const heroRoles=['Лицо героя','Персонаж целиком','Костюм','Актёрская игра','Движение','Движение и камера','Голос'];
      if(references.some(ref=>(heroRoles.includes(ref.role)||(ref.ownerCandidates?.length>1&&ref.role.startsWith('Авто')))&&!ref.owner))throw new Error('Укажи владельца каждого материала персонажа или выбери общую роль.');
      await studio.importFranchiseScene({...packet,prompt:prompt.value,references});
      error.textContent='Сцена сохранена в Studio. Можно закрыть это окно и продолжить работу.';completed=true;apply.textContent='Продолжить в Studio';
    }catch(e){error.textContent=e.message;}
    finally{saving=false;close.disabled=false;const imported=studio.project().scenes.some(scene=>scene.franchiseSource?.packet_id===packet.id);if(!imported){prompt.disabled=false;for(const {role,owner} of controls){role.disabled=false;owner.disabled=false;}}else if(!completed){error.textContent+=' Повторное нажатие сохранит уже добавленную сцену; после сохранения её можно редактировать в Studio.';}}
  };
  sheet.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.stopPropagation();if(!saving)close.click();}
    if(event.key==='Tab'){
      const items=[...sheet.querySelectorAll('button:not(:disabled),input,select,textarea,a[href]')];
      if(event.shiftKey&&document.activeElement===items[0]){event.preventDefault();items.at(-1)?.focus();}
      else if(!event.shiftKey&&document.activeElement===items.at(-1)){event.preventDefault();items[0]?.focus();}
    }
  });
}
