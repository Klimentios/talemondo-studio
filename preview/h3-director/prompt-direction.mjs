// Explicit prompt grammar uses the same preset catalogue as the cards.
import { DIRECTOR_CATEGORIES, DIRECTOR_PRESETS, validateDirectorState } from './director-presets.mjs?v=20261031-lighting-15-1';
import { DIRECTOR_SCHEMA, directorFieldOptions } from './director-config.mjs?v=20261007-camera-options-5';
import { resolvePromptCameraDefaults } from './prompt-camera-defaults.mjs?v=20261008-auto-camera-1';
import { NATIVE_CAMERA_MOVES } from './director-camera.mjs?v=20261008-auto-camera-1';

const normalize = (value) => String(value).trim().toLocaleLowerCase().replaceAll('ё', 'е').replace(/\s+/g, ' ');
const OPEN = '[Режиссура]';
const CLOSE = '[/Режиссура]';
const markers = /\[\/?Режиссура\]/gi;
const CAMERA_LABELS = new Map([
  ['movement', 'movement'], ['framing', 'shot'], ['скорость', 'speed'], ['speed', 'speed'],
  ['end', 'end'], ['конец', 'end'],
]);
function movementSpeed(value) {
  const normalized = normalize(value);
  return directorFieldOptions('movement', 'speed').find((option) =>
    [option.value, option.label.ru, option.label.en].some((label) => normalize(label) === normalized))?.value;
}
const END_FRAMING_OPTIONS = directorFieldOptions('shot', 'framing');

export function parsePromptDirection(prompt, custom = []) {
  const text = String(prompt ?? '');
  const found = [...text.matchAll(markers)];
  if (!found.length) return { present: false, state: {}, scene: text, errors: [] };
  if (found.length !== 2 || normalize(found[0][0]) !== normalize(OPEN) || normalize(found[1][0]) !== normalize(CLOSE))
    return { present: true, state: {}, scene: text, errors: ['Нужен один полный блок [Режиссура] … [/Режиссура].'] };
  const start = found[0].index, end = found[1].index + found[1][0].length;
  const body = text.slice(start + found[0][0].length, found[1].index);
  const state = {}, errors = [], seenPreset = new Set(), seenFreeform = new Set(), freeform = [];
  const camera = {};
  const presets = [...DIRECTOR_PRESETS, ...custom];
  for (const line of body.split(/\r?\n/).filter((line) => line.trim())) {
    const colon = line.indexOf(':');
    const label = colon < 0 ? line : line.slice(0, colon);
    const normalizedLabel = normalize(label);
    const cameraField = CAMERA_LABELS.get(normalizedLabel);
    const category = cameraField === 'movement' || cameraField === 'shot' ? cameraField :
      DIRECTOR_CATEGORIES.find(([key, title]) => [key, title].some((s) => normalize(s) === normalizedLabel))?.[0];
    if (cameraField === 'speed') {
      const speed = movementSpeed(line.slice(colon + 1));
      if (colon < 0 || !speed) errors.push(`Скорость камеры должна быть slow, medium или fast (медленно, средне, быстро).`);
      else if (Object.hasOwn(camera, 'speed')) errors.push('Поле «Speed» указано дважды.');
      else camera.speed = speed;
      continue;
    }
    if (cameraField === 'end') {
      const value = normalize(line.slice(colon + 1));
      const matches = presets.filter((preset) => preset.category === 'shot' &&
        [preset.id, preset.name, preset.titleEn].filter(Boolean).some((entry) => normalize(entry) === value));
      if (colon < 0 || !value) errors.push('Поле «End» требует ID конечного кадра из каталога.');
      else if (Object.hasOwn(camera, 'end_framing')) errors.push('Поле «End» указано дважды.');
      else if (matches.length > 1) errors.push('Конечный кадр задан неоднозначно; укажи ID пресета.');
      else if (matches.length === 1) {
        const framing = matches[0].parameters?.framing;
        if (!END_FRAMING_OPTIONS.some((option) => option.value === framing)) errors.push('У выбранного пресета кадра нет поддерживаемой конечной крупности.');
        else { camera.end_framing = framing; camera.endPreset = matches[0].id; }
      } else errors.push('Поле «End» принимает ID пресета кадра из каталога, например shot.close.');
      continue;
    }
    if (!category || colon < 0) { errors.push(`Неизвестная строка режиссуры: «${line.trim()}». Формат: Кадр: Крупный.`); continue; }
    const value = normalize(line.slice(colon + 1));
    if (['авто', 'auto'].includes(value)) {
      if (seenPreset.has(category)) errors.push(`Поле «${label.trim()}» указано дважды.`);
      seenPreset.add(category);
      continue;
    }
    const aliases = value.replace(/^медленный наезд$/, 'наезд').replace(/^крупный план$/, 'крупный');
    const matches = presets.filter((p) => p.category === category && [p.id, p.name, p.titleEn].filter(Boolean).some((s) => normalize(s) === aliases));
    if (matches.length > 1) errors.push(`«${label.trim()}» — неоднозначное название; укажи ID пресета.`);
    else if (matches.length === 1) {
      if (seenPreset.has(category)) errors.push(`Поле «${label.trim()}» указано дважды.`);
      seenPreset.add(category);
      state[category] = matches[0].id;
    }
    else if (!value) errors.push(`Поле «${label.trim()}» пустое.`);
    else {
      if (seenFreeform.has(category)) errors.push(`Авторское поле «${label.trim()}» указано дважды.`);
      seenFreeform.add(category);
      freeform.push(`${label.trim()}: ${line.slice(colon + 1).trim()}`);
    }
  }
  const scene = text.slice(0, start) + text.slice(end);
  const autoFilled = errors.length ? [] : resolvePromptCameraDefaults(state, camera, presets, seenFreeform);
  if (Object.hasOwn(camera, 'speed')) {
    const movementPreset = presets.find((preset) => preset.id === state.movement && preset.category === 'movement');
    if (!movementPreset) errors.push('Поле «Speed» требует выбранного движения камеры.');
    else if (movementPreset.parameters?.movement === 'static') errors.push('Поле «Speed» нельзя применить к статичной камере.');
  }
  if (Object.hasOwn(camera, 'end_framing')) {
    const movementPreset = presets.find((preset) => preset.id === state.movement && preset.category === 'movement');
    const framingPreset = presets.find((preset) => preset.id === state.shot && preset.category === 'shot');
    if (!movementPreset || !framingPreset) errors.push('Поле «End» требует выбранные Movement и начальный Framing.');
    else if (!NATIVE_CAMERA_MOVES.includes(movementPreset.parameters?.movement))
      errors.push('Поле «End» поддерживается только для движения с нативной траекторией камеры.');
  }
  return { present: true, state, freeform, camera, autoFilled, errors, start, end,
    scene: freeform.length ? `${freeform.join('\n')}\n\n${scene.trim()}` : scene };
}

