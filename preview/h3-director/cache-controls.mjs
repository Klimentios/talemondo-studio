// Cache configuration is sent as structured settings, never as prompt instructions.
export function cacheSettings(project) {
  if (project.cacheMode !== 'custom') return {};
  return { cache_config: { ...(project.cacheConfig || {
    threshold: 0.08, start_percent: 0.1, end_percent: 0.95,
    max_consecutive_hits: 2, temporal_guard: true,
  }) } };
}

export function bindCacheControls(select, getProject, onChange) {
  const panel = document.createElement('div');
  panel.className = 'output-details-fields';
  panel.innerHTML = `<label>Порог <input data-cache="threshold" type="number" min="0" max="1" step="0.005"></label>
    <label>Начало окна <input data-cache="start_percent" type="number" min="0" max="1" step="0.01"></label>
    <label>Конец окна <input data-cache="end_percent" type="number" min="0" max="1" step="0.01"></label>
    <label>Повторных шагов подряд <input data-cache="max_consecutive_hits" type="number" min="1" max="20" step="1"></label>
    <label><input data-cache="temporal_guard" type="checkbox"> Проверка временных срезов</label>`;
  const note = document.createElement('p');
  note.className = 'execution-summary';
  select.closest('.output-details-fields').after(panel, note);
  function refresh() {
    const project = getProject();
    panel.hidden = project.cacheMode !== 'custom';
    panel.style.display = panel.hidden ? 'none' : '';
    if (!panel.hidden) {
      const config = cacheSettings(project).cache_config;
      for (const input of panel.querySelectorAll('[data-cache]')) {
        if (input.type === 'checkbox') input.checked = config[input.dataset.cache];
        else input.value = config[input.dataset.cache];
      }
    }
    note.textContent = project.cacheMode === 'off' ? 'Кэш отключён.' : project.cacheMode === 'fast'
      ? 'FAST: порог 0,10 · окно 0,10–0,95 · до 2 повторов · без temporal guard.'
      : project.cacheMode === 'custom' ? 'CUSTOM — экспериментальные настройки. Temporal guard проверяет временные срезы, но не гарантирует сохранность лица и глаз.'
      : 'SAFE: порог 0,08 · окно 0,10–0,95 · до 2 повторов · temporal guard включён.';
  }
  select.addEventListener('change', refresh);
  panel.addEventListener('input', (event) => {
    const input = event.target;
    if (!input.dataset.cache) return;
    const project = getProject();
    project.cacheConfig = { ...cacheSettings(project).cache_config,
      [input.dataset.cache]: input.type === 'checkbox' ? input.checked : Number(input.value) };
    onChange();
  });
  refresh();
  return refresh;
}
