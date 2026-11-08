// Controls for GPU features added after the original Video composer.
// The GPU capability response, not this module, decides which effects are selectable.
import { effectiveFrameCount } from './model.mjs?v=20261106-refmatch';
const labels = {
  art_is_explosion: 'Взрыв искусства', blooming_flowers: 'Цветение', bullet_time: 'Bullet time',
  dark_magic: 'Тёмная магия', fire_breath: 'Огненное дыхание', four_seasons: 'Четыре сезона',
  kiss_camera: 'Поцелуй в камеру', spiral_ascent: 'Спиральный подъём',
  storm_magic: 'Магия бури', truman_show: 'Шоу Трумана',
};

let availableEffects = [];
let capabilitiesKnown = false;

export function guideFramesForProject(project, duration) {
  const images = (project.references || []).filter((ref) => ref.kind === 'image' && ref.role === 'Кадр сцены');
  const configured = Array.isArray(project.guideFrames) ? project.guideFrames : null;
  const selected = configured ?? images.slice(0, 1).map((ref) => ({ asset_id: ref.id, frame_idx: 0 }));
  const bound = effectiveFrameCount(Number(duration));
  const continuation = (project.references || []).some((ref) => ref.kind === 'video' && ref.role === 'Продолжение');
  return selected.map((guide) => {
    const ref = images.find((item) => item.id === guide.asset_id);
    if (!ref) return null;
    const index = Number(guide.frame_idx);
    const resolved = index < 0 ? index + bound : index;
    if (!Number.isInteger(index) || index < -bound || index >= bound || (continuation && resolved < 22))
      throw new Error(`Кадр сцены «${ref.name}»: укажи позицию от ${continuation ? 22 : 0} до ${bound - 1} или −1 для конца`);
    return [ref.number, index];
  }).filter(Boolean);
}

export function officialSettings(project, duration) {
  const effect = project.cameraEffect || 'none';
  if (effect !== 'none' && (!capabilitiesKnown || !availableEffects.includes(effect)))
    throw new Error('Эффект камеры недоступен на подключённом GPU. Проверь возможности сервера.');
  return { guide_frames: guideFramesForProject(project, duration), ...(effect !== 'none' ? { camera_effect: effect } : {}) };
}

export function installOfficialControls({ project, onChange }) {
  // Camera effects and guide-frame binding are professional controls. Keeping
  // them inside the prompt made the primary Create task several screens tall.
  const host = document.querySelector('#createControls .controls-content');
  if (!host) return { render() {}, refreshCapabilities() {} };
  const wrap = document.createElement('section');
  wrap.className = 'official-controls';
  wrap.setAttribute('aria-label', 'Эффект камеры и ключевые кадры');
  wrap.innerHTML = '<div class="official-controls-head"><strong>Управление шотом</strong><button type="button" class="official-refresh">↻ Проверить GPU</button></div><div class="official-controls-grid"><label>Эффект камеры<select class="official-effect"></select></label><div class="official-guides"><span>Ключевые кадры</span><div class="official-guide-list"></div></div></div><p class="official-capability" role="status"></p>';
  host.append(wrap);
  const effectSelect = wrap.querySelector('.official-effect');
  const guideList = wrap.querySelector('.official-guide-list');
  const status = wrap.querySelector('.official-capability');

  function render() {
    const current = project();
    const selectedEffect = current.cameraEffect || 'none';
    effectSelect.replaceChildren(new Option('Без эффекта', 'none'));
    for (const key of availableEffects) effectSelect.add(new Option(labels[key] || key.replaceAll('_', ' '), key));
    if (selectedEffect !== 'none' && !availableEffects.includes(selectedEffect))
      effectSelect.add(new Option(`${labels[selectedEffect] || selectedEffect} · недоступен`, selectedEffect));
    effectSelect.value = selectedEffect;
    effectSelect.disabled = !capabilitiesKnown || availableEffects.length === 0;

    const images = (current.references || []).filter((ref) => ref.kind === 'image' && ref.role === 'Кадр сцены');
    const guides = Array.isArray(current.guideFrames) ? current.guideFrames
      : images.slice(0, 1).map((ref) => ({ asset_id: ref.id, frame_idx: 0 }));
    guideList.replaceChildren();
    if (!images.length) {
      const empty = document.createElement('small');
      empty.textContent = 'Добавь фото с ролью «Кадр сцены»';
      guideList.append(empty);
    }
    for (const ref of images) {
      const guide = guides.find((item) => item.asset_id === ref.id);
      const row = document.createElement('div');
      row.className = 'official-guide-row';
      const enabled = document.createElement('input');
      enabled.type = 'checkbox'; enabled.checked = Boolean(guide);
      enabled.setAttribute('aria-label', `Использовать ${ref.name} как ключевой кадр`);
      const title = document.createElement('span');
      title.textContent = `<Picture ${ref.number}> · ${ref.name}`;
      const frame = document.createElement('input');
      frame.type = 'number'; frame.step = '1'; frame.min = '-362';
      frame.max = '361'; frame.value = String(guide?.frame_idx ?? 0); frame.disabled = !guide;
      frame.setAttribute('aria-label', `Позиция ключевого кадра ${ref.name}, −1 — последний`);
      const unit = document.createElement('small'); unit.textContent = 'кадр · −1 = конец';
      const save = () => {
        const existing = Array.isArray(current.guideFrames) ? [...current.guideFrames]
          : images.slice(0, 1).map((item) => ({ asset_id: item.id, frame_idx: 0 }));
        current.guideFrames = existing.filter((item) => item.asset_id !== ref.id);
        if (enabled.checked) current.guideFrames.push({ asset_id: ref.id, frame_idx: Number(frame.value) });
        onChange(); render();
      };
      enabled.addEventListener('change', save);
      frame.addEventListener('change', save);
      row.append(enabled, title, frame, unit); guideList.append(row);
    }
  }

  effectSelect.addEventListener('change', () => { project().cameraEffect = effectSelect.value; onChange(); });
  async function refreshCapabilities() {
    status.textContent = 'Проверяю GPU…';
    try {
      const response = await fetch('/api/studio/capabilities');
      if (response.status === 404) {
        availableEffects = []; capabilitiesKnown = false;
        status.textContent = 'Mac-сервис ещё работает со старым API. Эффекты появятся после его перезапуска.';
        render(); return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const caps = await response.json();
      availableEffects = Array.isArray(caps.camera_effects_v1?.available) ? caps.camera_effects_v1.available : [];
      capabilitiesKnown = Boolean(caps.gpu_reachable);
      status.textContent = capabilitiesKnown
        ? `На GPU доступно эффектов: ${availableEffects.length}. Продолжение дубля: ${caps.continuation_v1 ? 'доступно' : 'недоступно'}.`
        : 'GPU отключён. Эффекты станут доступны после подключения сервера.';
    } catch {
      availableEffects = []; capabilitiesKnown = false;
      status.textContent = 'Не удалось проверить GPU. Эффекты пока недоступны.';
    }
    render();
  }
  wrap.querySelector('.official-refresh').addEventListener('click', refreshCapabilities);
  render();
  return { render, refreshCapabilities };
}
