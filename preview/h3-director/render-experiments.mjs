// Optional settings stay out of baseline payloads and are checked again by worker/GPU.
export function renderSettings(project) {
  const profile = project.profile || 'bf16';
  const value = project.renderOptions || {};
  const options = {};
  if (value.adapter && value.adapter !== 'none') {
    options.adapter = value.adapter;
    options.strength = Number(value.strength ?? (value.adapter === 'wushu_r2v_v7' ? 0.5 : 1));
    if (!Number.isFinite(options.strength) || options.strength <= 0 || options.strength > 1.2)
      throw new Error('Сила экспериментальной LoRA: больше 0 и до 1,2');
  }
  if (value.attention && value.attention !== 'dense') options.attention = value.attention;
  if (value.encoder_device && value.encoder_device !== 'default') options.encoder_device = value.encoder_device;
  return { ...(profile !== 'bf16' ? { profile } : {}),
    ...(Object.keys(options).length ? { render_options: options } : {}) };
}

export function restoreRenderSettings(project, settings) {
  // Replaying a baseline job must also clear the previous experiment's state.
  project.profile = settings.profile || 'bf16';
  project.renderOptions = structuredClone(settings.render_options || {});
}

let catalog = null;
export function renderProfileLabel(profile = 'bf16') {
  return catalog?.profiles?.[profile]?.label || (profile === 'bf16' ? 'BF16' : profile);
}

export function installRenderExperiments({ getProject, onChange, request }) {
  const host = document.querySelector('.settings-section') || document.querySelector('#createControls .controls-content');
  if (!host) return { render() {}, async refresh() {} };
  const panel = document.createElement('details');
  panel.className = 'output-details';
  panel.innerHTML = `<summary>Эксперименты · качество и скорость</summary>
    <div class="output-details-fields">
      <label>Модель DiT<select data-render="profile"><option value="bf16">BF16 · исходная модель</option></select></label>
      <label>Дополнительная LoRA<select data-render="adapter"><option value="none">Без экспериментальной LoRA</option></select></label>
      <label>Сила LoRA<input data-render="strength" type="number" min="0.05" max="1.2" step="0.05" value="1"></label>
      <label>Внимание<select data-render="attention"><option value="dense">Обычное</option><option value="sol-attn">SOL · эксперимент</option></select></label>
      <label>BF16-энкодер<select data-render="encoder_device"><option value="default">Автоматически · GPU/RAM</option><option value="cpu">CPU · меньше VRAM, может быть медленнее</option></select></label>
    </div><p class="execution-summary" data-render-status role="status"></p>
    <p class="execution-summary">Кэш сохраняет выбранный SAFE / FAST / CUSTOM. Новые модели ещё не проверены на ваших героях. ORB360 требует описания облёта; CardSpin вращает фотографию. CPU-энкодеру требуется большой запас RAM.</p>
    <button type="button" data-render-refresh>Проверить установленные варианты</button>
    <button type="button" data-render-reset>Вернуть исходный профиль</button>`;
  host.append(panel);
  const controls = Object.fromEntries([...panel.querySelectorAll('[data-render]')].map(e => [e.dataset.render,e]));
  function choices(select, entries, selected, baseline) {
    select.replaceChildren();
    if (baseline) select.add(new Option(baseline[1], baseline[0]));
    for (const [id,item] of Object.entries(entries || {})) {
      if (baseline && id === baseline[0]) continue;
      const option = new Option(item.label + (item.available ? '' : ' · не установлен'), id);
      option.disabled = !item.available && id !== selected;
      select.add(option);
    }
    if (selected && ![...select.options].some(o => o.value === selected)) {
      const option = new Option(`${selected} · недоступен`,selected); select.add(option);
    }
    select.value = selected;
  }
  function render() {
    const project = getProject(), options = project.renderOptions || {};
    choices(controls.profile, catalog?.profiles, project.profile || 'bf16',['bf16','BF16 · исходная модель']);
    choices(controls.adapter, catalog?.adapters, options.adapter || 'none',['none','Без экспериментальной LoRA']);
    controls.strength.value = options.strength ?? catalog?.adapters?.[options.adapter]?.strength ?? 1;
    controls.strength.disabled = !options.adapter || options.adapter === 'none';
    controls.attention.value = options.attention || 'dense';
    controls.attention.querySelector('[value="sol-attn"]').disabled = !catalog?.attention?.includes('sol-attn') && options.attention !== 'sol-attn';
    controls.encoder_device.value = options.encoder_device || 'default';
    controls.encoder_device.querySelector('[value="cpu"]').disabled = !catalog?.encoder_devices?.includes('cpu') && options.encoder_device !== 'cpu';
    panel.querySelector('[data-render-status]').textContent = catalog?.gpu_reachable
      ? 'Доступность подтверждена GPU. Скорость и качество определяются только сравнением готовых роликов.'
      : 'GPU недоступен или его API требует обновления. Каталог показан; установка весов не подтверждена.';
  }
  panel.addEventListener('change', event => {
    const key = event.target.dataset.render;
    if (!key) return;
    const project = getProject();
    if (key === 'profile') project.profile = event.target.value;
    else {
      project.renderOptions = { ...(project.renderOptions || {}), [key]: key === 'strength' ? Number(event.target.value) : event.target.value };
      if (key === 'adapter') {
        if (event.target.value === 'none') project.renderOptions = { ...project.renderOptions, strength:undefined };
        else project.renderOptions.strength = catalog?.adapters?.[event.target.value]?.strength ?? 1;
      }
    }
    render(); onChange();
  });
  panel.querySelector('[data-render-reset]').addEventListener('click', () => {
    restoreRenderSettings(getProject(), {}); render(); onChange();
  });
  async function refresh() {
    try { catalog = await request('/api/studio/render-options'); }
    catch { catalog = null; }
    render();
  }
  panel.querySelector('[data-render-refresh]').addEventListener('click', refresh);
  render();
  return { render, refresh };
}
