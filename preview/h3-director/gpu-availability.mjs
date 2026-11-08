// Connection failure is not evidence that a model or node is missing.
export function featureAvailability(health, available) {
  if (!health) return { state: 'unknown', label: 'нет данных', message: 'Не удалось проверить GPU. Проверь локальную Studio и обнови страницу.' };
  if (!health.gpu_configured) return { state: 'unconfigured', label: 'GPU не подключён', message: 'GPU-сервер не подключён. Открой «GPU и модели».' };
  if (!health.gpu_reachable) return { state: 'offline', label: 'GPU недоступен', message: 'GPU сейчас недоступен. Проверь сервер и туннель в «GPU и модели».' };
  if (!available) return { state: 'unsupported', label: 'не установлено', message: 'GPU доступен, но не объявляет поддержку этой функции. Проверь установленные модели и узлы.' };
  return { state: 'ready', label: 'готово', message: '' };
}
