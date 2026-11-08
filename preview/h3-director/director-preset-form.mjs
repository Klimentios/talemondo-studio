// «Свой пресет» form: per-category fields from app/director_schema.json,
// photo → parameters, palette extraction, saving the preset with its photo.
// Wired from app.mjs via installDirectorPresetForm(ctx).
import { DIRECTOR_FIELDS, directorFieldLabel, normalizeDirectorParameters } from './director-config.mjs?v=20261007-camera-options-5';
import { formatParameters, parametersToPrompt } from './director-labels.mjs?v=20261007-camera-options-5';
import { analyzePhotoFile, PHOTO_CATEGORIES } from './photo-analyze.mjs';
import { opticsFieldNote } from './optics-display.mjs?v=20261004-contract-1';

const $ = (selector) => document.querySelector(selector);

// What the photo does per category, shown under the file input.
const PHOTO_HINT = {
  ru: {
    lighting: 'Загрузи фото со светом, который нужен: источник, направление, жёсткость, контраст и температура заполнятся сами. Фото уйдёт в H3 как референс освещения.',
    color: 'Палитра и цветовой баланс извлекаются из фото. Фото уйдёт в H3 как референс цвета.',
    focus: 'По фото оценивается глубина резкости (резкость центра против краёв).',
    camera: 'По фото оцениваются контраст, зерно и детализация.',
    style: 'По фото оцениваются контраст и фактура. Фото уйдёт в H3 как референс стиля.',
    other: 'Фото сохранится как иллюстрация на карточке. Для этого раздела параметры по одному кадру не определяются — выставь их вручную.',
  },
  en: {
    lighting: 'Upload a photo with the lighting you want: source, direction, softness, contrast and temperature are filled in. The photo is sent to H3 as a lighting reference.',
    color: 'Palette and color balance are extracted from the photo. The photo is sent to H3 as a color reference.',
    focus: 'Depth of field is estimated from the photo (center vs. edge sharpness).',
    camera: 'Contrast, grain and detail are estimated from the photo.',
    style: 'Contrast and texture are estimated from the photo. The photo is sent to H3 as a style reference.',
    other: 'The photo is saved as the card image. For this category parameters cannot be measured from a still — set them manually.',
  },
};

