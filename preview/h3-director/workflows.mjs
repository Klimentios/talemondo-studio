// «Свои воркфлоу» (workflows.html): import a ComfyUI API graph, pick which inputs the form shows, run it on the
// GPU's ComfyUI and see the files. Server: local_studio/user_workflows.py; agents use the same API via MCP.
import { mountGraphEditor } from './workflow-graph-edit.mjs?v=20261108-graph-ops-1';
import { mountVersions } from './workflow-versions.mjs?v=20261108-graph-ops-1';

const $ = (id) => document.getElementById(id);
const state = { workflows: [], current: null, assets: [], jobs: [], timer: null };
const KIND_RU = { image: 'фото', video: 'видео', audio: 'звук', text: 'текст', seed: 'seed', int: 'целое', float: 'число' };
const STATE_RU = { queued_local: 'ждёт GPU', uploading: 'передаётся', queued_gpu: 'в очереди ComfyUI', running: 'идёт',
  downloading: 'скачивается', completed: 'готово', failed: 'ошибка', cancelled: 'отменено', needs_reconciliation: 'сверка' };
const ACTIVE = new Set(['queued_local', 'uploading', 'queued_gpu', 'running', 'downloading', 'needs_reconciliation']);

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, options = {}) {
  const response = await fetch(path, { headers: options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}, ...options });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : detail || `HTTP ${response.status}`);
  }
  return data;
}

function status(text, tone = '') {
  $('wfStatus').textContent = text;
  $('wfStatus').dataset.tone = tone;
}

async function loadList(selectId) {
  const data = await api('/api/workflows');
  state.workflows = data.workflows;
  const list = $('wfList');
  if (!state.workflows.length) {
    list.innerHTML = '<p class="wf-empty">Пока пусто. Импортируй JSON графа (в ComfyUI: Workflow → Export (API)) или нажми в ComfyUI «⇪ Воркфлоу в Studio», затем «Из ComfyUI».</p>';
    $('wfDetail').innerHTML = '';
    return;
  }
  list.innerHTML = state.workflows.map((wf) => `<button type="button" class="wf-item" data-id="${esc(wf.id)}">
    ${esc(wf.name)}<small>${wf.inputs.length} вход(ов) · ${wf.nodes} узлов${wf.check?.checked && !wf.check.ok ? ' · ⚠ не хватает узлов' : ''}</small></button>`).join('');
  list.querySelectorAll('.wf-item').forEach((button) => button.addEventListener('click', () => open(button.dataset.id)));
  const id = selectId || state.current?.id || new URLSearchParams(location.search).get('id') || state.workflows[0].id;
  await open(state.workflows.some((wf) => wf.id === id) ? id : state.workflows[0].id);
}

async function open(id) {
  state.current = await api(`/api/workflows/${encodeURIComponent(id)}`);
  history.replaceState(null, '', `?id=${encodeURIComponent(id)}`);
  $('wfList').querySelectorAll('.wf-item').forEach((button) => button.setAttribute('aria-current', String(button.dataset.id === id)));
  renderDetail();
  await loadJobs();
}

function checkLine(check) {
  if (!check) return '';
  if (!check.checked) return `<p class="wf-check">Проверка с ComfyUI не выполнена: ${esc(check.reason)}</p>`;
  if (check.ok) return '<p class="wf-check" data-ok="true">✓ Все узлы и значения есть на GPU-сервере</p>';
  const missing = check.missing_nodes?.length ? `Не хватает узлов: ${check.missing_nodes.map(esc).join(', ')}. ` : '';
  return `<p class="wf-check" data-ok="false">⚠ ${missing}${esc((check.problems || []).slice(0, 4).join('; '))}</p>`;
}

function mediaField(param) {
  const kind = param.kind;
  const options = state.assets.filter((asset) => asset.kind === kind)
    .map((asset) => `<option value="${esc(asset.asset_id)}">${esc(asset.original_name || asset.asset_id)}</option>`).join('');
  return `<div class="wf-media">
    <select class="k-field" data-key="${esc(param.key)}"><option value="">как в графе: ${esc(param.default)}</option>${options}</select>
    <label class="k-btn k-btn--ghost" title="Загрузить в библиотеку">＋<input type="file" hidden data-upload="${esc(param.key)}" accept="${kind}/*"></label>
    ${kind === 'image' ? '<img alt="" hidden>' : ''}</div>`;
}