export function writePromptDirection(prompt, state, custom = []) {
  const parsed = parsePromptDirection(prompt, custom);
  if (parsed.errors.length) throw new Error(parsed.errors.join(' '));
  const presets = [...DIRECTOR_PRESETS, ...custom];
  const lines = DIRECTOR_CATEGORIES.map(([key, title]) => {
    const preset = presets.find((p) => p.id === state[key] && p.category === key);
    // IDs are stable, including custom presets with duplicate names.
    return `${title}: ${preset?.id || 'Авто'}`;
  });
  const cameraLines = [parsed.camera?.speed ? `Speed: ${parsed.camera.speed}` : '',
    parsed.camera?.endPreset ? `End: ${parsed.camera.endPreset}` : '', ...(parsed.freeform || [])].filter(Boolean);
  const block = `${OPEN}\n${lines.join('\n')}${cameraLines.length ? `\n${cameraLines.join('\n')}` : ''}\n${CLOSE}`;
  return parsed.present ? String(prompt).slice(0, parsed.start) + block + String(prompt).slice(parsed.end) : `${block}\n\n${prompt || ''}`;
}

export function promptDirectionErrors(prompt, state, custom = [], mode) {
  const parsed = parsePromptDirection(prompt, custom);
  const errors = [...parsed.errors];
  if (parsed.present && !parsed.errors.length) {
    errors.push(...validateDirectorState(parsed.state, custom, mode));
    if (!parsed.scene.replace(/\[\/?Сцена\]/gi, '').trim()) errors.push('Добавь описание сцены за пределами блока режиссуры.');
    const presets = [...DIRECTOR_PRESETS, ...custom];
    const movement = presets.find((p) => p.id === parsed.state.movement)?.parameters?.movement;
    if (movement && movement !== 'static' && /камера\s*(?:[:—-]\s*)?(?:неподвижна|статична|зафиксирована)(?=[\s.,!]|$)/iu.test(parsed.scene))
      errors.push('Конфликт: в сцене камера неподвижна, а в блоке «Режиссура» выбрано движение.');
    for (const [key] of DIRECTOR_CATEGORIES) if ((parsed.state[key] || '') !== (state[key] || '')) {
      errors.push('Карточки и блок режиссуры расходятся. Исправь блок перед запуском.'); break;
    }
  }
  return errors;
}

