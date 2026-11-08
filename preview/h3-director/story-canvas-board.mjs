// Board tools of the story canvas, the way a node canvas (Higgsfield Canvas) is worked by hand:
// - drag from a material's port onto a shot = give it to that shot; click a material link = take it away;
// - right-click menu on a shot, a material, a link or the empty board; double-click the board = new shot there;
// - keys: N new shot, ⌘D duplicate, ⌫ delete, ←/→ previous/next shot, F fit, C compare takes, ? help;
// - minimap of the whole scene, drag it to move the view;
// - takes of one shot side by side, played together, one click picks the take;
// - notes on a shot (M or the 💬 chip): for people only, they never reach a prompt or H3;
// - scene templates (story-canvas-templates.mjs).
// Every change goes through the canvas's own actions (api.act → story-canvas.mjs onClick), so the render,
// the draft and the material rules stay in one place; this module only adds ways to reach them.
import { shotMaterials, takeList, canvasMaterials } from './story-canvas-model.mjs?v=20261101-keyframe-1';
import { templatesPopover, saveTemplate } from './story-canvas-templates.mjs?v=20261101-templates-1';
import { installWorlds } from './story-canvas-worlds.mjs?v=20261103-codex-world-1';
import { installBlockout } from './story-canvas-blockout.mjs?v=20261101-staging-1';

const esc = (value = '') => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const MINI_W = 200;
const MINI_H = 120;

export const SHORTCUTS = [
  ['N', 'Новый шот'], ['⌘D', 'Дублировать шот'], ['⌫', 'Удалить шот'], ['← →', 'Соседний шот'],
  ['C', 'Сравнить дубли'], ['M', 'Заметка к шоту'], ['F', 'Показать всю сцену'], ['Esc', 'Закрыть'], ['?', 'Эта подсказка'],
];

/** A copy of a shot for «Дублировать»: its text, length, direction and material switches; never its takes or frames. */
export function duplicateFields(cut) {
  return {
    prompt: cut.prompt || '', duration: cut.duration, director: { ...(cut.director || {}) },
    directorProfileId: cut.directorProfileId || '', offRefs: [...(cut.offRefs || [])],
  };
}

/** Notes on a shot: newest last; text trimmed, empty refused. */
export function addNote(cut, text, now = Date.now()) {
  const body = String(text || '').trim();
  if (!body) return cut;
  return { ...cut, notes: [...(cut.notes || []), { id: `n-${now.toString(36)}`, text: body, at: new Date(now).toISOString() }] };
}
export function removeNote(cut, id) {
  return { ...cut, notes: (cut.notes || []).filter((note) => note.id !== id) };
}

/** Moves the cut with id `moved` right after the cut with id `after`; other order unchanged. */
export function placeAfter(list, moved, after) {
  const item = list.find((cut) => cut.id === moved);
  if (!item || moved === after) return list;
  const rest = list.filter((cut) => cut.id !== moved);
  const at = rest.findIndex((cut) => cut.id === after);
  if (at < 0) return list;
  rest.splice(at + 1, 0, item);
  return rest;
}

/** Minimap geometry: nodes and the visible window scaled into the minimap box. */
export function minimap(nodes, view, viewport, size = { w: MINI_W, h: MINI_H }) {
  const seen = { x: -view.x / view.zoom, y: -view.y / view.zoom, w: viewport.w / view.zoom, h: viewport.h / view.zoom };
  const all = [...nodes, seen];
  const x0 = Math.min(...all.map((r) => r.x)); const y0 = Math.min(...all.map((r) => r.y));
  const x1 = Math.max(...all.map((r) => r.x + r.w)); const y1 = Math.max(...all.map((r) => r.y + r.h));
  const scale = Math.min(size.w / Math.max(1, x1 - x0), size.h / Math.max(1, y1 - y0));
  const ox = (size.w - (x1 - x0) * scale) / 2; const oy = (size.h - (y1 - y0) * scale) / 2;
  const map = (r) => ({ ...r, x: ox + (r.x - x0) * scale, y: oy + (r.y - y0) * scale, w: r.w * scale, h: r.h * scale });
  return { nodes: nodes.map(map), seen: map(seen), scale, toWorld: (mx, my) => ({ x: x0 + (mx - ox) / scale, y: y0 + (my - oy) / scale }) };
}