function field(param) {
  const label = `<span>${esc(param.label)} · ${KIND_RU[param.kind]}</span>`;
  if (['image', 'video', 'audio'].includes(param.kind)) return `<label class="wf-field">${label}${mediaField(param)}</label>`;
  if (param.kind === 'text') return `<label class="wf-field wf-field--wide">${label}<textarea class="k-field" data-key="${esc(param.key)}">${esc(param.default)}</textarea></label>`;
  if (param.kind === 'seed') return `<label class="wf-field">${label}<input class="k-field" type="number" min="0" step="1" data-key="${esc(param.key)}" placeholder="случайный"></label>`;
  return `<label class="wf-field">${label}<input class="k-field" type="number" step="${param.kind === 'int' ? 1 : 'any'}" data-key="${esc(param.key)}" value="${esc(param.default)}"></label>`;
}

function renderDetail() {
  const wf = state.current;
  const exposed = wf.params.filter((p) => p.exposed);
  $('wfDetail').innerHTML = `
    <section class="k-panel wf-block">
      <div class="wf-head"><div><h2>${esc(wf.name)}</h2>
        <div class="wf-meta"><span>ревизия ${wf.revision}</span><span>${wf.nodes} узлов</span><span>${{ comfyui: 'из ComfyUI', job: wf.origin?.kind === 'recipe' ? `рецепт ${wf.origin.recipe_id}` : `задание ${String(wf.origin?.job_id || '').slice(0, 8)}` }[wf.source] || 'импорт'}</span>
        ${wf.note ? `<span title="Последняя правка">✎ ${esc(wf.note)}</span>` : ''}
        ${wf.models?.length ? `<span title="${esc(wf.models.join('\n'))}">модели: ${wf.models.length}</span>` : ''}</div></div>
        <div class="wf-actions"><a class="k-btn k-btn--ghost" href="/comfyui.html?wf=${encodeURIComponent(wf.id)}" title="Изменить связи и узлы в настоящем редакторе ComfyUI; «⇪ Воркфлоу в Studio» вернёт граф сюда">Открыть в ComfyUI</a>
        <button type="button" class="k-btn k-btn--ghost" id="wfRecheck">Проверить на GPU</button>
        <button type="button" class="k-btn k-btn--danger" id="wfDelete">Удалить</button></div></div>
      ${checkLine(wf.check)}
      <div class="wf-form" id="wfForm">${exposed.map(field).join('') || '<p class="wf-hint">Нет открытых входов: включи их в «Настроить форму».</p>'}</div>
      <div class="wf-run">
        <label class="wf-field"><span>Сколько вариантов</span><select class="k-field" id="wfCount"><option>1</option><option>2</option><option>3</option><option>4</option></select></label>
        <button type="button" class="k-btn k-btn--primary" id="wfRun">Сгенерировать</button>
      </div>
    </section>
    <details class="k-panel wf-block" id="wfGraphBlock"><summary>Граф · ${wf.nodes} узлов</summary>
      <p class="wf-hint">Нажми на узел: значения (шаги, семплер, шедулер, сдвиг, разрешение, сила LoRA, файл референса) и связи — из схемы
      живого ComfyUI. «＋ Узел», «＋ LoRA», «Заменить…», «Удалить» меняют состав графа. Каждая правка сразу проверяется по <code>/object_info</code>;
      «Сохранить» пишет новую ревизию, прежняя остаётся в «Версиях».</p>
      <div id="wfGraph"></div>
    </details>
    <details class="k-panel wf-block" id="wfVersionsBlock"><summary>Версии · ревизия ${wf.revision}</summary><div id="wfVersions"></div></details>
    <details class="k-panel wf-block"><summary>Настроить форму</summary>
      <p class="wf-hint">Отметь, какие входы графа показывать в форме, API и агентам. Остальные остаются как в графе.</p>
      <table class="wf-params">${wf.params.map((p) => `<tr><td><input type="checkbox" data-param="${esc(p.key)}" ${p.exposed ? 'checked' : ''}></td>
        <td><input class="k-field" data-label="${esc(p.key)}" value="${esc(p.label)}"></td><td>${KIND_RU[p.kind]}</td><td><small>${esc(p.key)} = ${esc(String(p.default).slice(0, 40))}</small></td></tr>`).join('')}</table>
      <div class="wf-run"><button type="button" class="k-btn k-btn--accent" id="wfSaveForm">Сохранить форму</button></div>
    </details>
    <details class="k-panel wf-block wf-api"><summary>API и агенты</summary>
      <p class="wf-hint">Агент Studio: инструменты <code>workflow_get</code> и <code>workflow_run</code> (id <code>${esc(wf.id)}</code>). Из скрипта:</p>
      <pre>curl -X POST http://127.0.0.1:18187/api/workflows/${esc(wf.id)}/run \\
  -H 'Content-Type: application/json' \\
  -d '${esc(JSON.stringify({ values: Object.fromEntries(exposed.map((p) => [p.key, ['image', 'video', 'audio'].includes(p.kind) ? '<asset_id>' : p.kind === 'seed' ? 42 : p.default])), count: 1 }))}'</pre>
    </details>
    <section class="k-panel wf-block"><h2>Запуски</h2><div class="wf-jobs" id="wfJobs"></div></section>`;
  wireDetail();
}

