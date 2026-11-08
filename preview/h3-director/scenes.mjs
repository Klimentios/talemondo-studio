import { jobTitle } from './job-title.mjs?v=20261008-1';
// Scenes of several cuts that read as one: send every cut, and continue a cut from the last
// frame of a finished clip («Кадр сцены» = H3 keyframe completion at frame 0).
const $ = (selector) => document.querySelector(selector);
const studio = () => window.h3Studio;

function el(tag, attrs = {}, ...children) {
  const node = Object.assign(document.createElement(tag), attrs);
  node.append(...children);
  return node;
}

export async function sendAllCuts(api = studio(), notify = () => {}) {
  const cuts = api.cuts().filter((cut) => cut.prompt?.trim());
  if (!cuts.length) throw new Error('В кадрах нет описаний');
  for (const [index, cut] of cuts.entries()) {
    notify(`Отправляю кадр ${index + 1} из ${cuts.length}…`);
    // Only the first cut starts from «Кадр сцены»; the next ones continue the story.
    await api.enqueueCut(cut.id, { withSceneFrame: index === 0 });
  }
  return cuts.length;
}

export function completedJobs(jobs) {
  return jobs.filter((job) => job.state === 'completed').slice(0, 8);
}

function install() {
  const controls = $('#createControls .controls-content');
  if (!controls || $('#scenesAll')) return;
  const status = el('p', { className: 'scenes-status', role: 'status' });
  const all = el('button', { type: 'button', id: 'scenesAll', className: 'scenes-button',
    title: 'Отправить все кадры по очереди, каждый со своей длительностью и режиссурой' }, '▶▶ Все кадры');
  const cont = el('button', { type: 'button', id: 'scenesContinue', className: 'scenes-button',
    title: 'Начать следующую генерацию ровно с последнего кадра готового ролика' }, '⤷ С последнего кадра');
  const actions = el('div', { className: 'scene-generation-actions', ariaLabel: 'Создание последовательности шотов' }, all, cont);
  controls.prepend(actions, status);
  const colourToggle = el('button', { type: 'button', id: 'scenesColorOff', className: 'scenes-button', hidden: true });
  status.after(colourToggle);
  function renderColourToggle() {
    const match = studio()?.project?.()?.colorMatch;
    colourToggle.hidden = !match;
    colourToggle.textContent = match ? `🎨 Цвет как в предыдущем кадре: ${match.strength} — выключить` : '';
  }
  colourToggle.addEventListener('click', () => { studio().setColorMatch(null); renderColourToggle(); });
  renderColourToggle();

  all.addEventListener('click', async () => {
    const count = studio().cuts().filter((cut) => cut.prompt?.trim()).length;
    if (!count || !window.confirm(`Отправить ${count} кадр(а/ов) в очередь?`)) return;
    all.disabled = true;
    try { status.textContent = `В очереди: ${await sendAllCuts(studio(), (text) => { status.textContent = text; })} кадр(а/ов)`; }
    catch (error) { status.textContent = error.message; }
    finally { all.disabled = false; }
  });

  const pick = el('select', { id: 'scenesJob' });
  const matchColour = el('input', { type: 'checkbox', id: 'scenesColorMatch', checked: true });
  const strength = el('input', { type: 'range', id: 'scenesColorStrength', min: 0.2, max: 1, step: 0.1, value: 0.6 });
  const take = el('button', { type: 'button', className: 'scenes-primary' }, 'Взять последний кадр');
  const close = el('button', { type: 'button' }, 'Закрыть');
  const dialog = el('dialog', { id: 'scenesDialog', className: 'scenarist-dialog' },
    el('h2', {}, 'Продолжить сцену'),
    el('p', { className: 'scenarist-hint' }, 'Последний кадр выбранного ролика станет «Кадром сцены»: следующая генерация начнётся ровно с него — та же поза, свет и место. Опиши в промпте, что происходит дальше.'),
    el('label', {}, 'Готовый ролик', pick),
    el('label', { className: 'scenarist-check' }, matchColour, ' Подогнать цвет под этот кадр (баланс белого, контраст)'),
    el('label', {}, 'Сила подгонки цвета', strength),
    el('div', { className: 'scenarist-actions' }, take, close));
  document.body.append(dialog);
  close.addEventListener('click', () => dialog.close());
  cont.addEventListener('click', async () => {
    const jobs = completedJobs(await (await fetch('/api/jobs')).json());
    pick.replaceChildren(...jobs.map((job) => el('option', { value: job.job_id },
      `${jobTitle(job, 60)} · ${job.job_id.slice(0, 8)}`)));
    take.disabled = !jobs.length;
    if (!jobs.length) status.textContent = 'Нет готовых роликов';
    dialog.showModal();
  });
  take.addEventListener('click', async () => {
    take.disabled = true;
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(pick.value)}/last-frame`, { method: 'POST' });
      const asset = await response.json();
      if (!response.ok) throw new Error(asset.detail || `HTTP ${response.status}`);
      const ref = studio().addSceneFrame(asset);
      const match = studio().setColorMatch(matchColour.checked ? { asset_id: asset.asset_id, strength: strength.value } : null);
      status.textContent = `Кадр сцены добавлен как <Picture ${ref?.number ?? '?'}>: следующая генерация начнётся с него`
        + (match ? `; цвет подгоняется под него (сила ${match.strength})` : '');
      renderColourToggle();
      dialog.close();
    } catch (error) { status.textContent = error.message; }
    finally { take.disabled = false; }
  });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
}