export function installDirectorPresetForm(ctx) {
  // ctx: { category(), locale(), choice(), apiReady(), toast(msg), registerAsset(asset), savePreset(preset) }
  let palette = [];
  let source = null;
  const en = () => ctx.locale() === 'en';
  const auto = () => (en() ? 'Auto' : 'Авто');
  const form = $('#directorCustomForm');

  function setControlValue(key, value) {
    const control = form.elements[`parameter_${key}`];
    if (!control) return false;
    control.value = typeof value === 'boolean' ? String(value) : Array.isArray(value) ? value.join(', ') : String(value);
    control.closest('label')?.classList.add('from-photo');
    return true;
  }

  function renderSwatches() {
    const host = $('#directorColorSwatches'); host.replaceChildren();
    host.hidden = ctx.category() !== 'color' || palette.length === 0;
    for (const color of palette) {
      const swatch = document.createElement('span'); swatch.style.backgroundColor = color; swatch.title = color; host.append(swatch);
    }
  }

  function renderFields(parameters = {}) {
    const category = ctx.category();
    const host = $('#directorCustomFields'); host.replaceChildren();
    for (const [key, , kind] of DIRECTOR_FIELDS[category] || []) {
      const row = document.createElement('label');
      row.textContent = directorFieldLabel(category, key, undefined, ctx.locale()) + opticsFieldNote(category, key, ctx.locale());
      let control;
      if (Array.isArray(kind) || kind === 'boolean') {
        control = document.createElement('select');
        control.add(new Option(auto(), ''));
        if (kind === 'boolean') { control.add(new Option(en() ? 'Yes' : 'Да', 'true')); control.add(new Option(en() ? 'No' : 'Нет', 'false')); }
        else for (const value of kind) control.add(new Option(directorFieldLabel(category, key, value, ctx.locale()), value));
        const current = parameters[key];
        control.value = current === undefined ? '' : String(current);
      } else if (typeof kind === 'string') {
        control = document.createElement('input'); control.type = 'text';
        control.placeholder = kind === 'swatches' ? '#13202d, #ad754a, …' : kind === 'palette_tags' ? 'cyan, magenta, near_black' : (en() ? 'e.g. deep navy' : 'например, deep navy');
        if (kind === 'short_text') { control.maxLength = 60; control.pattern = '[A-Za-zА-Яа-яЁё0-9 \\-]+'; }
        control.value = Array.isArray(parameters[key]) ? parameters[key].join(', ') : (parameters[key] ?? '');
        if (kind === 'swatches') control.addEventListener('input', () => {
          palette = control.value.split(/[\s,;]+/).filter((value) => /^#[0-9a-fA-F]{6}$/.test(value)).slice(0, 8);
          renderSwatches();
        });
      } else {
        control = document.createElement('input'); control.type = 'number';
        Object.assign(control, { min: String(kind.min), max: String(kind.max), step: String(kind.step), placeholder: auto() });
        control.value = parameters[key] ?? '';
      }
      control.name = `parameter_${key}`;
      control.addEventListener('change', () => row.classList.remove('from-photo'));
      row.append(control); host.append(row);
    }
    palette = Array.isArray(parameters.swatches) ? [...parameters.swatches] : [];
    renderSwatches();
    const field = $('#directorColorImageField');
    field.hidden = false;
    field.firstChild.nodeValue = en() ? 'Reference photo' : 'Фото-референс';
    field.querySelector('span').textContent = PHOTO_HINT[en() ? 'en' : 'ru'][PHOTO_CATEGORIES.has(category) ? category : 'other'];
    $('#directorPhotoResult').textContent = '';
  }

  function open(copySelected = false) {
    form.reset();
    const chosen = copySelected ? ctx.choice() : null;
    source = chosen?.category === ctx.category() ? chosen : null;
    form.elements.name.value = chosen ? `${chosen.name} · ${en() ? 'mine' : 'мой'}` : '';
    form.elements.prompt.value = chosen?.prompt || '';
    renderFields(chosen?.parameters || {});
    form.hidden = false;
    form.scrollIntoView({ block: 'nearest' });
    form.elements.name.focus();
  }

  function readParameters() {
    const category = ctx.category();
    const raw = {};
    for (const [key, , kind] of DIRECTOR_FIELDS[category] || []) {
      const value = form.elements[`parameter_${key}`]?.value?.trim();
      if (!value) continue;
      raw[key] = kind === 'boolean' ? value === 'true'
        : kind === 'swatches' ? value.split(/[\s,;]+/).filter(Boolean)
          : kind === 'palette_tags' ? value.split(/[\s,;]+/).filter(Boolean)
            : typeof kind === 'object' && !Array.isArray(kind) ? Number(value) : value;
    }
    if (raw.swatches && raw.swatches.some((value) => !/^#[0-9a-fA-F]{6}$/.test(value)))
      throw new Error(en() ? 'Palette: up to 8 colors as #RRGGBB' : 'Палитра: до 8 цветов в формате #RRGGBB');
    return normalizeDirectorParameters(category, raw);
  }

  async function uploadPhoto(file) {
    const data = new FormData(); data.append('file', file, file.name);
    const role = { lighting: 'Свет', color: 'Цвет' }[ctx.category()] || 'Стиль';
    const response = await fetch(`/api/assets?role=${encodeURIComponent(role)}`, { method: 'POST', body: data });
    const asset = await response.json();
    if (!response.ok) throw new Error(asset.detail || `HTTP ${response.status}`);
    ctx.registerAsset(asset);
    return { imageAssetId: asset.asset_id, image: `/api/assets/${encodeURIComponent(asset.asset_id)}/file` };
  }

  $('#directorColorImage').addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    const category = ctx.category();
    if (!file || !PHOTO_CATEGORIES.has(category)) return;
    try {
      const measured = await analyzePhotoFile(category, file);
      for (const [key, value] of Object.entries(measured)) setControlValue(key, value);
      if (measured.swatches) { palette = measured.swatches; renderSwatches(); }
      if (!form.elements.name.value.trim()) form.elements.name.value = file.name.replace(/\.[^.]+$/, '').slice(0, 60);
      $('#directorPhotoResult').textContent = (en() ? 'Filled from photo: ' : 'Заполнено по фото: ')
        + formatParameters(category, measured, ctx.locale()) + (en() ? '. Check and adjust.' : '. Проверь и поправь.');
    } catch (error) { ctx.toast(`${en() ? 'Could not read the photo' : 'Не удалось разобрать фото'}: ${error.message}`); }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const category = ctx.category();
    const name = form.elements.name.value.trim();
    if (!name) return;
    let parameters;
    try { parameters = readParameters(); } catch (error) { ctx.toast(error.message); return; }
    if (!Object.keys(parameters).length) { ctx.toast(en() ? 'Set at least one parameter or upload a photo' : 'Укажи хотя бы один параметр или загрузи фото'); return; }
    // Description is optional: an empty one is generated from the parameters (English, for H3).
    const prompt = form.elements.prompt.value.trim() || parametersToPrompt(category, parameters);
    let media = source?.image ? { image: source.image, imageAssetId: source.imageAssetId } : {};
    const file = $('#directorColorImage').files?.[0];
    if (file) {
      if (!ctx.apiReady()) { ctx.toast(en() ? 'Wait for the local Studio connection to save the photo' : 'Чтобы сохранить фото, дождись подключения локальной Studio'); return; }
      try { media = await uploadPhoto(file); } catch (error) { ctx.toast(`${en() ? 'Could not save the photo' : 'Не удалось сохранить фото'}: ${error.message}`); return; }
    }
    ctx.savePreset({
      id: `custom.${crypto.randomUUID()}`, category, name,
      description: form.elements.prompt.value.trim(), // empty → the card shows the parameter summary
      prompt, parameters, ...media, tags: [name.toLocaleLowerCase()], tone: 'violet', source: 'custom',
    });
    form.reset(); form.hidden = true;
  });

  $('#newDirectorPreset').addEventListener('click', () => open(false));
  $('#editDirectorPreset').addEventListener('click', () => open(true));
  $('#cancelDirectorPreset').addEventListener('click', () => { form.hidden = true; form.reset(); });

  return {
    open,
    // Re-render labels after a language switch without losing entered values.
    relocalize() { if (!form.hidden) { const values = (() => { try { return readParameters(); } catch { return {}; } })(); renderFields(values); } },
  };
}