function wireDetail() {
  $('wfRun').addEventListener('click', run);
  $('wfRecheck').addEventListener('click', async () => {
    status('Проверяю граф на GPU…');
    try { state.current = { ...state.current, ...(await api(`/api/workflows/${state.current.id}/check`, { method: 'POST' })) }; renderDetail(); await loadJobs(); status('Проверено', 'good'); }
    catch (error) { status(error.message, 'bad'); }
  });
  $('wfDelete').addEventListener('click', async () => {
    if (!confirm(`Удалить воркфлоу «${state.current.name}»? Запуски и файлы останутся.`)) return;
    try { await api(`/api/workflows/${state.current.id}`, { method: 'DELETE' }); state.current = null; history.replaceState(null, '', location.pathname); await loadList(); status('Удалено'); }
    catch (error) { status(error.message, 'bad'); }
  });
  $('wfSaveForm').addEventListener('click', async () => {
    const params = state.current.params.map((p) => ({ key: p.key,
      exposed: document.querySelector(`[data-param="${CSS.escape(p.key)}"]`).checked,
      label: document.querySelector(`[data-label="${CSS.escape(p.key)}"]`).value.trim() || p.label }));
    try {
      await api(`/api/workflows/${state.current.id}`, { method: 'PUT', body: JSON.stringify({ expected_revision: state.current.revision, params }) });
      await loadList(state.current.id);
      status('Форма сохранена', 'good');
    } catch (error) { status(error.message, 'bad'); }
  });
  let editor = null;
  const reopen = async (saved) => { state.reopenGraph = true; await loadList(saved.id); };
  $('wfGraphBlock').addEventListener('toggle', async (event) => {
    if (!event.target.open || editor) return;
    try {
      const full = await api(`/api/workflows/${state.current.id}?graph=true`);
      editor = mountGraphEditor($('wfGraph'), { workflow: state.current, graph: full.graph, missing: state.current.check?.missing_nodes || [],
        api, status, onSaved: reopen });
    } catch (error) { status(error.message, 'bad'); }
  });
  if (state.reopenGraph) { state.reopenGraph = false; $('wfGraphBlock').open = true; }
  let versions = false;
  $('wfVersionsBlock').addEventListener('toggle', (event) => {
    if (!event.target.open || versions) return;
    versions = true;
    mountVersions($('wfVersions'), { workflow: state.current, api, status, onRestored: (saved) => loadList(saved.id) });
  });
  document.querySelectorAll('#wfForm select[data-key]').forEach((select) => select.addEventListener('change', () => preview(select)));
  document.querySelectorAll('#wfForm input[data-upload]').forEach((input) => input.addEventListener('change', () => upload(input)));
}

function preview(select) {
  const img = select.parentElement.querySelector('img');
  if (!img) return;
  img.hidden = !select.value;
  if (select.value) img.src = `/api/assets/${encodeURIComponent(select.value)}/thumbnail`;
}

