// Director fields come from ONE file: app/director_schema.json.
// The server validates with the same file (app/director_config.py).
// To add or change a field/option, edit only that JSON.
async function loadDirectorSchema() {
  if (typeof window === 'undefined') {
    const { readFile } = await import('node:fs/promises');
    return JSON.parse(await readFile(new URL('../../app/director_schema.json', import.meta.url), 'utf8'));
  }
  const response = await fetch('/director-schema.json', { cache: 'no-cache' }).catch(() => null);
  if (!response?.ok) {
    // Old Studio process without the route: say exactly what to do instead of a blank page.
    const banner = document.createElement('div');
    banner.style.cssText = 'position:fixed;inset:auto 16px 16px 16px;z-index:9999;padding:14px 16px;border-radius:10px;background:#5a1f2b;color:#fff;font:13px/1.45 system-ui';
    banner.textContent = 'Studio обновлена — перезапусти локальный сервер: launchctl kickstart -k gui/$(id -u)/com.minimax.h3-mac-studio, затем обнови страницу.';
    document.body.append(banner);
    throw new Error(`director-schema.json: HTTP ${response?.status ?? 'network'} — перезапусти локальную Studio`);
  }
  return response.json();
}

export const DIRECTOR_SCHEMA = await loadDirectorSchema();

export function directorFieldOptions(category, key) {
  const field = DIRECTOR_SCHEMA.categories[category]?.find((entry) => entry.key === key);
  if (!field) return [];
  if (Array.isArray(field.options)) return field.options;
  const [refCategory, refKey] = String(field.options_ref || '').split('.');
  return DIRECTOR_SCHEMA.categories[refCategory]?.find((entry) => entry.key === refKey)?.options || [];
}

// Legacy tuple shape [key, label, kind] used across the UI.
// kind: array of values | 'boolean' | 'swatches' | 'palette_tags' | 'short_text' | {min,max,step}
export const DIRECTOR_FIELDS = Object.fromEntries(Object.entries(DIRECTOR_SCHEMA.categories || {}).map(([category, fields]) => [
  category,
  (Array.isArray(fields) ? fields : []).map((field) => [field.key, field.label.ru,
    field.type === 'enum' ? (directorFieldOptions(category, field.key) || []).map((option) => option.value)
      : field.type === 'number' ? { min: field.min, max: field.max, step: field.step }
        : field.type]),
]));

export function directorField(category, key) {
  return DIRECTOR_SCHEMA.categories[category]?.find((field) => field.key === key) || null;
}

/** Field-aware label: directorFieldLabel('lighting', 'source', 'sun', 'ru') → 'Солнце'. */
export function directorFieldLabel(category, key, value, locale = 'ru') {
  const lang = String(locale).toLowerCase().startsWith('en') ? 'en' : 'ru';
  const field = directorField(category, key);
  if (value === undefined) return field?.label[lang] || key.replaceAll('_', ' ');
  return directorFieldOptions(category, key).find((option) => option.value === value)?.label[lang] || String(value).replaceAll('_', ' ');
}

