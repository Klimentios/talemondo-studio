// Match complete franchise names (or their explicitly separated titles); never guess from one shared hero.
const normalize = (text) => String(text || '').toLocaleLowerCase().replace(/[&+]/g, ' и ').replace(/\band\b/g, ' и ').replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
const transliterate = (text) => [...normalize(text)].map((char) => ({ а:'a', б:'b', в:'v', г:'g', д:'d', е:'e', ё:'e', ж:'zh', з:'z', и:'i', й:'i', к:'k', л:'l', м:'m', н:'n', о:'o', п:'p', р:'r', с:'s', т:'t', у:'u', ф:'f', х:'h', ц:'ts', ч:'ch', ш:'sh', щ:'shch', ы:'y', э:'e', ю:'yu', я:'ya', ь:'', ъ:'' }[char] ?? char)).join('');
export function resolveFranchise(text, franchises) {
  const haystack = ` ${transliterate(text)} `;
  const matches = (franchises || []).filter(({ name }) => String(name || '').split(/[·|]/).some((part) => {
    const name = transliterate(part);
    return name.length >= 3 && haystack.includes(` ${name} `);
  }));
  return matches.length === 1 ? matches[0] : null;
}

export function suggestedDeliverable(text) {
  const words = normalize(text);
  return /(?:сдела|созда|сгенериру|сними|собери|произвед|снять|создать|сделать)/u.test(words) && /(?:ролик|мульт|фильм|видео)/u.test(words) ? 'film' : 'task';
}

export function productionIds(run) {
  const found = { job: new Set(), scene: new Set(), render: new Set() };
  const add = (kind, value) => (Array.isArray(value) ? value : value ? [value] : []).forEach((id) => { if (typeof id === 'string' && id) found[kind].add(id); });
  const take = (item = {}) => {
    add('job', item.job_ids || item.job_id);
    add('scene', item.scene_ids || item.scene_id || item.scene); add('render', item.render_ids || item.render_id);
  };
  take(run?.wait); for (const step of run?.steps || []) take(step.artifacts);
  return Object.fromEntries(Object.entries(found).map(([kind, ids]) => [kind, [...ids]]));
}
export function jobStatus(job) {
  const labels = { queued_local: 'В очереди на Mac', uploading: 'Загрузка на GPU', queued_gpu: 'В очереди GPU', running: 'Генерация', downloading: 'Загрузка результата на Mac', completed: 'Готово технически · ожидает проверки', failed: 'Ошибка', cancelled: 'Отменено', interrupted: 'Прервано', needs_reconciliation: 'Нужна сверка задания', stopping: 'Останавливается', missing: 'Состояние недоступно' };
  const label = labels[job?.state] || job?.state || 'Состояние недоступно';
  const value = job?.sampling_value, max = job?.sampling_max;
  return Number.isFinite(value) && Number.isFinite(max) && max > 0 && value >= 0 && value <= max && job.state === 'running' ? `${label} · шаг ${value}/${max}` : label;
}
