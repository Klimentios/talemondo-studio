// Read-only film strip for the current canvas. The same shot model and QC receipt drive the board.
import { nodeState, plural } from './story-canvas-model.mjs?v=20261101-keyframe-1';
import { acceptedShot, frameAccepted, keyframeJobsOf } from './storyboard-model.mjs?v=20261102-take-admission-1';

const esc = (value = '') => String(value).replace(/[&<>"']/g, (char) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const frameUrl = (id) => `/api/assets/${encodeURIComponent(id)}/thumbnail`;
const posterUrl = (id) => `/api/jobs/${encodeURIComponent(id)}/poster`;

export function sceneOverviewRows(cuts = [], jobs = new Map(), board = {}, references = []) {
  return cuts.map((cut, index) => {
    const previous = cuts[index - 1];
    const source = cut.startFrame?.source || 'none';
    const jobId = cut.shotJobId || '';
    const status = nodeState(cuts, index, jobs).status;
    const approved = status === 'done' && acceptedShot(cut);
    const priorJob = previous?.shotJobId;
    const previousFrame = source === 'previous' && cut.startFrame?.from_job === priorJob
      ? board.lastFrames?.[priorJob] : null;
    const frameId = source === 'previous' ? previousFrame : cut.startFrame?.asset_id;
    const frameApproved = source === 'keyframe' && frameAccepted(cut, references);
    const shot = cut.sceneShot || {};
    const qc = cut.shotQc?.job_id === jobId ? cut.shotQc : null;
    const rejected = cut.shotQcReview === 'rejected' || ['flagged', 'rejected'].includes(cut.takeReview?.state)
      || qc?.decision === 'regenerate';
    let phase = 'draft'; let next = 'Проверить постановку и материалы';
    if (status === 'stale') { phase = 'stale'; next = 'Пересобери: входные данные или предыдущий дубль изменились'; }
    else if (status === 'missing') { phase = 'blocked'; next = 'Сверь задание: результат отсутствует'; }
    else if (status === 'failed') { phase = 'blocked'; next = 'Разбери ошибку задания до повтора'; }
    else if (status === 'queued' || status === 'running') { phase = 'working'; next = 'Дождись результата задания'; }
    else if (approved) { phase = 'accepted'; next = 'Дубль принят для сборки'; }
    else if (status === 'done' && rejected) { phase = 'blocked'; next = 'Дубль отклонён: исправь причину и пересними шот'; }
    else if (status === 'done') { phase = 'review'; next = 'Просмотри весь дубль и прими решение QC'; }
    else if (!String(cut.prompt || '').trim()) next = 'Опиши действие шота';
    else if (keyframeJobsOf(cut).length && !cut.startFrame?.asset_id) { phase = 'working'; next = 'Дождись опорного кадра'; }
    else if (source === 'keyframe' && !cut.startFrame?.asset_id) next = 'Создай опорный кадр';
    else if (source === 'keyframe' && !frameApproved) { phase = 'review'; next = 'Проверь и прими опорный кадр'; }
    else if (source === 'library' && !cut.startFrame?.asset_id) next = 'Выбери начальный кадр';
    else if (source === 'previous' && (!previous || nodeState(cuts, index - 1, jobs).status !== 'done'
                || !acceptedShot(previous))) next = 'Сначала прими предыдущий дубль';
    const transition = index === 0 ? 'Начало сцены'
      : source === 'previous' ? 'Продолжение с конца предыдущего'
        : board.linkPrevious !== false ? 'Новый ракурс · окружение из предыдущего' : 'Новый ракурс';
    return { id: cut.id, number: index + 1, duration: Number(cut.duration) || 0,
      action: String(shot.action || cut.prompt || '').trim(),
      before: String(shot.state_before || '').trim(), after: String(shot.state_after || '').trim(),
      firstFrame: String(shot.first_frame || cut.productionSpec?.shot?.first_frame_description || '').trim(),
      frameId: frameId || '', frameApproved, source, transition, phase, next,
      status, jobId, resultId: status === 'done' || status === 'stale' ? jobId : '', approved };
  });
}

function card(row) {
  const frame = row.frameId ? `<img src="${frameUrl(row.frameId)}" alt="" loading="lazy">`
    : '<span>Кадр ещё не выбран</span>';
  const result = row.resultId ? `<img src="${posterUrl(row.resultId)}" alt="" loading="lazy">`
    : '<span>Дубля ещё нет</span>';
  const frameLabel = row.source === 'previous' ? 'Из предыдущего дубля'
    : row.frameApproved ? 'Кадр принят' : row.frameId ? 'Кадр выбран' : 'Без опорного кадра';
  const qcLabel = row.approved ? 'QC · принято' : row.phase === 'review' && row.resultId ? 'QC · нужен просмотр'
    : row.resultId ? 'QC · недействительно' : 'QC · впереди';
  return `<article class="so-shot" data-phase="${row.phase}">
    <div class="so-shot-head"><span class="so-shot-number">${String(row.number).padStart(2, '0')}</span>
      <strong>Шот ${row.number}</strong><span>${row.duration} с</span></div>
    <div class="so-frames"><div><span class="so-frame-label">НАЧАЛО</span><div class="so-image">${frame}</div>
      <small>${esc(frameLabel)}</small></div><div><span class="so-frame-label">РЕЗУЛЬТАТ</span><div class="so-image">${result}</div>
      <small>${esc(qcLabel)}</small></div></div>
    <p class="so-action">${esc(row.action || 'Действие ещё не описано')}</p>
    ${row.firstFrame ? `<p class="so-first-frame" title="${esc(row.firstFrame)}">Начальный кадр: ${esc(row.firstFrame)}</p>` : ''}
    <dl class="so-states"><div><dt>До</dt><dd>${esc(row.before || 'Не задано')}</dd></div>
      <div><dt>После</dt><dd>${esc(row.after || 'Не задано')}</dd></div></dl>
    <p class="so-transition">${esc(row.transition)}</p>
    <p class="so-next">${esc(row.next)}</p>
    <button type="button" class="k-btn k-btn--sm" data-overview-shot="${esc(row.id)}">Открыть шот ${row.number}</button>
  </article>`;
}

export function installSceneOverview({ shell, snapshot, selectShot }) {
  const layer = document.createElement('section');
  layer.className = 'so-overview'; layer.hidden = true;
  layer.setAttribute('role', 'dialog'); layer.setAttribute('aria-modal', 'true');
  layer.setAttribute('aria-labelledby', 'so-title');
  layer.innerHTML = `<div class="so-sheet k-panel">
    <header class="so-header"><div><span class="so-eyebrow">TaleMondo / МОНТАЖНЫЙ ОБЗОР</span>
      <h2 id="so-title">Обзор сцены</h2><p class="so-summary" aria-live="polite"></p></div>
      <button type="button" class="k-btn k-btn--ghost so-close" aria-label="Закрыть обзор сцены">Закрыть ✕</button></header>
    <p class="so-guidance">Начало → действие → результат. Каждый дубль требует отдельного решения QC.</p>
    <div class="so-list"></div></div>`;
  shell.append(layer);
  const list = layer.querySelector('.so-list');
  let rendered = '';
  const trigger = shell.querySelector('[data-kc-act="overview"]');
  const close = () => {
    if (layer.hidden) return;
    layer.hidden = true;
    for (const child of shell.children) if (child !== layer) child.inert = false;
    trigger?.setAttribute('aria-expanded', 'false');
    trigger?.focus();
  };
  const render = () => {
    if (layer.hidden) return;
    const { title, cuts, jobs, board, references } = snapshot();
    const rows = sceneOverviewRows(cuts, jobs, board, references);
    const key = JSON.stringify([title, rows]);
    if (key === rendered) return;
    rendered = key;
    layer.querySelector('#so-title').textContent = title || 'Обзор сцены';
    const accepted = rows.filter((row) => row.approved).length;
    const duration = rows.reduce((total, row) => total + row.duration, 0);
    layer.querySelector('.so-summary').textContent = `${plural(rows.length, 'шот', 'шота', 'шотов')} · ${duration} с · принято ${accepted}/${rows.length}`;
    list.innerHTML = rows.length ? rows.map(card).join('') : '<p class="so-empty">Добавь первый шот на холст.</p>';
    list.querySelectorAll('img').forEach((image) => image.addEventListener('error', () => {
      image.replaceWith(document.createTextNode('Превью недоступно'));
    }, { once: true }));
  };
  const open = () => {
    if (!layer.hidden) return close();
    layer.hidden = false;
    for (const child of shell.children) if (child !== layer) child.inert = true;
    trigger?.setAttribute('aria-expanded', 'true');
    render(); layer.querySelector('.so-close').focus();
  };
  layer.addEventListener('click', (event) => {
    if (event.target === layer || event.target.closest('.so-close')) return close();
    const id = event.target.closest('[data-overview-shot]')?.dataset.overviewShot;
    if (id) { close(); selectShot(id); }
  });
  layer.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.stopPropagation(); close(); }
    if (event.key !== 'Tab') return;
    const focusable = [...layer.querySelectorAll('button:not(:disabled)')];
    const first = focusable[0]; const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  return { open, close, render, get isOpen() { return !layer.hidden; } };
}
