import {applyBoardOperations, applyDirectorRecommendations, checkScenePlan, cutsFromScenePlan, directorReview, planForReview} from './scene-director-model.mjs?v=20261031-take-edit-2';
import {sceneWindows, sourceSceneCuts} from './long-scene.mjs?v=20261006-auto-2';
import {probeReferenceVideoDuration} from './reference-duration.mjs?v=20261006-1';
import { errorText } from './api-error.mjs?v=20261008-1';

if (new URLSearchParams(location.search).get('view') === 'scenes') {
  const root = document.createElement('section');
  root.className = 'scene-idea storyboard-zone'; root.id = 'scene-idea';
  root.innerHTML = `<div class="scene-idea-heading"><div><span class="director-spark">✦</span><h2>AI Director</h2></div><span>Правки сначала показываются на проверку</span></div>
    <div class="scene-command-row"><input id="sceneCommand" type="text" maxlength="2000" placeholder="Например: добавь реакцию после шота 3, остальные шоты не меняй" aria-label="Команда AI Director" />
      <button id="sceneEditBuild" type="button" class="storyboard-primary">Подготовить правку</button></div>
    <details class="scene-idea-details"><summary>Описание сцены и создание новой раскадровки</summary>
      <label for="sceneIdea">Что происходит от начала до конца?</label>
      <textarea id="sceneIdea" rows="2" maxlength="20000" placeholder="Опиши событие, поворот и финал сцены…"></textarea>
      <div class="scene-idea-controls"><label>Целевая длительность <input id="sceneTarget" type="number" min="20" max="180" step="1" value="40" /> секунд</label>
      <label>Шотов <select id="sceneShotCount"><option value="">авто</option>${Array.from({length:11},(_, i) => `<option value="${i + 2}">${i + 2}</option>`).join('')}</select></label>
      <button id="scenePlanBuild" type="button">Создать новую раскадровку</button></div>
    </details>
    <p id="scenePlanStatus" class="scene-plan-status" role="status" aria-live="polite"></p>
    <div id="scenePlanResult" hidden></div><div id="sceneEditResult" hidden></div>`;
  const studio = () => window.h3Studio;
  const by = (id) => root.querySelector(`#${id}`);
  const status = (value, bad = false) => { by('scenePlanStatus').textContent = value; by('scenePlanStatus').classList.toggle('is-error', bad); };
  let timer;
  const save = () => {
    const state = studio().project().sceneDirector || {};
    studio().project().sceneDirector = {...state, idea:by('sceneIdea').value, targetSeconds:Number(by('sceneTarget').value),
      shotCount:Number(by('sceneShotCount').value) || null};
    void studio().saveDraft().catch(() => {});
  };
  function renderPlan() {
    const state = studio().project().sceneDirector || {};
    const plan = state.plan;
    const result = by('scenePlanResult');
    result.hidden = !plan;
    if (!plan) return;
    const reviews = plan.source_driven ? [] : directorReview(plan);
    const card = document.createElement('div'); card.className = 'scene-plan-summary';
    const arc = document.createElement('p'); arc.textContent = plan.dramatic_arc || '';
    const visual = document.createElement('small'); visual.textContent = `Визуальное правило: ${plan.visual_rule || 'не указано'}`;
    card.append(arc,visual);
    const list = document.createElement('ol'); list.className = 'scene-plan-list';
    const frameNames = {source_video:'Фрагмент видео',new_keyframe:'Новый кадр / cut',previous_end:'Продолжение'};
    const framing = {wide:'Общий',medium:'Средний',close:'Крупный',insert:'Деталь'};
    for (const [index, shot] of plan.shots.entries()) {
      const item = document.createElement('li'); item.className = 'scene-plan-shot';
      const number = document.createElement('b'); number.textContent = String(index + 1).padStart(2,'0');
      const detail = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = shot.title;
      const purpose = document.createElement('p'); purpose.textContent = shot.purpose;
      const action = document.createElement('small'); action.textContent = shot.action;
      detail.append(title,purpose,action);
      const meta = document.createElement('span'); meta.textContent = `${shot.duration} с · ${plan.source_driven ? 'По исходнику' : `${framing[shot.framing] || shot.framing} · ${shot.lens_mm} мм`} · ${frameNames[shot.start_frame]}`;
      item.append(number,detail,meta); list.append(item);
    }
    const review = document.createElement('div'); review.className = 'scene-director-review';
    const reviewTitle = document.createElement('strong'); reviewTitle.textContent = 'Режиссёрская проверка'; review.append(reviewTitle);
    const reviewText = document.createElement('p');
    reviewText.textContent = reviews.length ? reviews.map((note) => `• ${note.text}`).join('\n') : 'Структурных замечаний нет. Содержание и референсы проверь перед запуском.';
    review.append(reviewText);
    if(reviews.length) {
      const fix=document.createElement('button');fix.type='button';fix.textContent='Применить рекомендации';
      fix.addEventListener('click',()=>{
        const next=applyDirectorRecommendations(plan);
        studio().project().sceneDirector={...state,plan:next};
        void studio().saveDraft();renderPlan();status('Рекомендации применены. Проверь изменённые планы и связи шотов.');
      });
      review.append(fix);
    }
    const actions = document.createElement('div'); actions.className = 'scene-plan-actions';
    const apply = document.createElement('button'); apply.type='button'; apply.className='storyboard-primary'; apply.textContent='Применить план к раскадровке';
    apply.addEventListener('click', async () => {
      try { await applyPlan(plan, state); } catch (error) { status(error.message,true); }
    });
    // One press for the whole scene: plan → board → «Собрать сцену» (keyframes, shots, AI checks).
    const applyRun = document.createElement('button'); applyRun.type='button'; applyRun.textContent='Применить и собрать сцену ▶▶';
    applyRun.addEventListener('click', async () => {
      try { await applyPlan(plan, state); document.querySelector('#scene-assembly .storyboard-primary')?.click(); }
      catch (error) { status(error.message,true); }
    });
    actions.append(apply, applyRun); result.replaceChildren(card,list,review,actions);
  }
  function renderEdit() {
    const state = studio().project().sceneDirector || {};
    const pending = state.pendingEdit;
    const result = by('sceneEditResult');
    result.hidden = !pending;
    if (!pending) return;
    const card = document.createElement('div'); card.className = 'scene-edit-review';
    const summary = document.createElement('p'); summary.textContent = pending.summary || 'Предложены точечные изменения доски.';
    const list = document.createElement('ul');
    const shotNames = new Map(studio().cuts().map((cut,index) => [cut.id,cut.sceneShot?.title || `Шот ${index + 1}`]));
    const changeLabels = {title:'Название',action:'Действие',duration:'Длительность',framing:'Крупность',lens_mm:'Объектив',camera_motion:'Движение камеры',start_frame:'Связь кадров'};
    const frameNames = {new_keyframe:'Новый кадр',previous_end:'Продолжение предыдущего'};
    const framingNames = {wide:'Общий',medium:'Средний',close:'Крупный',insert:'Деталь'};
    const movementNames = {static:'Статика',dolly:'Наезд / отъезд',tracking:'Следование',truck:'Боковой проезд',orbit:'Облёт',pan:'Панорама',tilt:'Наклон'};
    for (const operation of pending.operations || []) {
      const item = document.createElement('li');
      if (operation.op === 'add_after') item.textContent = `Добавить «${operation.shot.title}» · ${operation.shot.duration} с · ${operation.after_shot_id ? `после ${shotNames.get(operation.after_shot_id) || 'шота'}` : 'в конец доски'}`;
      else item.textContent = `Изменить ${shotNames.get(operation.shot_id) || 'шот'}: ${Object.entries(operation.changes).map(([key,value]) => {
        const display = key === 'framing' ? framingNames[value] || value : key === 'camera_motion' ? movementNames[value] || value
          : key === 'start_frame' ? frameNames[value] || value : key === 'duration' ? `${value} с` : value;
        return `${changeLabels[key] || key} — ${display}`;
      }).join(' · ')}`;
      list.append(item);
    }
    const actions = document.createElement('div'); actions.className = 'scene-plan-actions';
    const discard = document.createElement('button'); discard.type='button'; discard.className='scene-edit-discard'; discard.textContent='Отменить';
    discard.addEventListener('click', async () => { const current=studio().project().sceneDirector||{}; delete current.pendingEdit; studio().project().sceneDirector=current; await studio().saveDraft(); renderEdit(); status('Предложенная правка отменена. Доска не менялась.'); });
    const apply = document.createElement('button'); apply.type='button'; apply.className='storyboard-primary'; apply.textContent='Применить к доске';
    apply.addEventListener('click', async () => {
      try {
        const next = applyBoardOperations(studio().cuts(),pending.operations);
        studio().editCuts(() => next);
        const current=studio().project().sceneDirector||{}; delete current.pendingEdit; studio().project().sceneDirector=current;
        await studio().saveDraft(); window.h3Storyboard?.refresh(); renderEdit();
        status(`Правка применена: доска обновлена, затронуто операций — ${pending.operations.length}.`);
      } catch (error) { status(error.message,true); }
    });
    actions.append(discard,apply); card.append(summary,list,actions); result.replaceChildren(card);
  }
  async function buildEdit() {
    const command = by('sceneCommand').value.trim();
    if (command.length < 8) { status('Опиши конкретную правку доски минимум в 8 символах.',true); return; }
    const cuts = studio().cuts();
    if (!cuts.length) { status('Сначала добавь хотя бы один шот.',true); return; }
    const button=by('sceneEditBuild'); button.disabled=true; status('AI Director читает сцену и готовит точечную правку…');
    try {
      const selected = [...document.querySelectorAll('.storyboard-mini.is-selected')].map((node) => node.dataset.id).filter(Boolean);
      const shots = cuts.map((cut,index) => ({id:cut.id,title:cut.sceneShot?.title || `Шот ${index + 1}`,
        action:(cut.prompt || '').slice(0,700),duration:Number(cut.duration) || 5,
        framing:cut.sceneShot?.framing || 'medium',lens_mm:Number(cut.sceneShot?.lens_mm) || 50,
        camera_motion:cut.sceneShot?.camera_motion || 'Статично',
        start_frame:cut.startFrame?.source === 'previous' ? 'previous_end' : 'new_keyframe'}));
      const references=studio().references().slice(0,12).map((ref) => ({asset_id:ref.id,number:ref.number,kind:ref.kind,role:ref.role||'',owner:ref.owner||'',name:ref.name||''}));
      const response=await fetch('/api/scene-board-edits',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({command,shots,selected_shot_ids:selected,style:studio().project().sceneStyle||'',references})});
      const data=await response.json().catch(()=>({}));
      if(!response.ok) throw new Error(response.status===404&&!data.detail?'Точечные AI-правки появятся после обновления Studio и GPU API.':errorText(data, response.status));
      const state=studio().project().sceneDirector||{};
      studio().project().sceneDirector={...state,pendingEdit:{...data,command,createdAt:new Date().toISOString()}};
      await studio().saveDraft(); renderEdit(); status('Предложение готово. Проверь список операций и отдельно примени его к доске.');
    } catch(error) { status(error.message,true); }
    finally { button.disabled=false; }
  }
  async function applyPlan(plan, state) {
    const refs = studio().references();
    const project = studio().project();
    // Keep the previous board available for recovery instead of discarding job links.
    project.sceneDirector = {...state, previousCuts:structuredClone(studio().cuts())};
    studio().editCuts(cuts => plan.source_driven
      ? sourceSceneCuts(plan, state.idea, cuts, refs)
      : sceneWindows(cutsFromScenePlan(plan,cuts).map(cut => ({...cut,
          // «Собрать сцену» makes these keyframes itself; with that switched off a new plan starts free.
          startFrame:cut.startFrame?.source === 'keyframe' && project.storyboard?.autoKeyframes === false ? null : cut.startFrame})), refs));
    project.sceneDirector = {...project.sceneDirector, plan, autoPrepare:false, appliedAt:new Date().toISOString()};
    await studio().saveDraft();
    window.h3Storyboard?.refresh();
    status(`Раскадровка готова: ${plan.shots.length} шотов. Можно запускать все кадры.`);
    document.querySelector('#scene-board')?.scrollIntoView({behavior:'smooth',block:'start'});
  }
  async function build() {
    const idea = by('sceneIdea').value.trim();
    const targetSeconds = Number(by('sceneTarget').value);
    if (idea.length < 20) { status('Опиши событие, поворот и финал хотя бы в 20 символах.',true); return; }
    if (!Number.isInteger(targetSeconds) || targetSeconds < 20 || targetSeconds > 180) { status('Цель сцены: от 20 до 180 секунд.',true); return; }
    const shotCount = Number(by('sceneShotCount').value) || null;
    if (shotCount && (targetSeconds < 10 * shotCount || targetSeconds > 15 * shotCount)) {
      status(`${shotCount} шотов по 10–15 с: длительность сцены от ${10 * shotCount} до ${15 * shotCount} секунд.`,true); return;
    }
    save(); by('scenePlanBuild').disabled=true; status('Агент проектирует драматургию и связи шотов… обычно 1–3 минуты');
    try {
      const references = studio().references();
      for (const ref of references.filter(ref => ref.kind === 'video' && ref.role !== 'Продолжение')) {
        const duration = await probeReferenceVideoDuration(studio().referenceUrl(ref.id));
        if (duration === null) throw new Error(`Не удалось прочитать длительность «${ref.name}»`);
        ref.sourceDuration = duration;
      }
      const refs = references.slice(0,12).map((ref) => ({asset_id:ref.id,number:ref.number,kind:ref.kind,role:ref.role || '',owner:ref.owner || '',name:ref.name || ''}));
      const response = await fetch('/api/scene-plans',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({idea,target_seconds:targetSeconds,style:studio().project().sceneStyle || '',references:refs,
          ...(shotCount ? {shot_count:shotCount} : {})})});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(response.status === 404 && !data.detail ? 'Планировщик появится после обновления локальной Studio.' : errorText(data, response.status));
      if (references.some(ref => ref.kind === 'video' && ref.role !== 'Продолжение') && !data.source_driven)
        throw new Error('Сервер ещё не подключил анализ кадров видео. Текущая раскадровка сохранена; обновление будет применено после рендера.');
      checkScenePlan(data,targetSeconds);
      if (shotCount && data.shots.length !== shotCount) throw new Error(`Агент дал ${data.shots.length} шотов вместо ${shotCount}. Нажми ещё раз.`);
      studio().project().sceneDirector = planForReview(studio().project().sceneDirector || {},data,idea,targetSeconds);
      await studio().saveDraft();
      renderPlan();
      status(`План готов: ${data.shots.length} шотов. Текущая доска не менялась — проверь план и примени его отдельно.`);
    } catch (error) { status(error.message,true); }
    finally { by('scenePlanBuild').disabled=false; }
  }
  function install() {
    const scene = document.querySelector('#sceneRail');
    if (!scene || !studio()?.ready?.()) { setTimeout(install,150); return; }
    const anchor = scene.querySelector('.scene-entity-rail') || scene.querySelector('.scene-rail-head');
    if (anchor) anchor.after(root); else scene.prepend(root);
    const state = studio().project().sceneDirector || {};
    by('sceneIdea').value = state.idea || '';
    by('sceneTarget').value = state.targetSeconds || 40;
    by('sceneShotCount').value = state.shotCount ? String(state.shotCount) : '';
    const ideaInput = by('sceneIdea');
    let ideaSummary = root.querySelector('.scene-idea-summary');
    if (!ideaSummary) {
      const wrap = document.createElement('div'); wrap.className = 'scene-idea-input-wrap';
      ideaSummary = document.createElement('button'); ideaSummary.type = 'button'; ideaSummary.className = 'scene-idea-summary';
      ideaSummary.textContent = 'Описание сцены сохранено · нажми, чтобы редактировать';
      ideaInput.before(wrap); wrap.append(ideaInput, ideaSummary);
      ideaSummary.addEventListener('click', () => ideaInput.focus());
    }
    const syncIdeaSummary = () => { ideaSummary.hidden = !ideaInput.value.trim() || document.activeElement === ideaInput; };
    ideaInput.addEventListener('focus', syncIdeaSummary); ideaInput.addEventListener('blur', syncIdeaSummary);
    syncIdeaSummary();
    by('sceneIdea').addEventListener('input', () => { clearTimeout(timer); timer=setTimeout(save,500); });
    by('sceneTarget').addEventListener('change',save);
    by('sceneShotCount').addEventListener('change',save);
    by('scenePlanBuild').addEventListener('click',build);
    by('sceneEditBuild').addEventListener('click',buildEdit);
    by('sceneCommand').addEventListener('keydown',(event)=>{ if(event.key==='Enter'){event.preventDefault();buildEdit();} });
    window.addEventListener('h3:scenes-changed', () => {
      const next = studio().project().sceneDirector || {};
      by('sceneIdea').value = next.idea || '';
      by('sceneTarget').value = next.targetSeconds || 40;
      by('sceneShotCount').value = next.shotCount ? String(next.shotCount) : '';
      syncIdeaSummary();
      renderPlan();
      renderEdit();
      window.dispatchEvent(new CustomEvent('h3:scene-director-ready'));
    });
    renderPlan();
    renderEdit();
    window.dispatchEvent(new CustomEvent('h3:scene-director-ready'));
    if (state.autoPrepare && !state.appliedAt) void build();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',install);
  else install();
}
