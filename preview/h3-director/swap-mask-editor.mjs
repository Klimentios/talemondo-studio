// Masked hero swap (app/swap_mask.py, A/B): on the first frame of the source video the user draws one box
// per hero around the performer who becomes that hero. Only those performers are redrawn on the GPU.
export const SWAP_MASK_GROW = 24;

// The mask belongs to the heroes paired with this video and to the moment the job's excerpt starts.
export function pairedHeroes(ref) {
  return (ref.performers?.pairs || []).map(pair => pair.owner).filter(Boolean);
}

export function swapMaskLabel(ref, start = 0) {
  const count = ref.swapMask?.performers?.length || 0;
  if (!count) return 'Маска A/B';
  return (ref.swapMask.at || 0) !== start ? 'Маска для другого шота' : `✓ маска · ${count}`;
}

export function swapMaskButton(ref, project, start = 0) {
  const heroes = pairedHeroes(ref);
  if (ref.kind !== 'video' || ref.role !== 'Исходное видео' || project.mode !== 'replace' || !heroes.length) return null;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'reference-pair-button';
  button.dataset.action = 'swap-mask';
  button.textContent = swapMaskLabel(ref, start);
  button.setAttribute('aria-label', `Маска исполнителей для ${ref.name}`);
  return button;
}

// Settings field for a replacement job: the mask of its source video, only for heroes it still pairs.
export function swapMaskSetting(project, start = 0) {
  if (project.mode !== 'replace') return {};
  const video = project.references.find(ref => ref.kind === 'video' && ref.role === 'Исходное видео' && ref.swapMask);
  if (!video || (video.swapMask.at || 0) !== start) return {};
  const heroes = pairedHeroes(video);
  const performers = (video.swapMask.performers || []).filter(p => heroes.includes(p.name));
  if (!heroes.length || performers.length !== heroes.length) return {};
  // The user drew the boxes by hand: that is the A/B request local_studio/store.py requires.
  return { swap_mask: { performers, grow: video.swapMask.grow ?? SWAP_MASK_GROW },
    ab_user_request: 'Studio: пользователь нарисовал маску замены в форме' };
}

export function clampBox(x0, y0, x1, y1) {
  const left = Math.max(0, Math.min(x0, x1)), top = Math.max(0, Math.min(y0, y1));
  const right = Math.min(1, Math.max(x0, x1)), bottom = Math.min(1, Math.max(y0, y1));
  const round = (v) => Math.round(v * 1000) / 1000;
  return [round(left), round(top), round(right - left), round(bottom - top)];
}

export function openSwapMaskDialog(ref, videoUrl, onSave, start = 0) {
  const heroes = pairedHeroes(ref);
  const boxes = new Map((ref.swapMask?.performers || []).map(p => [p.name, p.box]));
  let active = heroes[0];
  const dialog = document.createElement('dialog');
  dialog.className = 'reference-pair-dialog swap-mask-dialog';
  const form = document.createElement('form');
  form.method = 'dialog';
  const heading = document.createElement('h2');
  heading.textContent = 'Маска исполнителей';
  const description = document.createElement('p');
  description.textContent = `Кадр ${start.toFixed(2)} с — начало части.`
    + '  Выбери героя и обведи рамкой его исполнителя целиком, с волосами и хвостом. Перерисуются только обведённые, остальное останется как в исходнике.';
  const stage = document.createElement('div');
  stage.className = 'swap-mask-stage';
  const video = document.createElement('video');
  video.src = videoUrl; video.muted = true; video.preload = 'auto'; video.playsInline = true;
  // The job's excerpt starts here: boxes belong to that frame, not to frame 0 of the whole file.
  video.addEventListener('loadeddata', () => { video.currentTime = start; }, { once: true });
  const layer = document.createElement('div');
  layer.className = 'swap-mask-layer';
  stage.append(video, layer);
  const picker = document.createElement('div');
  picker.className = 'swap-mask-heroes';
  const draw = () => {
    layer.replaceChildren(...[...boxes].map(([name, [x, y, w, h]]) => {
      const rect = document.createElement('div');
      rect.className = `swap-mask-box${name === active ? ' active' : ''}`;
      Object.assign(rect.style, { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` });
      rect.textContent = name;
      return rect;
    }));
    for (const button of picker.children) button.setAttribute('aria-pressed', String(button.dataset.hero === active));
  };
  for (const hero of heroes) {
    const button = document.createElement('button');
    button.type = 'button'; button.dataset.hero = hero; button.textContent = hero;
    button.addEventListener('click', () => { active = hero; draw(); });
    picker.append(button);
  }
  let anchor = null;
  const point = (event) => {
    const r = layer.getBoundingClientRect();
    return [(event.clientX - r.left) / r.width, (event.clientY - r.top) / r.height];
  };
  layer.addEventListener('pointerdown', (event) => { anchor = point(event); layer.setPointerCapture(event.pointerId); });
  layer.addEventListener('pointermove', (event) => {
    if (!anchor) return;
    const [x, y] = point(event);
    boxes.set(active, clampBox(anchor[0], anchor[1], x, y));
    draw();
  });
  layer.addEventListener('pointerup', () => { anchor = null; });
  const actions = document.createElement('div');
  actions.className = 'reference-pair-actions';
  const clear = document.createElement('button');
  clear.type = 'button'; clear.textContent = 'Без маски';
  clear.addEventListener('click', () => { onSave(null); dialog.close(); });
  const cancel = document.createElement('button');
  cancel.type = 'button'; cancel.textContent = 'Отмена';
  cancel.addEventListener('click', () => dialog.close());
  const save = document.createElement('button');
  save.type = 'submit'; save.textContent = 'Сохранить маску';
  actions.append(clear, cancel, save);
  form.append(heading, description, picker, stage, actions);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const missing = heroes.filter(hero => { const b = boxes.get(hero); return !b || b[2] < 0.02 || b[3] < 0.02; });
    if (missing.length) {
      description.textContent = `Обведи исполнителя для: ${missing.join(', ')}.`;
      return;
    }
    onSave({ performers: heroes.map(name => ({ name, box: boxes.get(name) })), grow: SWAP_MASK_GROW, at: start });
    dialog.close();
  });
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  dialog.append(form);
  document.body.append(dialog);
  draw();
  dialog.showModal();
}
