import { errorText } from './api-error.mjs?v=20261008-1';
// «Сценарист»: the Studio agent (Claude/Codex by subscription, local_studio/agent_tasks.py) turns a free-form brief (any language) into the official
// MiniMax H3 six-section prompt, using this project's references, roles and owners.
const $ = (selector) => document.querySelector(selector);

function studio() { return window.h3Studio || null; }

function el(tag, attrs = {}, ...children) {
  const node = Object.assign(document.createElement(tag), attrs);
  node.append(...children);
  return node;
}

function install() {
  const helpers = $('.prompt-helpers');
  if (!helpers || $('#scenaristOpen')) return;
  const open = el('button', { type: 'button', id: 'scenaristOpen', className: 'scenarist-open',
    title: 'Агент перепишет сцену в официальный формат MiniMax H3' }, '✍ Сценарист');
  helpers.prepend(open);

  const brief = el('textarea', { id: 'scenaristBrief', rows: 6,
    placeholder: 'Опиши сцену как хочешь, по-русски: кто, что делает, где, как снимает камера, кто что говорит…' });
  const shots = el('input', { id: 'scenaristShots', type: 'number', min: 1, max: 6, value: 1 });
  const speakers = el('input', { id: 'scenaristSpeakers', placeholder: 'Кто говорит, через запятую (пусто — без реплик)' });
  const output = el('textarea', { id: 'scenaristOutput', rows: 14, readOnly: true });
  const everyCut = el('input', { id: 'scenaristAllCuts', type: 'checkbox' });
  const style = el('input', { id: 'scenaristStyle', placeholder: 'Общий стиль сцены (одинаковый для всех кадров), напр. ночной город, холодный неон, 35 мм' });
  const cameraMoves = {
    '': ['Авто', ['']], push: ['Наезд', ['in']], pull: ['Отъезд', ['out']],
    zoom: ['Зум', ['in', 'out']], pan: ['Панорама', ['left', 'right']],
    truck: ['Боковой ход', ['left', 'right']], tilt: ['Наклон', ['up', 'down']],
    pedestal: ['Подъём / спуск', ['up', 'down']], arc: ['Облёт', ['', 'left', 'right']],
    tracking: ['Следование', ['']], static: ['Статично', ['']],
    shake: ['Ручная камера', ['slight', 'strong']], roll: ['Вращение', ['clockwise', 'counterclockwise']],
    pov: ['От первого лица', ['']],
  };
  const directionNames = { '': 'Без направления', in: 'Вперёд', out: 'Назад', left: 'Влево', right: 'Вправо',
    up: 'Вверх', down: 'Вниз', slight: 'Слегка', strong: 'Сильно', clockwise: 'По часовой', counterclockwise: 'Против часовой' };
  const cameraMove = el('select', { id: 'scenaristCameraMove', ariaLabel: 'Движение камеры' });
  const cameraDirection = el('select', { id: 'scenaristCameraDirection', ariaLabel: 'Направление движения' });
  const cameraSpeed = el('select', { id: 'scenaristCameraSpeed', ariaLabel: 'Скорость камеры' });
  const cameraAmplitude = el('select', { id: 'scenaristCameraAmplitude', ariaLabel: 'Размах движения' });
  for (const [value, [label]] of Object.entries(cameraMoves)) cameraMove.add(new Option(label, value));
  for (const [value, label] of [['normal', 'Обычная'], ['slow', 'Медленная'], ['fast', 'Быстрая']]) cameraSpeed.add(new Option(label, value));
  for (const [value, label] of [['medium', 'Средний'], ['small', 'Небольшой'], ['large', 'Большой']]) cameraAmplitude.add(new Option(label, value));
  const updateDirection = () => {
    cameraDirection.replaceChildren();
    for (const value of cameraMoves[cameraMove.value][1]) cameraDirection.add(new Option(directionNames[value], value));
    const fixed = ['', 'static', 'shake', 'pov'].includes(cameraMove.value);
    cameraSpeed.disabled = fixed; cameraAmplitude.disabled = fixed;
  };
  cameraMove.addEventListener('change', updateDirection);
  updateDirection();
  const cameraSetting = () => cameraMove.value ? { camera: { movement: cameraMove.value,
    direction: cameraDirection.value, speed: cameraSpeed.value, amplitude: cameraAmplitude.value } } : {};
  const problems = el('ul', { id: 'scenaristProblems', className: 'scenarist-problems' });
  const status = el('p', { id: 'scenaristStatus', className: 'scenarist-status', role: 'status' });
  const write = el('button', { type: 'button', className: 'scenarist-primary' }, 'Написать промпт');
  const use = el('button', { type: 'button', disabled: true }, 'Вставить в сцену');
  const close = el('button', { type: 'button' }, 'Закрыть');
  const dialog = el('dialog', { id: 'scenaristDialog', className: 'scenarist-dialog' },
    el('h2', {}, 'Сценарист'),
    el('p', { className: 'scenarist-hint' }, 'Пиши свободно. Агент (Claude или Codex по подписке) соберёт промпт в формате, на котором обучена H3: герои из твоих референсов, шоты с таймкодами, камера и звук. Реплики останутся на русском.'),
    el('label', {}, 'Сцена', brief),
    el('div', { className: 'scenarist-row' }, el('label', {}, 'Шотов', shots), el('label', {}, 'Говорят', speakers)),
    el('label', {}, 'Стиль', style),
    el('div', { className: 'scenarist-camera' },
      el('label', {}, 'Камера', cameraMove), el('label', {}, 'Направление', cameraDirection),
      el('label', {}, 'Скорость', cameraSpeed), el('label', {}, 'Размах', cameraAmplitude)),
    el('label', { className: 'scenarist-check' }, everyCut, ' Для всех кадров сцены: в описании кадра — что происходит именно в нём, общий бриф выше'),
    el('div', { className: 'scenarist-actions' }, write, use, close),
    status, problems, el('label', {}, 'Промпт для H3', output));
  document.body.append(dialog);

  open.addEventListener('click', () => {
    if (!brief.value.trim()) brief.value = $('#mainPrompt')?.value || '';
    dialog.showModal();
  });
  close.addEventListener('click', () => dialog.close());
  use.addEventListener('click', () => {
    const prompt = $('#mainPrompt');
    prompt.value = output.value;
    prompt.dispatchEvent(new Event('input', { bubbles: true }));
    dialog.close();
  });
  write.addEventListener('click', async () => {
    const project = studio()?.project?.() || { references: [], cuts: [] };
    if (everyCut.checked) return writeEveryCut(project);
    const duration = Number($('#duration')?.value) || 10;
    const payload = {
      brief: brief.value.trim(), duration, shots: Math.min(6, Math.max(1, Number(shots.value) || 1)),
      speaking_owners: speakers.value.split(',').map((s) => s.trim()).filter(Boolean),
      style_hint: style.value.trim(),
      ...cameraSetting(),
      use_video_audio: Boolean(project.useVideoAudio),
      references: (project.references || []).map((ref) => ({
        asset_id: ref.id, kind: ref.kind, number: ref.number, role: ref.role || '', owner: ref.owner || '' })),
    };
    write.disabled = true; use.disabled = true; problems.replaceChildren(); output.value = '';
    status.textContent = 'Агент пишет промпт… обычно 1–3 минуты';
    try {
      const response = await fetch('/api/scenarist', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorText(result, response.status));
      output.value = result.prompt;
      problems.replaceChildren(...(result.problems || []).map((p) => el('li', {}, p)));
      status.textContent = result.problems?.length ? 'Готово, но есть замечания к формату — проверь перед запуском' : 'Готово: формат совпадает с официальным';
      use.disabled = !result.prompt;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      write.disabled = false;
    }
  });

  // One request per cut with the same brief, style, speakers and references: identical subject
  // definitions and style sentence in every cut, only the action differs.
  async function writeEveryCut(project) {
    const cuts = project.cuts || [];
    write.disabled = true; use.disabled = true; problems.replaceChildren(); output.value = '';
    const remarks = [];
    try {
      for (const [index, cut] of cuts.entries()) {
        status.textContent = `Кадр ${index + 1} из ${cuts.length}: агент пишет промпт…`;
        const payload = {
          brief: `${brief.value.trim()}\n\nЭТОТ КАДР (${index + 1} из ${cuts.length}): ${cut.prompt?.trim() || 'продолжение сцены'}`,
          duration: Number(cut.duration) || 10, shots: Math.min(6, Math.max(1, Number(shots.value) || 1)),
          speaking_owners: speakers.value.split(',').map((s) => s.trim()).filter(Boolean),
          style_hint: style.value.trim(), use_video_audio: Boolean(project.useVideoAudio),
          ...cameraSetting(),
          references: (project.references || []).map((ref) => ({
            asset_id: ref.id, kind: ref.kind, number: ref.number, role: ref.role || '', owner: ref.owner || '' })),
        };
        const response = await fetch('/api/scenarist', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload) });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(`Кадр ${index + 1}: ${errorText(result, response.status)}`);
        studio().setCutPrompt(cut.id, result.prompt);
        for (const problem of result.problems || []) remarks.push(`Кадр ${index + 1}: ${problem}`);
        output.value += `${output.value ? '\n\n' : ''}=== КАДР ${index + 1} ===\n${result.prompt}`;
      }
      problems.replaceChildren(...remarks.map((p) => el('li', {}, p)));
      status.textContent = `Готово: промпты записаны во все ${cuts.length} кадр(а/ов)` + (remarks.length ? ' — есть замечания' : '');
    } catch (error) {
      status.textContent = error.message;
    } finally {
      write.disabled = false;
    }
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
else install();
