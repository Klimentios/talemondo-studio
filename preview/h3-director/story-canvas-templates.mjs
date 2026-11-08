// Scene templates of the story canvas (like saved canvases in Higgsfield Canvas): the shape of a scene — its shots
// (text, length, direction, continue/cut, materials switched off) and its materials — saved once and started again
// as a new scene. Takes, frames, notes and job ids never travel. Templates live in the project draft
// (project.sceneTemplates), so they survive a restart and sit in the draft history like everything else.
// Built-in templates are structures with bracketed English placeholders: a draft to fill in, never a ready prompt.

export const BUILTIN_TEMPLATES = [
  {
    id: 'builtin-reaction', builtin: true, title: 'Реакция: событие → тейк → ответ', note: '3 шота: общий план, крупный тейк героя, ответ второго героя.',
    cuts: [
      { prompt: '[Wide shot] [Place]. [Hero A] is busy with [small action] when [event] happens at the edge of frame.', duration: 6, link: false },
      { prompt: '[Close-up] [Hero A] freezes, eyes go to [event]; a beat of thought, then a clear reaction: [take].', duration: 5, link: false },
      { prompt: '[Medium shot] [Hero B] answers the reaction with [physical action]; [Hero A] stays in frame reacting.', duration: 6, link: false },
    ],
    references: [],
  },
  {
    id: 'builtin-dialogue', builtin: true, title: 'Диалог: установка и восьмёрка', note: '4 шота: общий, плечо A, плечо B, общий с итогом. Ось 180° держится.',
    cuts: [
      { prompt: '[Wide two-shot] [Hero A] on screen left, [Hero B] on screen right, [place]; they face each other.', duration: 6, link: false },
      { prompt: '[Over-the-shoulder of Hero B] [Hero A] looks screen right and says: "[line]".', duration: 5, link: false },
      { prompt: '[Over-the-shoulder of Hero A] [Hero B] looks screen left and answers: "[line]".', duration: 5, link: false },
      { prompt: '[Wide two-shot] Same axis. [Outcome of the talk shown in action].', duration: 6, link: false },
    ],
    references: [],
  },
  {
    id: 'builtin-oner', builtin: true, title: 'Одно действие в три шота подряд', note: '3 продолжения: каждый шот начинается с последнего кадра предыдущего.',
    cuts: [
      { prompt: '[Medium shot] [Hero] starts [action] in [place].', duration: 8, link: false },
      { prompt: 'Continuous: [Hero] carries [action] further, [obstacle] appears.', duration: 8, link: true },
      { prompt: 'Continuous: [Hero] finishes [action]; [result] lands in frame.', duration: 8, link: true },
    ],
    references: [],
  },
];

const clean = (value) => JSON.parse(JSON.stringify(value ?? null));

/** The template made from a scene: shots and materials only. */
export function templateFromScene({ title, cuts = [], references = [] }, now = Date.now()) {
  return {
    id: `tpl-${now.toString(36)}`, title: String(title || 'Шаблон').trim(), createdAt: new Date(now).toISOString(),
    cuts: cuts.map((cut, index) => ({
      prompt: cut.prompt || '', duration: cut.duration, director: clean(cut.director || {}),
      directorProfileId: cut.directorProfileId || '', offRefs: [...(cut.offRefs || [])],
      link: index > 0 && cut.startFrame?.source === 'previous',
    })),
    references: references.map((ref) => ({ id: ref.id, kind: ref.kind, name: ref.name || '', role: ref.role || '', owner: ref.owner || '' })),
  };
}

/** Fields for each cut of a new scene from a template (order = template order). */
export function cutsFromTemplate(template, present = null) {
  const keep = (ids) => (present ? ids.filter((id) => present.has(id)) : ids);
  return (template.cuts || []).map((cut, index) => ({
    prompt: cut.prompt || '', duration: cut.duration || 5, director: clean(cut.director || {}),
    directorProfileId: cut.directorProfileId || '', offRefs: keep([...(cut.offRefs || [])]),
    startFrame: index > 0 && cut.link ? { source: 'previous', asset_id: null } : null,
  }));
}