export function installPromptDirectionHelp({ input, getState, getCustom, changed, toast }) {
  const details = document.createElement('details');
  details.className = 'prompt-direction-help';
  const summary = document.createElement('summary'); summary.textContent = 'Настройки из промпта · синтаксис';
  const description = document.createElement('p');
  description.textContent = 'Вставь один блок [Режиссура] … [/Режиссура]. Movement, Framing и End принимают ID пресетов. End меняет конечную крупность в camera_state; пропущенные начальный кадр и движение подставятся автоматически. Speed меняет настройку движения и масштаб траектории. Свободная формулировка остаётся инструкцией MiniMax. Ручной выбор карточки обновляет блок.';
  const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Вставить блок из карточек';
  button.addEventListener('click', () => {
    try { input.value = writePromptDirection(input.value, getState(), getCustom()); changed(); }
    catch (error) { toast(error.message); }
  });
  const author = document.createElement('button'); author.type = 'button'; author.textContent = 'Авторский промпт · скопировать контракт для чата';
  author.addEventListener('click', async () => {
    const catalogue = DIRECTOR_CATEGORIES.map(([key, title]) =>
      `${title}: Авто | ${[...DIRECTOR_PRESETS, ...getCustom()].filter((p) => p.category === key).map((p) => `${p.id} (${p.name})`).join(' | ')}`).join('\n');
    const fields = DIRECTOR_CATEGORIES.map(([key, title]) => `${title}: ${DIRECTOR_SCHEMA.categories[key].map((field) =>
      `${field.key} (${field.label.ru}): ${field.type === 'enum' ? directorFieldOptions(key, field.key).map((option) => option.value).join('/') : field.type === 'number' ? `${field.min}–${field.max}` : field.type}`).join('; ')}`).join('\n');
    const contract = `Составь для H3 Studio сцену и один блок [Режиссура] … [/Режиссура].\nДля камеры используй Movement: ID движения, Speed: slow|medium|fast, Framing: ID начального кадра, End: ID конечного кадра. Speed меняет конфиг движения и масштаб нативной траектории. End меняет конечную крупность в camera_state и director_config; Если Movement или Framing пропущены, Studio подберёт недостающие параметры автоматически. Используй только ID из каталога; не выдумывай значения. Сохрани сюжет, действия, тайминг, реплики и роли <Picture N>/<Video N>. Вне блока дай саму сцену.\n\nДействительные категории и ID:\n${catalogue}\n\nПоля конструктора собственного пресета (это справочник для понимания, а не синтаксис блока):\n${fields}\n\nМой замысел:\n`;
    try { await navigator.clipboard.writeText(contract); toast('Контракт для чата скопирован'); }
    catch { toast('Не удалось скопировать контракт'); }
  });
  const catalogue = document.createElement('pre');
  catalogue.style.cssText = 'white-space:pre-wrap;max-height:220px;overflow:auto;font-size:12px';
  // Rebuild on open so newly added custom presets are listed as well.
  details.addEventListener('toggle', () => {
    if (details.open) catalogue.textContent = DIRECTOR_CATEGORIES.map(([key, title]) => `${title}: Авто\n${[...DIRECTOR_PRESETS, ...getCustom()].filter((p) => p.category === key).map((p) => `  ${p.name} → ${p.id}`).join('\n')}`).join('\n\n');
  });
  const status = document.createElement('p'); status.setAttribute('role', 'status'); status.hidden = true;
  details.append(summary, description, button, author, catalogue);
  input.parentElement.append(status);
  // The catalogue is reference material, not part of the primary composer.
  // Keep its actions available with the other professional controls.
  const advanced = document.querySelector('#createControls .controls-content');
  (advanced || input.parentElement).append(details);
  return { status };
}
