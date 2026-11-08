// Video formats (aspect ratios) for every Studio screen. One list: the GPU API's /api/capabilities aspect_ratios
// (app/contract.py ASPECTS), passed on by the Mac's /api/studio/capabilities. Selects are filled from here; the Mac
// store and worker check the same list again before a job reaches the GPU.

export const DEFAULT_ASPECT = '9:16'; // STUDIO franchises: vertical TikTok
// Until the live list arrives: what every GPU API version accepts.
const BASE = Object.freeze(['9:16', '16:9']);
let current = [...BASE];
let loading = null;
const listeners = new Set();

/** Width / height of a «W:H» format, or null for anything else. */
export function aspectRatio(aspect) {
  const match = /^(\d{1,3}):(\d{1,3})$/.exec(String(aspect ?? ''));
  return match && Number(match[1]) > 0 && Number(match[2]) > 0 ? Number(match[1]) / Number(match[2]) : null;
}

export function aspectLabel(aspect, en = false) {
  const ratio = aspectRatio(aspect);
  if (!ratio) return String(aspect ?? '');
  const kind = ratio < 1 ? (en ? 'vertical' : 'верт.') : ratio > 1 ? (en ? 'horizontal' : 'гориз.') : (en ? 'square' : 'квадрат');
  return `${aspect} ${kind}`; // short: the format select is one of four in a row
}

/** The formats offered now, the Studio default first. */
export function aspects() { return [...current]; }

export function setAspects(list) {
  const valid = [...new Set((Array.isArray(list) ? list : []).filter((item) => aspectRatio(item)))];
  if (!valid.length) return aspects();
  current = valid.includes(DEFAULT_ASPECT) ? [DEFAULT_ASPECT, ...valid.filter((item) => item !== DEFAULT_ASPECT)] : valid;
  for (const listener of listeners) listener(aspects());
  return aspects();
}

/** Fetch the list once per page (later calls share it); an old Mac service without the field keeps the base pair. */
export function loadAspects(fetcher = globalThis.fetch) {
  loading ||= (async () => {
    try {
      const response = await fetcher('/api/studio/capabilities', { cache: 'no-store' });
      if (response.ok) setAspects((await response.json()).aspect_ratios);
    } catch { /* offline page: base pair */ }
    return aspects();
  })();
  return loading;
}

export function onAspects(listener) { listeners.add(listener); return () => listeners.delete(listener); }

/** The offered format closest to a picture of width × height (agent recipes, sources). */
export function nearestAspect(width, height, list = current) {
  const target = Math.log(Number(width) / Number(height));
  if (!Number.isFinite(target)) return null;
  return list.reduce((best, item) => (Math.abs(Math.log(aspectRatio(item)) - target)
    < Math.abs(Math.log(aspectRatio(best)) - target) ? item : best));
}

/** [value, label] pairs for a select; a saved value the GPU no longer lists stays visible instead of jumping away. */
export function aspectOptions(selected = '', en = false) {
  const values = aspects();
  if (selected && !values.includes(selected) && aspectRatio(selected))
    values.push(selected);
  return values.map((value) => [value, current.includes(value) ? aspectLabel(value, en)
    : `${aspectLabel(value, en)} · ${en ? 'not on GPU' : 'нет на GPU'}`]);
}

/** Set a format select to `aspect` (an agent draft, a saved scene) even before the live list arrived. */
export function selectAspect(select, aspect) {
  if (!select || !aspectRatio(aspect)) return false;
  if (![...select.options].some((option) => option.value === aspect))
    select.append(Object.assign(select.ownerDocument.createElement('option'), { value: aspect, textContent: aspectLabel(aspect) }));
  select.value = aspect;
  return true;
}

/** Fill a <select> with the formats and keep it in step with the live list; keeps the chosen value. */
export function bindAspectSelect(select, value = () => select.value) {
  const fill = () => {
    const chosen = value() || select.value || DEFAULT_ASPECT;
    select.replaceChildren(...aspectOptions(chosen).map(([item, label]) => {
      const option = select.ownerDocument.createElement('option');
      option.value = item; option.textContent = label; option.selected = item === chosen;
      return option;
    }));
    select.value = chosen;
  };
  fill();
  onAspects(fill);
  void loadAspects();
  return fill;
}