export const templateSummary = (template) => `${template.cuts.length} шот.${template.references.length ? ` · ${template.references.length} мат.` : ''}`;

const esc = (value = '') => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

export function saveTemplate(api) {
  const { active, cuts, references } = api.scene();
  const title = window.prompt('Название шаблона', active.title || 'Шаблон');
  if (!title?.trim()) return;
  const project = api.studio().project();
  project.sceneTemplates = [...(project.sceneTemplates || []), templateFromScene({ title, cuts, references })];
  void api.studio().saveDraft();
  api.toast(`Шаблон «${title.trim()}» сохранён: ${cuts.length} шот., ${references.length} мат. Дубли и кадры в шаблон не входят.`);
}

async function startFromTemplate(api, template) {
  const s = api.studio();
  let present = null;
  if (template.references.length) {
    try {
      const assets = await (await fetch('/api/assets')).json();
      present = new Set(assets.map((asset) => asset.asset_id));
    } catch { api.toast('Библиотека недоступна — сцена из шаблона не создана'); return; }
  }
  const fields = cutsFromTemplate(template, present);
  s.createScene();
  s.renameScene(s.sceneState().activeSceneId, template.title);
  for (let i = 1; i < fields.length; i += 1) s.addShot({ link: false });
  s.editCuts((list) => list.map((cut, index) => (fields[index] ? { ...cut, ...fields[index] } : cut)));
  const missing = [];
  for (const ref of template.references) {
    if (present && !present.has(ref.id)) { missing.push(ref.role || ref.name); continue; }
    s.addReferenceAsset({ asset_id: ref.id, kind: ref.kind, original_name: ref.name }, ref.role, ref.owner);
  }
  void s.saveDraft();
  api.resetLayout();
  api.toast(missing.length ? `Сцена из шаблона готова; нет в библиотеке: ${missing.join(', ')}` : `Сцена «${template.title}» создана из шаблона`, 9000);
}

/** The «Шаблоны» popover: built-in and saved templates, start a scene from one, delete a saved one. */
export function templatesPopover(api, box) {
  const saved = api.studio().project().sceneTemplates || [];
  const row = (t) => `<li><div><b>${esc(t.title)}</b><small>${esc(t.note || templateSummary(t))}</small></div>
    <button type="button" data-tpl="use" data-id="${esc(t.id)}">Новая сцена</button>
    ${t.builtin ? '' : `<button type="button" data-tpl="delete" data-id="${esc(t.id)}" aria-label="Удалить шаблон" title="Удалить шаблон">×</button>`}</li>`;
  box.innerHTML = `<div class="kc-tpl-head"><b>Шаблоны сцен</b><button type="button" data-tpl="save">＋ Сохранить эту сцену</button><button type="button" data-tpl="close" aria-label="Закрыть">×</button></div>
    ${saved.length ? `<h5>Мои</h5><ul>${saved.map(row).join('')}</ul>` : '<p>Сохрани сцену, чтобы начинать следующие с её шотов и материалов.</p>'}
    <h5>Готовые структуры</h5><ul>${BUILTIN_TEMPLATES.map(row).join('')}</ul>`;
  box.hidden = false;
  box.onclick = (event) => {
    const button = event.target.closest('[data-tpl]');
    if (!button) return;
    event.stopPropagation();
    const all = [...(api.studio().project().sceneTemplates || []), ...BUILTIN_TEMPLATES];
    const template = all.find((t) => t.id === button.dataset.id);
    if (button.dataset.tpl === 'close') box.hidden = true;
    else if (button.dataset.tpl === 'save') { saveTemplate(api); templatesPopover(api, box); }
    else if (button.dataset.tpl === 'use' && template) { box.hidden = true; void startFromTemplate(api, template); }
    else if (button.dataset.tpl === 'delete' && template && window.confirm(`Удалить шаблон «${template.title}»? Сцены, созданные из него, останутся.`)) {
      const project = api.studio().project();
      project.sceneTemplates = (project.sceneTemplates || []).filter((t) => t.id !== template.id);
      void api.studio().saveDraft(); templatesPopover(api, box);
    }
  };
}