/** Why a material connected by hand still does not reach the shot (the render rules win), or ''. */
export function connectRefusal(gets, ref) {
  if (gets.ids.has(ref.id)) return '';
  if (gets.error) return gets.error;
  if (['Лицо героя', 'Персонаж целиком', 'Костюм', 'Голос'].includes(ref.role)) {
    return `«${ref.owner || ref.label || ref.role}» не назван в тексте шота — впиши его имя, тогда материал уйдёт в H3.`;
  }
  return 'Эта локация из другого места паспорта — в шот этого места она не уходит.';
}

export function installBoard(api) {
  const { shell, viewport, world } = api;
  const menu = document.createElement('div');
  menu.className = 'kc-menu'; menu.hidden = true; menu.setAttribute('role', 'menu');
  const mini = document.createElement('div');
  mini.className = 'kc-float kc-minimap';
  mini.innerHTML = `<svg width="${MINI_W}" height="${MINI_H}" aria-label="Мини-карта сцены"></svg>`;
  const help = document.createElement('div');
  help.className = 'kc-help'; help.hidden = true;
  help.innerHTML = `<h4>Клавиши холста</h4><dl>${SHORTCUTS.map(([k, t]) => `<dt><kbd>${k}</kbd></dt><dd>${t}</dd>`).join('')}</dl>
    <p>Тяни от точки материала к шоту — материал уйдёт в этот шот. Клик по линии — убрать. Правый клик — меню, двойной клик по пустому месту — новый шот.</p>`;
  const compare = document.createElement('div');
  compare.className = 'kc-compare'; compare.hidden = true;
  const notes = document.createElement('div');
  notes.className = 'kc-notes'; notes.hidden = true;
  const templates = document.createElement('div');
  templates.className = 'kc-templates'; templates.hidden = true;
  shell.append(menu, mini, help, compare, notes, templates);
  shell.querySelector('.kc-top-left [data-kc-act="scene-settings"]')?.insertAdjacentHTML('afterend',
    '<button type="button" data-kc-board="templates" title="Сохранить сцену как шаблон или начать новую из шаблона">⧉ Шаблоны</button>');
  const worlds = installWorlds(api);
  installBlockout(api);
  const zoomBar = shell.querySelector('.kc-zoom');
  zoomBar?.insertAdjacentHTML('beforeend', '<button type="button" data-kc-board="help" title="Клавиши и приёмы холста" aria-label="Клавиши холста">?</button>');

  const cutOf = (id) => api.scene().cuts.find((cut) => cut.id === id);
  const shotNumber = (id) => api.scene().cuts.findIndex((cut) => cut.id === id) + 1;
  const refOf = (id) => api.scene().references.find((ref) => ref.id === id);
  const selectedCut = () => (api.selected()?.startsWith('s:') ? api.selected().slice(2) : null);
  const toWorld = (clientX, clientY) => {
    const rect = viewport.getBoundingClientRect(); const v = api.getView();
    return { x: (clientX - rect.left - v.x) / v.zoom, y: (clientY - rect.top - v.y) / v.zoom };
  };

  // --- shot operations ---------------------------------------------------------------------

  function addShotAt(point = null, after = null) {
    api.act('add-shot');
    const id = selectedCut();
    if (!id) return;
    if (after) api.studio().editCuts((list) => placeAfter(list, id, after));
    if (point) api.pin(`s:${id}`, { x: Math.round(point.x), y: Math.round(point.y) });
    api.sync();
  }
  function duplicate(id) {
    const source = cutOf(id);
    if (!source) return;
    api.act('add-shot');
    const copy = selectedCut();
    if (!copy || copy === id) return;
    api.studio().editCuts((list) => placeAfter(list.map((cut) => (cut.id === copy ? { ...cut, ...duplicateFields(source), startFrame: null } : cut)), copy, id));
    void api.studio().saveDraft();
    api.sync(); api.select(`s:${copy}`);
    api.toast(`Шот ${shotNumber(id)} скопирован — дубли и кадры не копируются`);
  }
  function step(delta) {
    const { cuts } = api.scene();
    const at = cuts.findIndex((cut) => cut.id === selectedCut());
    const next = cuts[Math.max(0, Math.min(cuts.length - 1, at < 0 ? 0 : at + delta))];
    if (next) { api.select(`s:${next.id}`); api.reveal(`s:${next.id}`); }
  }

  // --- menu --------------------------------------------------------------------------------

  let menuItems = [];
  function openMenu(x, y, items) {
    menuItems = items.filter(Boolean);
    menu.innerHTML = menuItems.map((item, index) => (item === '-' ? '<hr>'
      : `<button type="button" role="menuitem" data-index="${index}" ${item.disabled ? 'disabled' : ''} class="${item.danger ? 'is-danger' : ''}">${esc(item.label)}${item.key ? `<kbd>${esc(item.key)}</kbd>` : ''}</button>`)).join('');
    menu.hidden = false;
    const box = shell.getBoundingClientRect();
    menu.style.left = `${Math.min(x - box.left, box.width - menu.offsetWidth - 8)}px`;
    menu.style.top = `${Math.min(y - box.top, box.height - menu.offsetHeight - 8)}px`;
    menu.querySelector('button:not(:disabled)')?.focus();
  }
  const closeMenu = () => { menu.hidden = true; };
  menu.addEventListener('click', (event) => {
    const item = menuItems[Number(event.target.closest('[data-index]')?.dataset.index)];
    if (!item || item.disabled) return;
    closeMenu(); item.run();
  });

  function shotMenu(id) {
    const { cuts } = api.scene();
    const index = cuts.findIndex((cut) => cut.id === id);
    const cut = cuts[index];
    const done = takeList(cut, api.jobs()).filter((take) => take.status === 'done').length;
    return [
      { label: 'Открыть шот', run: () => api.select(`s:${id}`) },
      { label: 'Дублировать', key: '⌘D', run: () => duplicate(id) },
      { label: 'Новый шот после этого', run: () => addShotAt(null, id) },
      { label: `Сравнить дубли (${done})`, key: 'C', disabled: done < 2, run: () => openCompare(id) },
      { label: `Заметки (${(cut.notes || []).length})`, key: 'M', run: () => openNotes(id) },
      { label: worlds.isFantasy(cut) ? 'Шот в фантазии ✓ — сделать реальным' : 'Шот в фантазии', run: () => worlds.toggleFantasy(id) },
      '-',
      index > 0 && { label: cut.startFrame?.source === 'previous' ? 'Сделать новым планом (склейка)' : 'Продолжить с конца предыдущего', run: () => api.act('toggle-link', { cut: id }) },
      { label: 'Сдвинуть влево', disabled: index === 0, run: () => api.act('move-left', { cut: id }) },
      { label: 'Сдвинуть вправо', disabled: index === cuts.length - 1, run: () => api.act('move-right', { cut: id }) },
      '-',
      { label: 'Удалить шот', key: '⌫', danger: true, disabled: cuts.length < 2, run: () => api.act('remove', { cut: id }) },
    ];
  }
  function materialMenu(id) {
    const ref = refOf(id);
    return [
      { label: 'Открыть материал', run: () => api.select(`m:${id}`) },
      { label: 'Дать всем шотам', run: () => setForAll(id, true) },
      { label: 'Убрать из всех шотов', run: () => setForAll(id, false) },
      '-',
      { label: `Убрать «${ref?.role || 'материал'}» из сцены`, danger: true, run: () => api.act('ref-remove', { ref: id }) },
    ];
  }
  function setForAll(refId, on) {
    api.studio().editCuts((list) => list.map((cut) => {
      const off = new Set(cut.offRefs || []);
      if (on) off.delete(refId); else off.add(refId);
      return { ...cut, offRefs: [...off] };
    }));
    void api.studio().saveDraft(); api.sync();
  }
  function boardMenu(point) {
    return [
      { label: 'Новый шот здесь', run: () => addShotAt(point) },
      { label: 'Новый шот в конец', key: 'N', run: () => addShotAt() },
      { label: 'Добавить материал', run: () => api.act('add-material') },
      { label: 'Сохранить сцену как шаблон', run: () => saveTemplate(api) },
      { label: 'Новая сцена из шаблона…', run: () => toggleTemplates(true) },
      '-',
      { label: 'Показать всю сцену', key: 'F', run: () => api.act('fit') },
      { label: 'Разложить шоты в ленту', run: () => api.act('relayout') },
      { label: 'Клавиши холста', key: '?', run: () => toggleHelp(true) },
    ];
  }

  // Links are drawn under the nodes; a click near a material link finds it by its stroke.
  function linkAt(clientX, clientY) {
    for (const path of shell.querySelectorAll('.kc-edge-ref[data-ref]')) {
      const p = toWorld(clientX, clientY);
      const svgPoint = Object.assign(api.edges().createSVGPoint(), p);
      const width = path.style.strokeWidth; path.style.strokeWidth = String(14 / api.getView().zoom);
      const hit = path.isPointInStroke(svgPoint);
      path.style.strokeWidth = width;
      if (hit) return path;
    }
    return null;
  }
  function linkMenu(path) {
    const ref = refOf(path.dataset.ref);
    const n = shotNumber(path.dataset.cut);
    return [{ label: `Не давать «${ref?.role || 'материал'}» шоту ${n}`, danger: true, run: () => api.act('ref-shot-off', { cut: path.dataset.cut, ref: path.dataset.ref }) },
      { label: `Открыть шот ${n}`, run: () => api.select(`s:${path.dataset.cut}`) }];
  }

  viewport.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    const node = event.target.closest('.kc-node');
    const key = node?.dataset.key || '';
    const link = !node && linkAt(event.clientX, event.clientY);
    const items = key.startsWith('s:') ? shotMenu(key.slice(2))
      : key.startsWith('m:') && key !== 'm:+' ? materialMenu(key.slice(2))
        : link ? linkMenu(link) : !node ? boardMenu(toWorld(event.clientX, event.clientY)) : null;
    if (items) openMenu(event.clientX, event.clientY, items);
  });
  viewport.addEventListener('dblclick', (event) => {
    if (event.target.closest('.kc-node, button')) return;
    addShotAt(toWorld(event.clientX, event.clientY));
  });
  // A plain click on a link (no drag) opens its menu; the canvas's own pan handler ignores clicks that did not move.
  viewport.addEventListener('click', (event) => {
    if (event.target.closest('.kc-node, button')) return;
    const link = linkAt(event.clientX, event.clientY);
    if (link) { event.stopPropagation(); openMenu(event.clientX, event.clientY, linkMenu(link)); }
  });
  document.addEventListener('pointerdown', (event) => { if (!menu.hidden && !menu.contains(event.target)) closeMenu(); }, true);

  // --- connect by dragging from a material port -------------------------------------------

  viewport.addEventListener('pointerdown', (event) => {
    const port = event.target.closest('.kc-material .kc-out');
    if (!port || event.button !== 0) return;
    event.stopPropagation(); event.preventDefault();
    const refId = port.closest('.kc-node').dataset.key.slice(2);
    const rect = port.getBoundingClientRect();
    const from = toWorld(rect.left + rect.width / 2, rect.top + rect.height / 2);
    const wire = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    wire.setAttribute('class', 'kc-edge-drag');
    api.edges().append(wire);
    let target = null;
    const move = (e) => {
      const to = toWorld(e.clientX, e.clientY);
      const dx = Math.max(60, Math.abs(to.x - from.x) / 2);
      wire.setAttribute('d', `M${from.x},${from.y} C${from.x + dx},${from.y} ${to.x - dx},${to.y} ${to.x},${to.y}`);
      const over = document.elementFromPoint(e.clientX, e.clientY)?.closest('.kc-shot');
      if (target !== over) { target?.classList.remove('is-drop'); target = over; target?.classList.add('is-drop'); }
    };
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      wire.remove(); target?.classList.remove('is-drop');
      if (!target) return;
      const cutId = target.dataset.cut;
      api.act('ref-shot-on', { cut: cutId, ref: refId });
      const { references, places } = api.scene();
      const ref = refOf(refId);
      const why = ref && connectRefusal(shotMaterials(cutOf(cutId), canvasMaterials(references), places), ref);
      api.toast(why || `«${ref?.role || 'Материал'}» уходит в шот ${shotNumber(cutId)}`);
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  }, true);

  // --- keys --------------------------------------------------------------------------------

  window.addEventListener('keydown', (event) => {
    if (event.target.closest?.('textarea, input, select, [contenteditable], dialog') || shell.closest('[hidden]')) return;
    if (!compare.hidden) { if (event.key === 'Escape') closeCompare(); return; }
    const id = selectedCut();
    const cmd = event.metaKey || event.ctrlKey;
    const key = event.key.toLowerCase();
    let handled = true;
    if (event.key === 'Escape') { closeMenu(); toggleHelp(false); notes.hidden = true; templates.hidden = true; handled = false; }
    else if (cmd && key === 'd' && id) duplicate(id);
    else if (cmd || event.altKey) handled = false;
    else if (key === 'n' || key === 'т') addShotAt();
    else if ((event.key === 'Delete' || event.key === 'Backspace') && id) api.act('remove', { cut: id });
    else if (event.key === 'ArrowRight') step(1);
    else if (event.key === 'ArrowLeft') step(-1);
    else if (key === 'f' || key === 'а') api.act('fit');
    else if ((key === 'c' || key === 'с') && id) openCompare(id);
    else if ((key === 'm' || key === 'ь') && id) openNotes(id);
    else if (event.key === '?' || event.key === ',') toggleHelp();
    else handled = false;
    if (handled) event.preventDefault();
  });

  function toggleHelp(force) { help.hidden = force === undefined ? !help.hidden : !force; }
  function toggleTemplates(force) {
    const open = force === undefined ? templates.hidden : force;
    if (open) templatesPopover(api, templates); else templates.hidden = true;
  }
  shell.addEventListener('click', (event) => {
    const board = event.target.closest('[data-kc-board]');
    const chip = event.target.closest('.kc-notes-chip');
    if (board) { event.stopPropagation(); if (board.dataset.kcBoard === 'help') toggleHelp(); else toggleTemplates(); }
    else if (chip) { event.stopPropagation(); openNotes(chip.closest('.kc-shot').dataset.cut); }
  });

  // --- notes on a shot ---------------------------------------------------------------------

  function openNotes(id) {
    const cut = cutOf(id);
    if (!cut) return;
    const list = cut.notes || [];
    notes.innerHTML = `<div class="kc-notes-head"><b>Заметки · шот ${shotNumber(id)}</b><button type="button" data-note="close" aria-label="Закрыть">×</button></div>
      <p class="kc-notes-hint">Только для вас: в промпт и в H3 не уходят.</p>
      <ul>${list.map((note) => `<li><p>${esc(note.text)}</p><small>${esc(new Date(note.at).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }))}</small>
        <button type="button" data-note="delete" data-id="${esc(note.id)}" aria-label="Удалить заметку">×</button></li>`).join('') || '<li class="is-empty">Пока пусто</li>'}</ul>
      <textarea rows="3" placeholder="Что поправить, что понравилось, идея для дубля…  ⌘Enter — добавить"></textarea>
      <button type="button" class="kc-accent" data-note="add">Добавить заметку</button>`;
    notes.dataset.cut = id;
    notes.hidden = false;
    notes.querySelector('textarea').focus();
  }
  function changeNotes(change) {
    const id = notes.dataset.cut;
    api.studio().editCuts((list) => list.map((cut) => (cut.id === id ? change(cut) : cut)));
    void api.studio().saveDraft(); api.sync(); openNotes(id);
  }
  notes.addEventListener('click', (event) => {
    const button = event.target.closest('[data-note]');
    if (!button) return;
    event.stopPropagation();
    if (button.dataset.note === 'close') notes.hidden = true;
    else if (button.dataset.note === 'add') changeNotes((cut) => addNote(cut, notes.querySelector('textarea').value));
    else if (button.dataset.note === 'delete') changeNotes((cut) => removeNote(cut, button.dataset.id));
  });
  notes.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); changeNotes((cut) => addNote(cut, notes.querySelector('textarea').value)); }
    if (event.key === 'Escape') notes.hidden = true;
  });

  // --- minimap -----------------------------------------------------------------------------

  let frame = 0;
  const redraw = () => { if (!frame) frame = requestAnimationFrame(drawMini); };
  function drawMini() {
    frame = 0;
    const nodes = [...world.querySelectorAll('.kc-node')].map((node) => {
      const p = api.positions()[node.dataset.key] || { x: 0, y: 0 };
      return { x: p.x, y: p.y, w: node.offsetWidth, h: node.offsetHeight, key: node.dataset.key, selected: node.classList.contains('is-selected'), status: node.dataset.status || '' };
    });
    mini.hidden = nodes.length < 4;
    if (mini.hidden) return;
    const geo = minimap(nodes, api.getView(), { w: viewport.clientWidth, h: viewport.clientHeight });
    mini.toWorld = geo.toWorld;
    const kind = (key) => (key.startsWith('m:') ? 'm' : key === 'film' ? 'f' : 's');
    mini.firstElementChild.innerHTML = geo.nodes.map((r) => `<rect class="is-${kind(r.key)}${r.selected ? ' is-selected' : ''}${r.status ? ` st-${r.status}` : ''}" x="${r.x.toFixed(1)}" y="${r.y.toFixed(1)}" width="${Math.max(2, r.w).toFixed(1)}" height="${Math.max(2, r.h).toFixed(1)}" rx="2"/>`).join('')
      + `<rect class="kc-mini-seen" x="${geo.seen.x.toFixed(1)}" y="${geo.seen.y.toFixed(1)}" width="${geo.seen.w.toFixed(1)}" height="${geo.seen.h.toFixed(1)}" rx="3"/>`;
  }
  new MutationObserver(redraw).observe(world, { attributes: true, attributeFilter: ['style', 'class'], subtree: true, childList: true });
  window.addEventListener('resize', redraw);
  mini.addEventListener('pointerdown', (event) => {
    event.stopPropagation();
    const svg = mini.firstElementChild;
    mini.setPointerCapture(event.pointerId);
    const go = (e) => {
      const rect = svg.getBoundingClientRect();
      const p = mini.toWorld?.(e.clientX - rect.left, e.clientY - rect.top);
      if (!p) return;
      const v = api.getView();
      api.setView({ ...v, x: viewport.clientWidth / 2 - p.x * v.zoom, y: viewport.clientHeight / 2 - p.y * v.zoom });
    };
    go(event);
    const up = () => { mini.removeEventListener('pointermove', go); mini.removeEventListener('pointerup', up); };
    mini.addEventListener('pointermove', go); mini.addEventListener('pointerup', up);
  });
  redraw();

  // --- compare takes -----------------------------------------------------------------------

  function openCompare(id) {
    const cut = cutOf(id);
    const takes = cut ? takeList(cut, api.jobs()).map((take, index) => ({ ...take, n: index + 1 })).filter((take) => take.status === 'done') : [];
    if (takes.length < 2) { api.toast('Сравнивать нечего: у шота меньше двух готовых дублей'); return; }
    const current = cut.selectedTakeId || cut.shotJobId;
    compare.innerHTML = `<div class="kc-compare-head"><b>Шот ${shotNumber(id)} · дубли рядом</b>
      <button type="button" data-cmp="play">▶ Играть все с начала</button><button type="button" data-cmp="close" aria-label="Закрыть">×</button></div>
      <div class="kc-compare-grid" style="--n:${Math.min(takes.length, 4)}">${takes.map((take) => `<figure class="${take.jobId === current ? 'is-current' : ''}">
        <video src="/api/jobs/${encodeURIComponent(take.jobId)}/video" poster="/api/jobs/${encodeURIComponent(take.jobId)}/poster" muted playsinline loop preload="metadata" controls></video>
        <figcaption>Дубль ${take.n}${take.jobId === current ? ' · выбран' : ''}<button type="button" data-cmp="pick" data-job="${esc(take.jobId)}" ${take.jobId === current ? 'disabled' : ''}>Взять этот</button></figcaption></figure>`).join('')}</div>`;
    compare.dataset.cut = id;
    compare.hidden = false;
  }
  function closeCompare() { compare.querySelectorAll('video').forEach((v) => v.pause()); compare.hidden = true; compare.replaceChildren(); }
  compare.addEventListener('click', (event) => {
    const button = event.target.closest('[data-cmp]');
    if (event.target === compare) { closeCompare(); return; }
    if (!button) return;
    event.stopPropagation();
    if (button.dataset.cmp === 'close') closeCompare();
    else if (button.dataset.cmp === 'play') compare.querySelectorAll('video').forEach((v) => { v.currentTime = 0; void v.play().catch(() => {}); });
    else if (button.dataset.cmp === 'pick') {
      api.act('take', { cut: compare.dataset.cut, job: button.dataset.job });
      api.toast('Дубль выбран — он пойдёт в фильм сцены');
      closeCompare();
    }
  });

  return { openCompare, duplicate, openNotes };
}
