// «Мир фантазии» of a scene (a hero imagines the real place as another world). Without a picture of that
// world H3 invents it anew in every fantasy shot — the world changes shot to shot and leans back into the
// real place. So the world is drawn once, as one «Окружение» picture made from the real place on the GPU
// (the «Ключевой кадр» queue), and the canvas gives it only to the fantasy shots: real shots never get it,
// fantasy shots stop getting the real place pictures. The binding is plain cut.offRefs, which the render honours.
//
// Data: scene storyboard.fantasyWorld = { description, baseId, keyframeId, assetId, refId, error };
// cut.fantasy = true/false set by hand, otherwise read from the shot text (imagines / fantasy / dream).

const FANTASY_TEXT = /\b(imagin\w*|fantas\w*|dream\w*|daydream\w*)\b/i;
const esc = (value = '') => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

/** The shot's own story line (the agent's «[reference generation]» line), else its whole text. */
export function storyLine(prompt = '') {
  return /\[reference generation\]\s*([^\n]+)/i.exec(prompt)?.[1]?.trim() || '';
}

export function isFantasy(cut) {
  if (typeof cut?.fantasy === 'boolean') return cut.fantasy;
  const line = storyLine(cut?.prompt);
  return FANTASY_TEXT.test(line || cut?.prompt || '');
}

/** A first description of the world, from what the fantasy shots say about it. */
export function worldDraft(cuts) {
  const lines = cuts.filter(isFantasy).map((cut) => storyLine(cut.prompt)).filter(Boolean);
  return lines.length ? lines.join(' ') : '';
}

/** The GPU edit request: the real place becomes the imagined world, same layout, nobody in it. */
export function worldPrompt(description) {
  return [
    `Repaint this real place as the imagined world: ${String(description).trim()}`,
    'Keep the layout and the main shapes of the real place readable: each real object becomes a landmark of the imagined world at the same spot and scale.',
    'One wide establishing view of the world, empty: no people, no animals, no characters, no text, no logos.',
    'Same render style, materials quality and colour richness as the source picture; one consistent world that later shots can enter.',
  ].join('\n');
}

export const worldSize = (aspect) => (aspect === '9:16' ? '768x1344' : aspect === '1:1' ? '1024x1024' : '1344x768');

/**
 * offRefs after the world binding: the world picture only in fantasy shots; the real place pictures
 * («Окружение» that is not the world) out of fantasy shots. Every other switch stays as the user set it.
 */
export function bindWorld(cuts, references, worldRefId) {
  if (!worldRefId) return cuts;
  const realPlaces = references.filter((ref) => ref.kind === 'image' && ref.role === 'Окружение' && ref.id !== worldRefId).map((ref) => ref.id);
  return cuts.map((cut) => {
    const off = new Set(cut.offRefs || []);
    if (isFantasy(cut)) { off.delete(worldRefId); realPlaces.forEach((id) => off.add(id)); }
    else { off.add(worldRefId); realPlaces.forEach((id) => off.delete(id)); }
    const next = [...off];
    return next.length === (cut.offRefs || []).length && next.every((id) => (cut.offRefs || []).includes(id)) ? cut : { ...cut, offRefs: next };
  });
}