async function upload(input) {
  const file = input.files?.[0];
  if (!file) return;
  const body = new FormData();
  body.append('file', file);
  status(`Загружаю ${file.name} в библиотеку…`);
  try {
    const asset = await api('/api/assets', { method: 'POST', body });
    state.assets.push(asset);
    const key = input.dataset.upload;
    renderDetail();
    await loadJobs();
    const select = document.querySelector(`#wfForm select[data-key="${CSS.escape(key)}"]`);
    select.value = asset.asset_id;
    preview(select);
    status(`${file.name} добавлен в библиотеку`, 'good');
  } catch (error) { status(error.message, 'bad'); }
}

function formValues() {
  const values = {};
  for (const param of state.current.params.filter((p) => p.exposed)) {
    const element = document.querySelector(`#wfForm [data-key="${CSS.escape(param.key)}"]`);
    const raw = element?.value ?? '';
    if (raw === '') continue;
    if (['seed', 'int'].includes(param.kind)) values[param.key] = Number.parseInt(raw, 10);
    else if (param.kind === 'float') values[param.key] = Number(raw);
    else if (param.kind === 'text' && raw === param.default) continue;
    else values[param.key] = raw;
  }
  return values;
}

async function run() {
  const button = $('wfRun');
  button.disabled = true;
  try {
    const result = await api(`/api/workflows/${state.current.id}/run`, { method: 'POST', body: JSON.stringify({ values: formValues(), count: Number($('wfCount').value) }) });
    status(`В очереди: ${result.jobs.length}. Задания идут в общей очереди GPU (пауза очереди их тоже держит).`, 'good');
    await loadJobs();
  } catch (error) { status(error.message, 'bad'); }
  finally { button.disabled = false; }
}

function output(item) {
  if (item.kind === 'image') return `<a href="${item.url}" target="_blank"><img src="${item.url}" alt="${esc(item.name)}"></a>`;
  if (item.kind === 'video') return `<video src="${item.url}" controls preload="metadata"></video>`;
  if (item.kind === 'audio') return `<audio src="${item.url}" controls></audio>`;
  return `<a href="${item.url}" download>${esc(item.name)}</a>`;
}

async function loadJobs() {
  if (!state.current) return;
  const data = await api(`/api/workflow-jobs?workflow_id=${encodeURIComponent(state.current.id)}&limit=20`);
  state.jobs = data.jobs;
  const box = $('wfJobs');
  if (box) box.innerHTML = state.jobs.length ? state.jobs.map((job) => `<div class="wf-job">
    <div class="wf-job-head"><span><span class="wf-state" data-state="${job.state}">${STATE_RU[job.state] || job.state}</span> · ${esc(job.created_at)} UTC · ревизия ${job.revision}</span>
    ${ACTIVE.has(job.state) ? `<button type="button" class="k-btn k-btn--ghost" data-cancel="${job.job_id}">Отменить</button>` : ''}</div>
    ${job.error ? `<div class="wf-error">${esc(job.error)}</div>` : ''}
    ${job.outputs.length ? `<div class="wf-outputs">${job.outputs.map(output).join('')}</div>` : ''}</div>`).join('')
    : '<p class="wf-hint">Ещё не запускался.</p>';
  box?.querySelectorAll('[data-cancel]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/workflow-jobs/${button.dataset.cancel}/cancel`, { method: 'POST' }); await loadJobs(); }
    catch (error) { status(error.message, 'bad'); }
  }));
  clearTimeout(state.timer);
  if (state.jobs.some((job) => ACTIVE.has(job.state))) state.timer = setTimeout(() => loadJobs().catch(() => {}), 4000);
}

async function importFile(file) {
  try {
    const graph = JSON.parse(await file.text());
    const created = await api('/api/workflows', { method: 'POST', body: JSON.stringify({ name: file.name.replace(/\.json$/i, '').slice(0, 120), graph }) });
    await loadList(created.id);
    status(`Импортирован «${created.name}»: найдено ${created.form.length} вход(ов) формы.`, 'good');
  } catch (error) { status(`Импорт не удался: ${error.message}`, 'bad'); }
}

