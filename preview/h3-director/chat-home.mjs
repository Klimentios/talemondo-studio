// The home owns presentation; submitting and attaching still use the existing chat pipeline.
let catalog;
const node = (tag, text, cls) => { const e = document.createElement(tag); if (text) e.textContent = text; if (cls) e.className = cls; return e; };
const paths = {
 brand:'M5 3 22 12 5 21ZM9 9l6 3-6 3Z',
 home:'M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9',
 layers:'m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 16l10 5 10-5',
 video:'M3 5h18v14H3Zm7 4 6 3-6 3Z',
 image:'M3 3h18v18H3Zm1 14 5-6 4 4 3-3 5 6M16 7h.01',
 audio:'M3 10v4m4-8v12m5-15v18m5-15v12m4-8v4',
 edit:'m7 7 14 14M7 17 21 3M8 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm0 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
 cube:'m12 2 9 5v10l-9 5-9-5V7Zm-9 5 9 5 9-5M12 12v10',
 folder:'M2 5h8l2 3h10v13H2Z',
 settings:'M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-8 0v6',
 plus:'M12 4v16M4 12h16',
 chat:'M3 4h18v14H9l-6 4ZM7 9h10M7 13h7',
};
function icon(name) { const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'); svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('fill','none'); svg.setAttribute('stroke','currentColor'); svg.setAttribute('stroke-width','1.5'); svg.setAttribute('stroke-linejoin','round'); svg.setAttribute('stroke-linecap','round'); svg.setAttribute('aria-hidden','true'); const path=document.createElementNS(svg.namespaceURI,'path'); path.setAttribute('d',paths[name]||paths.layers); svg.append(path); return svg; }
function link(title,href,glyph,cls='') { const a=node('a','',cls); a.href=href; a.append(icon(glyph),node('span',title)); return a; }
export function syncHomeMode(active) {
 document.body.classList.toggle('ac-home-mode',active);
 document.body.classList.remove('ac-history-open');
 document.querySelectorAll('[data-home-chrome]').forEach(e=>e.remove());
 const provider=document.getElementById('acProvider');
 const providerHost=active?document.querySelector('.ac-config-content'):document.querySelector('.ac-head-row');
 if(provider && providerHost && provider.parentElement!==providerHost) providerHost.prepend(provider);
 const input=document.getElementById('acInput'); input.placeholder=active?'Опиши видео, которое хочешь создать. Укажи героев, действие, стиль и атмосферу…':'Опишите идею, сцену или задачу для агента…';
 const send=document.getElementById('acSend'); send.querySelector('.home-send-label')?.remove();
 if(active) send.append(node('span','Начать создание','home-send-label'));
}
export function renderChatHome(host) {
 const home=node('section','','ac-home');
 const aside=node('aside','','home-navigation'); aside.dataset.homeChrome='';
 const brand=link('TaleMondo','/agent-chat.html?task=new','brand','home-brand'); const mark=node('img');mark.src='/assets/talemondo-mark.svg';mark.alt='';mark.width=32;mark.height=40;brand.querySelector('svg').replaceWith(mark);brand.append(node('small','AI VIDEO STUDIO'));  aside.append(brand);
 aside.append(link('Чат','/agent-chat.html?task=new','chat','is-active'),link('Аналитика каналов','/analytics/','audio','home-analytics'));
 const menu=node('details','','home-tool-menu'); menu.open=window.innerWidth>900;
 const summary=node('summary'); summary.append(icon('settings'),node('span','Инструменты и настройки')); menu.append(summary);
 menu.addEventListener('click',event=>{if(window.innerWidth>900&&event.target.closest('summary'))event.preventDefault();});
 window.addEventListener('resize',()=>{if(menu.isConnected&&window.innerWidth>900)menu.open=true;});
 for(const [caption,items] of [
  ['ГЕНЕРАЦИЯ',[['Создать','/index.html','plus'],['Видео','/index.html','video'],['Озвучка','/audio-studio.html','audio']]],
  ['ПРОЕКТЫ',[['Франшизы','/franchise-board/','layers'],['Студия','/?view=scenes','video'],['Редактор','/editor/','edit'],['Библиотека','/?view=assets','folder']]]
 ]) { menu.append(node('small',caption,'home-nav-caption')); for(const [label,href,glyph] of items) menu.append(link(label,href,glyph)); }
 aside.append(menu);
 const history=node('button','','home-history'); history.type='button'; history.append(icon('chat'),node('span','Мои разговоры')); history.onclick=()=>{const open=document.body.classList.toggle('ac-history-open'); history.setAttribute('aria-expanded',String(open));}; history.setAttribute('aria-expanded','false'); aside.append(history); document.body.append(aside);
 const top=node('div','','home-top-links'); top.dataset.homeChrome=''; top.append(link('Франшизы','/franchise-board/','layers'),link('Проекты','/?view=scenes','folder')); document.querySelector('.ac-head-row').prepend(top);
 const heading=node('h2','Начни создавать с '); heading.append(node('span','TaleMondo'));
 home.append(heading,node('p','Твоя AI-студия для кино, сериалов и вирусных роликов','ac-home-lead'));
 const cards=node('div','','ac-franchise-gallery'); cards.setAttribute('aria-label','Выберите франшизу для задачи'); home.append(cards);
 const tools=node('nav','','ac-home-tools'); tools.setAttribute('aria-label','Рабочие пространства');
 for(const [title,subtitle,href,glyph] of [['Сцена','Из идеи в шот','/?view=scenes','video'],['Франшизы','Миры и истории','/franchise-board/','layers'],['Материалы','Референсы и ассеты','/?view=assets','image'],['Озвучка','Голос и звук','/audio-studio.html','audio'],['Монтаж','Сборка и правка','/editor/','edit']]) { const a=link(title,href,glyph,'k-btn'); a.append(node('small',subtitle)); tools.append(a); }
 home.append(tools); host.append(home);
 const toolbar=node('div','','home-compose-tools'); toolbar.dataset.homeChrome='';
 const attach=node('button','','k-btn'); attach.type='button'; attach.append(icon('plus'),node('span','Фото, видео или звук')); attach.onclick=()=>document.querySelector('.ac-compose-row [aria-label="Прикрепить файл"]').click();
 const preset=node('button','','k-btn'); preset.type='button'; preset.append(icon('settings'),node('span','Параметры задачи')); preset.onclick=()=>{document.getElementById('acTaskOptions').open=!document.getElementById('acTaskOptions').open;};
 toolbar.append(attach,preset); document.querySelector('.ac-composer').prepend(toolbar);
 if(!catalog) catalog=fetch('/api/franchises').then(r=>{if(!r.ok) throw Error();return r.json();}).catch(()=>{catalog=null;return null;});
 catalog.then(list=>{
  if(!home.isConnected)return;
  if(!Array.isArray(list)){cards.append(node('p','Франшизы недоступны. Обновите страницу.'));return;}
  const sorted=[...list].sort((a,b)=>String(a.name).localeCompare(String(b.name),'ru'));
  for(const franchise of sorted){
   const button=node('button','','ac-franchise-card');button.type='button';button.title=`Выбрать ${franchise.name}`;
   const art=node('div','','ac-franchise-art'); art.append(icon('layers'),node('span',franchise.name,'home-art-title'));button.append(art,node('strong',franchise.name));
   button.onclick=()=>{const select=document.getElementById('acFranchise');select.value=franchise.id;select.dispatchEvent(new Event('change',{bubbles:true}));cards.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));document.getElementById('acTaskOptions').open=true; const summary=document.querySelector('#acTaskOptions summary'); summary.textContent=franchise.name+' · параметры задачи'; document.getElementById('acInput').focus();};button.setAttribute('aria-pressed',String(document.getElementById('acFranchise').value===franchise.id));cards.append(button);
   Promise.all([fetch(`/api/franchises/${encodeURIComponent(franchise.id)}/profile`).then(r=>r.ok?r.json():{}),fetch(`/api/franchises/${encodeURIComponent(franchise.id)}/entities`).then(r=>r.ok?r.json():[])]).then(([profile,entities])=>{
    const role=profile.roles?.find(r=>r.kind==='character'&&r.assets?.length);
    const asset=role?.assets.find(a=>a.role==='Персонаж целиком')?.asset_id||role?.assets[0]?.asset_id||entities.find(e=>e.asset_ids?.length)?.asset_ids[0];
    if(!asset)return;const img=node('img');img.alt=franchise.name;img.src=`/api/assets/${encodeURIComponent(asset)}/thumbnail`;img.onload=()=>{art.textContent='';art.append(img);};
   }).catch(()=>{});
  }
 });
}

if(typeof document !== 'undefined') {
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&document.body.classList.contains('ac-history-open')) { document.body.classList.remove('ac-history-open'); const trigger=document.querySelector('.home-history');trigger?.setAttribute('aria-expanded','false');trigger?.focus(); }});
 document.addEventListener('pointerdown',event=>{if(document.body.classList.contains('ac-history-open')&&!event.target.closest('.ac-side,.home-history')) {document.body.classList.remove('ac-history-open');document.querySelector('.home-history')?.setAttribute('aria-expanded','false');}});
}