export function installWorlds(api) {
  const box = document.createElement('div');
  box.className = 'kc-world-box'; box.hidden = true;
  api.shell.append(box);
  api.shell.querySelector('[data-kc-board="templates"]')?.insertAdjacentHTML('afterend',
    '<button type="button" data-kc-world="open" title="Один мир фантазии на всю сцену: рисуется один раз и идёт только в шоты фантазии">✦ Мир фантазии</button>');
  let timer = 0;

  const s = () => api.studio();
  const world = () => s().project().storyboard?.fantasyWorld || null;
  function setWorld(patch) {
    const project = s().project();
    project.storyboard = { ...(project.storyboard || {}), fantasyWorld: patch ? { ...(world() || {}), ...patch } : undefined };
    if (!patch) delete project.storyboard.fantasyWorld;
    void s().saveDraft();
  }
  // Re-applies the binding (after a shot is added, flagged or the world arrives).
  function rebind() {
    const refId = world()?.refId;
    if (!refId || !s().references().some((ref) => ref.id === refId)) return;
    const before = JSON.stringify(s().cuts().map((cut) => cut.offRefs || []));
    s().editCuts((list) => bindWorld(list, s().references(), refId));
    if (JSON.stringify(s().cuts().map((cut) => cut.offRefs || [])) !== before) { void s().saveDraft(); api.sync(); }
  }

  // Only a network failure polls again. The finished world is applied once, by a tab that may write the draft:
  // an apply error used to be taken for «Studio restarting» and looped every 5 s, re-saving the draft (01.11).
  async function poll() {
    clearTimeout(timer);
    const current = world();
    if (!current?.keyframeId || current.assetId) return;
    let job;
    try {
      job = await (await fetch(`/api/codex-frames/${encodeURIComponent(current.keyframeId)}`)).json();
    } catch { timer = setTimeout(poll, 5000); return; } // Studio restarting: try again
    if (world()?.keyframeId !== current.keyframeId || world()?.assetId) return; // scene switched or applied meanwhile
    if (!['completed', 'failed', 'cancelled'].includes(job.state)) { timer = setTimeout(poll, 5000); return; }
    if (!s().canWriteDraft()) return; // stale tab: the fresh one applies it
    try {
      if (job.state === 'completed' && job.asset_id) {
        const ref = s().addReferenceAsset({ asset_id: job.asset_id, kind: 'image', original_name: 'fantasy-world.png' }, 'Окружение');
        setWorld({ assetId: job.asset_id, refId: ref?.id || job.asset_id, state: 'completed', error: '' });
        rebind(); api.sync();
        api.toast('Мир фантазии готов: он стал материалом и идёт только в шоты фантазии', 9000);
      } else setWorld({ state: job.state, error: job.error || 'Мир не нарисован' });
    } catch (error) {
      setWorld({ assetId: job.asset_id || null, state: 'completed', error: `Мир нарисован, но не добавлен в сцену: ${error.message}` });
    }
    if (!box.hidden) render();
  }

  async function generate() {
    const description = box.querySelector('textarea').value.trim();
    const baseId = box.querySelector('select')?.value;
    if (!description || !baseId) { api.toast('Опиши мир и выбери реальное место, из которого он вырастает'); return; }
    if (!s().cuts().some(isFantasy)) { api.toast('В сцене нет шотов фантазии — отметь их (правый клик по шоту → «Шот в фантазии»)'); return; }
    const button = box.querySelector('[data-kc-world="run"]');
    button.disabled = true;
    try {
      // Codex draws it on the subscription (local_studio/codex_frames.py); the GPU keyframe model is gone.
      const response = await fetch('/api/codex-frames', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        prompt: worldPrompt(description), asset_ids: [baseId], size: worldSize(s().project().aspect),
        role: 'Окружение', roles: ['Окружение'] }) });
      const job = await response.json();
      if (!response.ok) throw new Error(typeof job.detail === 'string' ? job.detail : 'Задание мира не принято');
      setWorld({ description, baseId, keyframeId: job.frame_id, assetId: null, refId: null, state: job.state, error: '' });
      void poll(); render();
    } catch (error) { api.toast(error.message, 9000); button.disabled = false; }
  }

  function render() {
    const current = world();
    const { cuts, references } = api.scene();
    const places = references.filter((ref) => ref.kind === 'image' && ref.role === 'Окружение' && ref.id !== current?.refId);
    const fantasy = cuts.map((cut, index) => ({ cut, n: index + 1 })).filter(({ cut }) => isFantasy(cut));
    const pending = current?.keyframeId && !current.assetId && !['failed', 'cancelled'].includes(current.state);
    box.innerHTML = `<div class="kc-tpl-head"><b>Мир фантазии</b><button type="button" data-kc-world="close" aria-label="Закрыть">×</button></div>
      <p class="kc-world-hint">Мир рисуется один раз из реального места и идёт только в шоты фантазии — H3 больше не выдумывает его в каждом шоте заново.</p>
      ${current?.assetId ? `<img class="kc-world-pic" src="/api/assets/${encodeURIComponent(current.assetId)}/thumbnail" alt="Мир фантазии">` : ''}
      <h5>Шоты фантазии</h5>
      <p class="kc-world-shots">${fantasy.length ? fantasy.map(({ n }) => `Шот ${n}`).join(' · ') : 'Нет — отметь шоты: правый клик по шоту → «Шот в фантазии»'}</p>
      <h5>Какой это мир (по-английски)</h5>
      <textarea rows="4" ${pending ? 'disabled' : ''}>${esc(current?.description || worldDraft(cuts))}</textarea>
      <h5>Из какого реального места он вырастает</h5>
      ${places.length ? `<select ${pending ? 'disabled' : ''}>${places.map((ref) => `<option value="${esc(ref.id)}" ${ref.id === current?.baseId ? 'selected' : ''}>${esc(ref.name || ref.role)}</option>`).join('')}</select>`
        : '<p class="kc-world-hint kc-warn">Добавь в сцену картинку реального места с ролью «Окружение».</p>'}
      ${current?.error ? `<p class="kc-world-hint kc-warn">${esc(current.error)}</p>` : ''}
      <div class="kc-world-actions">
        ${pending ? `<span class="kc-world-hint">Рисуется · ${esc(current.state || 'в очереди')}. Готовый мир сам станет материалом.</span>`
          : `<button type="button" class="kc-accent" data-kc-world="run" ${places.length ? '' : 'disabled'}>${current?.assetId ? 'Нарисовать заново' : 'Нарисовать мир'} · GPU</button>`}
        ${current?.assetId ? '<button type="button" data-kc-world="rebind">Привязать к шотам</button>' : ''}
      </div>`;
    box.hidden = false;
  }

  api.shell.addEventListener('click', (event) => {
    const button = event.target.closest('[data-kc-world]');
    if (!button) return;
    event.stopPropagation();
    const act = button.dataset.kcWorld;
    if (act === 'open') { if (box.hidden) render(); else box.hidden = true; }
    else if (act === 'close') box.hidden = true;
    else if (act === 'run') void generate();
    else if (act === 'rebind') { rebind(); api.toast('Мир идёт только в шоты фантазии, реальное место — в остальные'); }
  });

  /** For the shot menu: flip a shot between real and fantasy, then re-bind. */
  function toggleFantasy(id) {
    s().editCuts((list) => list.map((cut) => (cut.id === id ? { ...cut, fantasy: !isFantasy(cut) } : cut)));
    void s().saveDraft(); rebind(); api.sync();
    if (!box.hidden) render();
  }

  window.addEventListener('h3:shot-added', () => setTimeout(rebind, 0));
  window.addEventListener('h3:scene-active-changed', () => { box.hidden = true; void poll(); });
  void poll();
  return { toggleFantasy, isFantasy };
}