async function showInbox() {
  const box = $('wfInboxList');
  box.hidden = false;
  box.innerHTML = '<p class="wf-hint">Читаю ComfyUI…</p>';
  try {
    const { items } = await api('/api/workflows/comfy-inbox');
    box.innerHTML = `<h2>Отправлено из ComfyUI</h2>${items.length ? items.map((item) => `<div class="wf-inbox-row">
      <span>${esc(item.name)} <small class="wf-hint">· ${item.nodes} узлов · ${new Date(item.saved_at * 1000).toLocaleString('ru')}</small></span>
      <span class="wf-actions">${item.studio_id && state.workflows.some((wf) => wf.id === item.studio_id)
        ? `<button type="button" class="k-btn k-btn--accent" data-import="${esc(item.id)}" data-target="${esc(item.studio_id)}">Обновить «${esc(state.workflows.find((wf) => wf.id === item.studio_id).name)}»</button>` : ''}
      <button type="button" class="k-btn ${item.studio_id ? 'k-btn--ghost' : 'k-btn--accent'}" data-import="${esc(item.id)}">Добавить новым</button></span></div>`).join('')
      : '<p class="wf-hint">Пусто. В ComfyUI открой граф и нажми «⇪ Воркфлоу в Studio».</p>'}`;
    box.querySelectorAll('[data-import]').forEach((button) => button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const target = button.dataset.target;
        const created = await api(`/api/workflows/comfy-inbox/${button.dataset.import}/import`, { method: 'POST',
          body: JSON.stringify(target ? { target } : {}) });
        box.hidden = true;
        await loadList(created.id);
        status(target ? `«${created.name}» обновлён из ComfyUI: ревизия ${created.revision}` : `Добавлен «${created.name}»`, 'good');
      } catch (error) { status(error.message, 'bad'); button.disabled = false; }
    }));
  } catch (error) { box.innerHTML = `<p class="wf-error">${esc(error.message)}</p>`; }
}

async function showFork() {
  const box = $('wfInboxList');
  box.hidden = false;
  box.innerHTML = '<p class="wf-hint">Читаю принятые рецепты…</p>';
  const recipes = await api('/api/recipes/accepted').catch(() => []);
  box.innerHTML = `<h2>Граф задания или рецепта → свой воркфлоу</h2>
    <p class="wf-hint">Точный граф ComfyUI, на котором шёл рендер, копируется сюда для правки. Само задание и принятый рецепт не меняются.
    Граф берётся с GPU-сервера или из архива Studio (Studio сохраняет граф каждого готового задания).</p>
    <div class="wf-run"><label class="wf-field"><span>Принятый рецепт</span><select class="k-field" id="wfRecipe">
      ${recipes.map((r) => `<option value="${esc(r.id)}">${esc(r.title)}</option>`).join('') || '<option value="">нет рецептов</option>'}</select></label>
      <button type="button" class="k-btn k-btn--accent" id="wfFromRecipe" ${recipes.length ? '' : 'disabled'}>Открыть граф рецепта</button></div>
    <div class="wf-run"><label class="wf-field"><span>ID задания Studio</span><input class="k-field" id="wfJobId" placeholder="uuid задания"
      value="${esc(new URLSearchParams(location.search).get('job') || '')}"></label>
      <button type="button" class="k-btn k-btn--accent" id="wfFromJob">Открыть граф задания</button></div>`;
  const fork = async (path) => {
    try {
      const created = await api(path, { method: 'POST', body: '{}' });
      box.hidden = true;
      state.reopenGraph = true;
      await loadList(created.id);
      status(`«${created.name}»: граф скопирован (${created.origin?.graph_source === 'archive' ? 'из архива Studio' : 'с GPU'})`, 'good');
    } catch (error) { status(error.message, 'bad'); }
  };
  $('wfFromRecipe').addEventListener('click', () => fork(`/api/workflows/from-recipe/${encodeURIComponent($('wfRecipe').value)}`));
  $('wfFromJob').addEventListener('click', () => {
    const id = $('wfJobId').value.trim();
    if (!id) { status('Вставь ID задания Studio (он есть в карточке задания).', 'bad'); $('wfJobId').focus(); return; }
    fork(`/api/workflows/from-job/${encodeURIComponent(id)}`);
  });
}

$('wfFork').addEventListener('click', () => showFork().catch((error) => status(error.message, 'bad')));
if (new URLSearchParams(location.search).get('job')) showFork().catch(() => {});
$('wfFile').addEventListener('change', (event) => { const file = event.target.files?.[0]; if (file) importFile(file); event.target.value = ''; });
$('wfInbox').addEventListener('click', showInbox);
window.studioBoot?.hold(api('/api/assets').then((assets) => { state.assets = assets; }).catch(() => {})
  .then(() => loadList()).catch((error) => status(error.message, 'bad')));
