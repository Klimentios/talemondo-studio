// One readable message for any failed Studio API answer. FastAPI sends `detail` as a string,
// as a list of validation errors ({msg, loc}) or as an object; never show «[object Object]».
export function errorText(payload, status) {
  const detail = payload?.detail ?? payload?.error ?? payload?.message;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => {
      if (typeof item === 'string') return item;
      const field = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== 'body').join('.') : '';
      return [field, item?.msg || item?.message].filter(Boolean).join(': ') || JSON.stringify(item);
    }).join('; ');
  }
  if (detail && typeof detail === 'object') return detail.message || detail.msg || JSON.stringify(detail);
  if (status === 404) return 'Функция недоступна: обнови Studio или GPU-сервер';
  if (status === 502 || status === 503 || status === 504) return 'GPU-сервер не отвечает — проверь, что он включён';
  return `Ошибка ${status || 'сети'}`;
}

// fetch → JSON body; throws Error(errorText) for non-2xx answers and non-JSON error pages.
export async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorText(payload, response.status));
  return payload;
}

// A failed job's stored error can be a whole Python traceback from the GPU server. Show one readable sentence;
// the raw text stays available as a tooltip (`title`) for debugging.
const KNOWN_JOB_ERRORS = [
  [/No module named ['"]?torch/i, 'Модель на GPU-сервере установлена не полностью (нет PyTorch). После обновления сервера нажми «Повторить».'],
  [/No module named ['"]?([\w.]+)/i, (m) => `На GPU-сервере не хватает модуля «${m[1]}». После обновления сервера нажми «Повторить».`],
  [/CUDA out of memory|OutOfMemoryError/i, 'Не хватило видеопамяти GPU. Уменьши качество или длительность и повтори.'],
  [/Connection refused|ECONNREFUSED|Name or service not known|timed? ?out/i, 'GPU-сервер не ответил. Проверь, что он включён, и повтори.'],
  [/cancel/i, 'Задание отменено.'],
];
export function jobErrorText(raw) {
  const text = String(raw || '').trim();
  if (!text) return '';
  for (const [pattern, message] of KNOWN_JOB_ERRORS) {
    const match = text.match(pattern);
    if (match) return typeof message === 'function' ? message(match) : message;
  }
  if (!/Traceback|File "\//.test(text)) return text.length > 240 ? `${text.slice(0, 237)}…` : text;
  // Traceback: the last «SomeError: message» is the useful part.
  const last = text.split(/\n|(?<=\)) (?=[A-Z]\w+(?:Error|Exception):)/).map((line) => line.trim()).filter(Boolean).pop() || text;
  const clean = last.replace(/^.*?\b([A-Z]\w+(?:Error|Exception)):\s*/, '$1: ');
  return clean.length > 240 ? `${clean.slice(0, 237)}…` : clean;
}