const OPTION_LABELS = {
  digital:'Цифровое кино', film35:'Плёнка 35 мм', film16:'Плёнка 16 мм', documentary:'Документальный', vintage:'Винтажный',
  natural:'Естественный', soft:'Мягкий', low:'Низкий', medium:'Средний', high:'Высокий', crisp:'Чёткий', fine:'Мелкое', subtle:'Деликатное', smooth:'Плавная',
  'full-frame':'Полный кадр', 'large format':'Большой формат', super_35:'Super 35',
  wide:'Общий / широкий', full:'В полный рост', close:'Крупный', extreme_close:'Деталь', over_shoulder:'Через плечо', two_shot:'Двое в кадре', point_of_view:'От первого лица', insert:'Врезка / предмет',
  eye:'На уровне глаз', overhead:'Сверху', dutch:'Наклон горизонта',
  standard:'Стандартный', portrait:'Портретный', telephoto:'Телеобъектив', macro:'Макро', anamorphic:'Анаморфный',
  static:'Статично', dolly:'Наезд / отъезд', tracking:'Следование', truck:'Боковой ход', orbit:'Орбита', pan:'Панорама', tilt:'Наклон', handheld:'С рук', crane:'Кран',
  forward:'Вперёд', backward:'Назад', side:'Вбок', left:'Влево', right:'Вправо', up:'Вверх', down:'Вниз', none:'Нет', slow:'Медленно', fast:'Быстро',
  subject:'Герой', eyes:'Глаза', object:'Предмет', shallow:'Малая', deep:'Глубокая', rack:'Перевод фокуса', locked:'Зафиксировать', foreground:'Передний план', background:'Задний план',
  daylight:'Дневной', sun:'Солнце', window:'Окно', practical:'Практический', backlight:'Контровой', neon:'Неон', overcast:'Пасмурный', front:'Спереди', back:'Сзади', top:'Сверху',
  hard:'Жёсткий', warm:'Тёплый', neutral:'Нейтральный', cool:'Холодный', split:'Раздельный', controlled:'Контролируемая', moderate:'Умеренная',
  photoreal:'Фотореализм', commercial:'Рекламный', film:'Плёнка', animation3d:'3D-анимация', animation2d:'2D-анимация', noir:'Нуар', scifi:'Фантастика',
};
export const directorOptionLabel = (value) => OPTION_LABELS[value] || value.replaceAll('_', ' ');

export function directorParameterSummary(category, parameters = {}, limit = Infinity) {
  const labels = new Map((DIRECTOR_FIELDS[category] || []).map(([key, label]) => [key, label]));
  return Object.entries(parameters)
    .filter(([key]) => key !== 'swatches' && labels.has(key))
    .slice(0, limit)
    .map(([key, value]) => `${labels.get(key)}: ${Array.isArray(value) ? value.map(directorOptionLabel).join(' · ') : typeof value === 'boolean' ? value ? 'Да' : 'Нет' : typeof value === 'string' ? directorOptionLabel(value) : value}`)
    .join(' · ');
}

export function normalizeDirectorParameters(category, parameters = {}) {
  const out = {};
  for (const [key, , kind] of DIRECTOR_FIELDS[category] || []) {
    const raw = parameters[key];
    if (raw === undefined || raw === null || raw === '') continue;
    if (Array.isArray(kind)) {
      if (kind.includes(raw)) out[key] = raw;
    } else if (kind === 'boolean') {
      if (typeof raw === 'boolean') out[key] = raw;
    } else if (kind === 'swatches') {
      if (Array.isArray(raw)) out[key] = raw.filter((v) => typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v)).slice(0, 8);
    } else if (kind === 'palette_tags') {
      const allowed = new Set(['cyan', 'magenta', 'near_black', 'neutral', 'warm_skin', 'earth', 'sky', 'charcoal', 'single_accent', 'amber', 'cream', 'muted_brown', 'limited']);
      if (Array.isArray(raw)) out[key] = raw.filter((v) => allowed.has(v)).slice(0, 4);
    } else if (kind === 'short_text') {
      if (typeof raw === 'string' && raw.length <= 60 && /^[A-Za-zА-Яа-яЁё0-9 -]+$/.test(raw) && raw.trim() === raw) out[key] = raw;
    } else {
      const value = Number(raw);
      if (Number.isFinite(value) && value >= kind.min && value <= kind.max) out[key] = value;
    }
  }
  return out;
}

export function directorConfigForState(state = {}, custom = [], builtins = [], cameraOverrides = {}) {
  const presets = [...builtins, ...custom];
  const config = {};
  for (const category of Object.keys(DIRECTOR_FIELDS)) {
    const preset = presets.find((item) => item.id === state[category] && item.category === category);
    if (preset) {
      const parameters = normalizeDirectorParameters(category, preset.parameters);
      if (category === 'movement' && ['slow', 'medium', 'fast'].includes(cameraOverrides.speed))
        parameters.speed = cameraOverrides.speed;
      if (category === 'movement' && DIRECTOR_FIELDS.movement.some(([key]) => key === 'end_framing') &&
          directorFieldOptions('movement', 'end_framing').some((option) => option.value === cameraOverrides.end_framing))
        parameters.end_framing = cameraOverrides.end_framing;
      config[category] = { id: preset.id, parameters };
    }
  }
  return config;
}
