// «▶ Проверить постановку»: the staging check of the saved scene without any window (GET /api/previs/scene/plan —
// the same geometry the agent runs before a build, scene_staging_check). «Ролик в Blockout» then stages the scene's shots in Blockout on one floor (heroes at passport scale, a camera
// per shot card: size, angle, lens, move) and played back as one grey reel (local_studio/previs_scene.py). No GPU,
// nothing written into the scene: it shows before any render whether the staging reads — scale, sides, framing.
import { sceneFranchiseId } from './scene-places.mjs?v=20261101-place-1';

const esc = (value = '') => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

/** The scene's place in words: the shots' «is the environment:» line (Blockout sets door, windows, sofa from it). */
export function scenePlace(cuts) {
  return [...new Set(cuts.map((cut) => /is the environment:\s*([^\n]+)/i.exec(cut.prompt || '')?.[1] || '').filter(Boolean))].join(' ').slice(0, 2000);
}

/** The shot cards as the check reads them; a shot without a card gets a medium static 35 mm on its named heroes. */
export function checkShots(cuts, heroes = []) {
  return cuts.map((cut) => {
    const card = cut.sceneShot || {};
    const named = heroes.filter((name) => new RegExp(`\\b${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i').test(cut.prompt || ''));
    return {
      duration: Math.min(15, Math.max(1, Number(cut.duration) || 5)),
      framing: card.framing || 'medium', angle: card.angle || 'eye', lens_mm: Number(card.lens_mm) || 35,
      camera_motion: card.camera_motion || 'static',
      characters: card.characters?.length ? card.characters : named,
    };
  });
}

export function installBlockout(api) {
  const box = document.createElement('div');
  box.className = 'kc-compare kc-blockout'; box.hidden = true;
  api.shell.append(box);
  api.shell.querySelector('.kc-top-right [data-kc-act="director"]')?.insertAdjacentHTML('beforebegin',
    '<button type="button" data-kc-blockout="plan" title="Герои в настоящем масштабе и камера каждого шота: кто не в кадре, кто обрезан, где камера вплотную. Без окна и без GPU">▶ Проверить постановку</button>');

  async function plan() {
    const s = api.studio();
    box.innerHTML = '<div class="kc-compare-head"><b>Постановка · сцена</b><button type="button" data-kc-blockout="close" aria-label="Закрыть">×</button></div><p class="kc-blockout-wait">Считаю камеры шотов…</p>';
    box.hidden = false;
    try {
      await s.saveDraft();
      const response = await fetch(`/api/previs/scene/plan/${encodeURIComponent(s.sceneState().activeSceneId)}`);
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Постановка не проверена');
      const heroes = result.heroes.map((hero) => `${hero.name} — ${hero.body === 'cat' ? 'кот' : hero.body === 'woman' ? 'женщина' : 'мужчина'}`).join(', ');
      box.innerHTML = `<div class="kc-compare-head"><b>Постановка · сцена</b><span class="kc-blockout-cast">${esc(heroes)} · ${result.ok ? 'ошибок постановки не найдено' : `ошибки в ${result.shots.filter((shot) => shot.notes.length).length} из ${result.shots.length} шотов`}</span>
        <button type="button" data-kc-blockout="plan">↻ Проверить снова</button><button type="button" data-kc-blockout="run" title="Те же камеры в окне Blockout: серый ролик сцены, чтобы посмотреть глазами">Ролик в Blockout</button><button type="button" data-kc-blockout="close" aria-label="Закрыть">×</button></div>
        <ul class="kc-blockout-list">${result.shots.map((shot) => `<li class="${shot.notes.length ? 'is-bad' : 'is-ok'}"><b>Шот ${shot.n}</b> ${shot.notes.length ? '⚠' : '✓'} <small>${esc(shot.label)}</small>
          ${shot.notes.map((note) => `<span class="kc-warn">${esc(note)}</span>`).join('')}</li>`).join('')}</ul>
        <p class="kc-blockout-hint">Поправь карточку шота (крупность, ракурс, объектив, движение) и проверь снова. Агент делает эту же проверку сам перед сборкой.</p>`;
    } catch (error) {
      box.querySelector('.kc-blockout-wait').textContent = error.message;
      box.querySelector('.kc-blockout-wait').classList.add('kc-warn');
    }
  }

  async function run() {
    const s = api.studio();
    const project = s.project();
    const heroes = [...new Set(s.references().map((ref) => ref.owner).filter(Boolean))];
    const shots = checkShots(s.cuts(), heroes);
    if (!shots.some((shot) => shot.characters.length)) { api.toast('В шотах не названы герои — ставить в Blockout некого'); return; }
    box.innerHTML = '<div class="kc-compare-head"><b>Blockout · сцена</b><button type="button" data-kc-blockout="close" aria-label="Закрыть">×</button></div><p class="kc-blockout-wait">Ставлю героев и камеры в Blockout и снимаю шоты… обычно 10–30 секунд.</p>';
    box.hidden = false;
    try {
      const fid = sceneFranchiseId(project);
      const cast = fid ? ((await (await fetch(`/api/franchises/${encodeURIComponent(fid)}/scene-kit`)).json()).cast || []) : [];
      const response = await fetch('/api/previs/scene/check', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aspect: project.aspect || '9:16', cast, shots, place: scenePlace(s.cuts()) }) });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Blockout не собрал сцену');
      show(result);
    } catch (error) {
      box.querySelector('.kc-blockout-wait').textContent = error.message;
      box.querySelector('.kc-blockout-wait').classList.add('kc-warn');
    }
  }

  function show(result) {
    const heroes = result.heroes.map((hero) => `${hero.name} — ${hero.body === 'cat' ? 'кот' : hero.body === 'woman' ? 'женщина' : 'мужчина'}`).join(', ');
    const bad = result.shots.filter((shot) => shot.notes.length).length;
    const stamp = Date.now();
    box.innerHTML = `<div class="kc-compare-head"><b>Blockout · сцена</b><span class="kc-blockout-cast">${esc(heroes)} · ${bad ? `ошибки в ${bad} из ${result.shots.length} шотов` : 'ошибок постановки не найдено'}</span>
      <button type="button" data-kc-blockout="run">↻ Пересобрать</button><button type="button" data-kc-blockout="close" aria-label="Закрыть">×</button></div>
      <div class="kc-blockout-sheet">${result.shots.map((shot) => `<figure class="${shot.notes.length ? 'is-bad' : 'is-ok'}">
        <button type="button" data-kc-blockout="seek" data-t="${shot.start}" title="Смотреть этот шот в ролике"><img src="${esc(shot.still)}?t=${stamp}" alt="Шот ${shot.n} в Blockout"></button>
        <figcaption><b>Шот ${shot.n}</b> ${shot.notes.length ? '⚠' : '✓'}<small>${esc(shot.label)}</small>
        ${shot.notes.map((note) => `<span class="kc-warn">${esc(note)}</span>`).join('')}</figcaption></figure>`).join('')}</div>
      <video src="${esc(result.url)}?t=${stamp}" controls muted playsinline loop></video>
      <p class="kc-blockout-hint">Серые манекены в масштабе паспорта: проверка крупности, сторон кадра и движения камеры до рендера. Поправь карточку шота (крупность, ракурс, объектив) и пересобери.</p>`;
    const video = box.querySelector('video');
    video.addEventListener('timeupdate', () => {
      const t = video.currentTime;
      box.querySelectorAll('.kc-blockout-sheet [data-kc-blockout="seek"]').forEach((b, i) => {
        const shot = result.shots[i];
        b.classList.toggle('is-current', t >= shot.start && t < shot.start + shot.duration);
      });
    });
  }

  api.shell.addEventListener('click', (event) => {
    const button = event.target.closest('[data-kc-blockout]');
    if (!button) return;
    event.stopPropagation();
    const act = button.dataset.kcBlockout;
    if (act === 'plan') void plan();
    else if (act === 'run') void run();
    else if (act === 'close') { box.querySelector('video')?.pause(); box.hidden = true; }
    else if (act === 'seek') { const v = box.querySelector('video'); v.currentTime = Number(button.dataset.t) + 0.05; void v.play().catch(() => {}); }
  });
  window.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !box.hidden) { box.querySelector('video')?.pause(); box.hidden = true; } });
}
