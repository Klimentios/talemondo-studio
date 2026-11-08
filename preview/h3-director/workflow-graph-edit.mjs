// Graph editor of «Свои воркфлоу» (workflows.html): change values, links, nodes and LoRAs of a workflow's ComfyUI
// graph. Every change is an operation of app/graph_ops.py; the server replays the list on the saved revision
// against the live ComfyUI /object_info (POST /api/workflows/{id}/edit, dry_run) and returns the new graph, the diff
// and the problems, so nothing unchecked is drawn or saved. «Сохранить» writes the next revision; the previous one
// stays in «Версии». Field types, ranges and choices (installed LoRAs, samplers, schedulers…) come from the schema.
import { mountGraph } from './workflow-graph.mjs?v=20261108-graph-ops-1';

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isLink = (value) => Array.isArray(value) && value.length === 2 && typeof value[0] === 'string' && Number.isInteger(value[1]);
const OP_RU = { set: 'значение', link: 'связь', unset: 'вход убран', add_node: 'узел добавлен', remove_node: 'узел удалён',
  replace_node: 'узел заменён', add_lora: 'LoRA', title: 'название' };

export function mountGraphEditor(root, { workflow, graph, missing = [], api, status, onSaved }) {
  const schemas = new Map();
  const state = { ops: [], graph, changes: null, problems: [], selected: null, busy: false };
  root.innerHTML = `
    <div class="wge-bar">
      <button type="button" class="k-btn" data-act="add">＋ Узел</button>
      <button type="button" class="k-btn" data-act="lora">＋ LoRA</button>
      <span class="wge-count" aria-live="polite"></span>
      <button type="button" class="k-btn k-btn--ghost" data-act="undo" disabled>↶ Отменить</button>
      <button type="button" class="k-btn k-btn--ghost" data-act="reset" disabled>Сбросить</button>
      <input class="k-field wge-note" maxlength="300" placeholder="Что меняем (в историю версий)">
      <button type="button" class="k-btn k-btn--accent" data-act="save" disabled>Сохранить ревизию ${workflow.revision + 1}</button>
    </div>
    <div class="wge-panel" hidden></div>
    <div class="wge-changes" hidden></div>
    <div class="wfg-wrap wge-graph"></div>`;
  const $ = (selector) => root.querySelector(selector);
  const panel = $('.wge-panel');

  const loading = new Map(); // class → promise; `schemas` holds the resolved ones for synchronous lookups
  async function schema(cls) {
    if (!loading.has(cls)) {
      loading.set(cls, api(`/api/comfy/classes/${encodeURIComponent(cls)}`).catch((error) => ({ error: error.message }))
        .then((info) => { schemas.set(cls, info); return info; }));
    }
    return loading.get(cls);
  }

  function draw() {
    const marked = state.changes ? [...state.changes.added.map((c) => c.node), ...state.changes.changed.map((c) => c.node)] : [];
    mountGraph($('.wge-graph'), state.graph, { missing, inspector, selected: state.selected, highlight: marked });
    $('.wge-count').textContent = state.ops.length ? `Правок: ${state.ops.length}` : 'Без правок';
    for (const act of ['undo', 'reset']) root.querySelector(`[data-act="${act}"]`).disabled = !state.ops.length || state.busy;
    root.querySelector('[data-act="save"]').disabled = !state.ops.length || state.busy || state.problems.length > 0;
    const box = $('.wge-changes');
    box.hidden = !state.ops.length;
    if (!state.ops.length) return;
    const c = state.changes || { added: [], removed: [], changed: [] };
    const show = (v) => (typeof v === 'string' ? v : JSON.stringify(v)).slice(0, 60);
    box.innerHTML = `<ul>${[
      ...c.added.map((x) => `<li>＋ ${esc(x.class_type)} #${esc(x.node)}</li>`),
      ...c.removed.map((x) => `<li>− ${esc(x.class_type)} #${esc(x.node)}</li>`),
      ...c.changed.map((x) => `<li>${esc(x.class_type)} #${esc(x.node)} · ${esc(x.input)}: ${esc(show(x.before))} → ${esc(show(x.after))}</li>`),
    ].join('')}</ul>${(state.warnings || []).map((w) => `<p class="wf-hint">⚠ ${esc(w)}</p>`).join('')}${state.problems.map((p) => `<p class="wf-error">${esc(p)}</p>`).join('')}`;
  }

  // Replays all operations on the saved revision; a refused operation is dropped with the server's reason.
  async function push(op) {
    const before = new Set(Object.keys(state.graph));
    state.ops.push(op);
    state.busy = true;
    try {
      await preview();
      const added = Object.keys(state.graph).filter((id) => !before.has(id));
      if (added.length) state.selected = added[added.length - 1];
      status(`${OP_RU[op.op] || op.op}: проверено по ComfyUI`, 'good');
    } catch (error) {
      state.ops.pop();
      status(error.message, 'bad');
    } finally {
      state.busy = false;
      draw();
    }
  }

  async function preview() {
    if (!state.ops.length) {
      Object.assign(state, { graph, changes: null, problems: [] });
      return;
    }
    const result = await api(`/api/workflows/${encodeURIComponent(workflow.id)}/edit`, { method: 'POST',
      body: JSON.stringify({ expected_revision: workflow.revision, ops: state.ops, dry_run: true }) });
    Object.assign(state, { graph: result.graph, changes: result.changes, problems: result.new_problems, warnings: result.warnings || [] });
  }

  const sources = (self) => Object.entries(state.graph).filter(([id]) => id !== self).map(([id, node]) => ({ id, node }));

  async function inspector(id, node, el) {
    state.selected = id;
    el.innerHTML = '<p class="wf-hint">Читаю схему узла…</p>';
    const info = await schema(node.class_type);
    // outputs of every node, to offer only type-compatible links
    await Promise.all(Object.values(state.graph).map((n) => schema(n.class_type)));
    if (state.selected !== id) return;
    const head = `<h3>${esc(node._meta?.title || node.class_type)}</h3>
      <p class="wf-hint"><code>${esc(node.class_type)}</code> · узел #${esc(id)}${info.pack ? ` · ${esc(info.pack)}` : ''}</p>
      <div class="wf-actions"><button type="button" class="k-btn k-btn--ghost" data-node-act="replace">Заменить…</button>
      <button type="button" class="k-btn k-btn--danger" data-node-act="remove">Удалить</button></div>`;
    if (info.error) {
      el.innerHTML = `${head}<p class="wfg-bad">${esc(info.error)}</p>`;
      wireNodeActions(el, id, node);
      return;
    }
    const fields = info.inputs.map((input) => fieldFor(id, node, input)).join('');
    const unknown = Object.keys(node.inputs || {}).filter((name) => !info.inputs.some((i) => i.name === name) && !name.includes('.'));
    el.innerHTML = `${head}${fields}${unknown.length ? `<p class="wfg-bad">Входов нет в схеме: ${unknown.map(esc).join(', ')}</p>` : ''}
      <p class="wf-hint">Выходы: ${info.outputs.map((o, i) => `${i} ${esc(o.name)}`).join(', ') || 'нет'}</p>`;
    wireNodeActions(el, id, node);
    el.querySelectorAll('[data-input]').forEach((input) => input.addEventListener('change', () => onField(id, input)));
  }

  function fieldFor(id, node, input) {
    const value = node.inputs?.[input.name];
    const name = esc(input.name);
    const label = `<span>${name}${input.required ? '' : ' · необяз.'}${input.tooltip ? ` <small title="${esc(input.tooltip)}">ⓘ</small>` : ''}</span>`;
    if (!input.widget || isLink(value)) {
      const options = [];
      for (const { id: src, node: other } of sources(id)) {
        const outs = schemas.get(other.class_type)?.outputs || [];
        outs.forEach((out, slot) => {
          if (input.widget || out.type === input.type || out.type === '*' || input.type === '*') {
            const val = `${src}:${slot}`;
            const on = isLink(value) && value[0] === src && value[1] === slot;
            options.push(`<option value="${esc(val)}" ${on ? 'selected' : ''}>#${esc(src)} ${esc(other._meta?.title || other.class_type)} · ${esc(out.name)}</option>`);
          }
        });
      }
      const empty = input.required ? '<option value="" disabled>— выбери источник —</option>' : '<option value="">— не подключено —</option>';
      return `<label class="wf-field">${label}<select class="k-field" data-input="${name}" data-kind="link" ${input.required ? 'required' : ''}>
        ${value === undefined || !isLink(value) ? empty.replace('value=""', 'value="" selected') : empty}${options.join('')}</select>
        <small class="wf-hint">${esc(input.type)}</small></label>`;
    }
    if (input.type === 'COMBO') {
      const choices = input.choices || [];
      const extra = value !== undefined && !choices.includes(value) ? `<option selected>${esc(value)} (нет на сервере)</option>` : '';
      return `<label class="wf-field">${label}<select class="k-field" data-input="${name}" data-kind="combo">${extra}
        ${choices.map((c) => `<option ${c === value ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>`;
    }
    if (input.type === 'BOOLEAN') return `<label class="wf-field">${label}<input type="checkbox" data-input="${name}" data-kind="bool" ${value ? 'checked' : ''}></label>`;
    if (input.type === 'INT' || input.type === 'FLOAT') {
      const range = ['min', 'max'].filter((k) => input[k] !== undefined && Math.abs(input[k]) < 1e15).map((k) => `${k}="${input[k]}"`).join(' ');
      return `<label class="wf-field">${label}<input class="k-field" type="number" data-input="${name}" data-kind="${input.type}"
        step="${input.type === 'INT' ? (input.step || 1) : 'any'}" ${range} value="${esc(value ?? input.default ?? '')}"></label>`;
    }
    if (input.multiline || (typeof value === 'string' && value.length > 50)) {
      return `<label class="wf-field">${label}<textarea class="k-field" data-input="${name}" data-kind="text">${esc(value ?? '')}</textarea></label>`;
    }
    return `<label class="wf-field">${label}<input class="k-field" data-input="${name}" data-kind="text" value="${esc(value ?? '')}"></label>`;
  }

  function onField(id, input) {
    const name = input.dataset.input;
    const kind = input.dataset.kind;
    if (kind === 'link') {
      if (!input.value) return push({ op: 'unset', node: id, input: name });
      const [src, slot] = input.value.split(':');
      return push({ op: 'link', node: id, input: name, from: [src, Number(slot)] });
    }
    let value = input.value;
    if (kind === 'bool') value = input.checked;
    else if (kind === 'INT') value = Number.parseInt(value, 10);
    else if (kind === 'FLOAT') value = Number(value);
    if (typeof value === 'number' && !Number.isFinite(value)) return status('Нужно число', 'bad');
    return push({ op: 'set', node: id, input: name, value });
  }

  function wireNodeActions(el, id, node) {
    el.querySelector('[data-node-act="remove"]').addEventListener('click', () => {
      if (!confirm(`Удалить ${node.class_type} #${id}? Связи через него будут проведены мимо, если это возможно.`)) return;
      state.selected = null;
      push({ op: 'remove_node', node: id, bypass: true });
    });
    el.querySelector('[data-node-act="replace"]').addEventListener('click', () => openSearch({ replace: id }));
  }

  function openSearch({ replace = null } = {}) {
    panel.hidden = false;
    panel.innerHTML = `<div class="wge-search"><input class="k-field" type="search" placeholder="${replace ? `Чем заменить #${esc(replace)}` : 'Найти узел ComfyUI'}: имя, категория, пакет">
      <button type="button" class="k-btn k-btn--ghost" data-close>Закрыть</button></div><div class="wge-results"></div>`;
    const input = panel.querySelector('input');
    const results = panel.querySelector('.wge-results');
    let timer = null;
    const search = async () => {
      try {
        const data = await api(`/api/comfy/classes?q=${encodeURIComponent(input.value)}&limit=40`);
        results.innerHTML = data.classes.map((c) => `<button type="button" class="wge-result" data-cls="${esc(c.class_type)}">
          <b>${esc(c.display_name)}</b> <code>${esc(c.class_type)}</code><small>${esc(c.category)} · → ${esc(c.outputs.join(', ') || '—')}</small></button>`).join('')
          || '<p class="wf-hint">Ничего не найдено. Узлы ставятся в «Узлы и модели».</p>';
        results.querySelectorAll('[data-cls]').forEach((button) => button.addEventListener('click', () => {
          panel.hidden = true;
          push(replace ? { op: 'replace_node', node: replace, class_type: button.dataset.cls } : { op: 'add_node', class_type: button.dataset.cls });
        }));
      } catch (error) { results.innerHTML = `<p class="wf-error">${esc(error.message)}</p>`; }
    };
    input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 250); });
    panel.querySelector('[data-close]').addEventListener('click', () => { panel.hidden = true; });
    input.focus();
    search();
  }

  async function openLora() {
    panel.hidden = false;
    panel.innerHTML = '<p class="wf-hint">Читаю LoRA на сервере…</p>';
    const info = await schema('LoraLoaderModelOnly');
    if (info.error) { panel.innerHTML = `<p class="wf-error">${esc(info.error)}</p>`; return; }
    await Promise.all(Object.values(state.graph).map((n) => schema(n.class_type)));
    const choices = info.inputs.find((i) => i.name === 'lora_name')?.choices || [];
    const models = Object.entries(state.graph).filter(([, n]) => (schemas.get(n.class_type)?.outputs || []).some((o) => o.type === 'MODEL'));
    panel.innerHTML = `<div class="wge-lora">
      <label class="wf-field"><span>LoRA (установлены на GPU)</span><select class="k-field" data-lora>${choices.map((c) => `<option>${esc(c)}</option>`).join('')}</select></label>
      <label class="wf-field"><span>Сила</span><input class="k-field" type="number" step="0.05" value="1" data-strength></label>
      <label class="wf-field"><span>После узла (MODEL)</span><select class="k-field" data-after><option value="">загрузчик модели (авто)</option>
        ${models.map(([id, n]) => `<option value="${esc(id)}">#${esc(id)} ${esc(n._meta?.title || n.class_type)}</option>`).join('')}</select></label>
      <div class="wf-actions"><button type="button" class="k-btn k-btn--accent" data-add>Добавить</button><button type="button" class="k-btn k-btn--ghost" data-close>Закрыть</button></div>
      ${choices.length ? '' : '<p class="wf-hint">На сервере нет LoRA: добавь файл в «Узлы и модели».</p>'}</div>`;
    panel.querySelector('[data-close]').addEventListener('click', () => { panel.hidden = true; });
    panel.querySelector('[data-add]').addEventListener('click', () => {
      const op = { op: 'add_lora', lora_name: panel.querySelector('[data-lora]').value, strength_model: Number(panel.querySelector('[data-strength]').value) };
      const after = panel.querySelector('[data-after]').value;
      if (after) op.after = after;
      panel.hidden = true;
      push(op);
    });
  }

  root.querySelector('[data-act="add"]').addEventListener('click', () => openSearch());
  root.querySelector('[data-act="lora"]').addEventListener('click', openLora);
  root.querySelector('[data-act="undo"]').addEventListener('click', async () => {
    state.ops.pop();
    try { await preview(); } catch (error) { status(error.message, 'bad'); }
    draw();
  });
  root.querySelector('[data-act="reset"]').addEventListener('click', () => {
    state.ops = [];
    Object.assign(state, { graph, changes: null, problems: [] });
    draw();
  });
  root.querySelector('[data-act="save"]').addEventListener('click', async () => {
    state.busy = true;
    draw();
    try {
      const saved = await api(`/api/workflows/${encodeURIComponent(workflow.id)}/edit`, { method: 'POST',
        body: JSON.stringify({ expected_revision: workflow.revision, ops: state.ops, note: $('.wge-note').value.trim() }) });
      status(`Сохранена ревизия ${saved.workflow.revision}: ${saved.workflow.note}`, 'good');
      await onSaved(saved.workflow);
    } catch (error) {
      state.busy = false;
      status(error.message, 'bad');
      draw();
    }
  });
  draw();
  return { pending: () => state.ops.length };
}
