// Isolated acting workflow. All media stays in the Mac asset store.
const $ = (selector) => document.querySelector(selector);

async function jsonResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`);
  return payload;
}

async function upload(file, role) {
  const body = new FormData();
  body.append('file', file, file.name);
  return jsonResponse(await fetch(`/api/assets?role=${encodeURIComponent(role)}`, { method: 'POST', body }));
}

export function installActing({ onAttach, onAttachVoice, onSettingsChange, voiceForOwner, references, owners, showToast }) {
  const panel = $('#acting');
  const grid = $('#directorGrid');
  const content = document.createElement('div');
  content.className = 'director-dialog-content';
  grid.before(content);
  content.append(grid, panel);
  const jump = document.createElement('button');
  jump.type = 'button'; jump.className = 'acting-jump'; jump.textContent = '↓ Герой и голос';
  $('#directorDialog .director-dialog-tools').append(jump);
  jump.addEventListener('click', () => panel.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  const mode = $('#actingMode');
  const reference = $('#actingReference');
  const driving = $('#actingVideo');
  const owner = $('#actingOwner');
  const voice = $('#actingVoice');
  const timbre = $('#actingTimbre');
  const texture = $('#actingTexture');
  const accent = $('#actingAccent');
  const emotion = $('#actingEmotion');
  const pace = $('#actingPace');
  const status = $('#actingStatus');
  const prepare = $('#actingPrepare');
  const attach = $('#actingAttach');
  const preview = $('#actingPreview');
  const voiceSample = $('#actingVoiceSample');
  const attachVoice = $('#actingAttachVoice');
  let result = null;
  let sourceAsset = null;
  let capabilities = { human: false, animal: false };
  function refreshReferences() {
    const selected = reference.value;
    reference.replaceChildren(new Option('Выбери героя из сцены', ''));
    for (const item of references()) reference.add(new Option(`<Picture ${item.number}> · ${item.owner || item.name}`, item.id));
    reference.value = [...reference.options].some((option) => option.value === selected) ? selected : '';
  }
  reference.addEventListener('change', () => {
    const item = references().find((entry) => entry.id === reference.value);
    if (item) { owner.value = item.owner || item.name.replace(/^.*? · /, '').slice(0, 80); owner.dispatchEvent(new Event('change')); }
  });

  async function refresh() {
    try {
      capabilities = await jsonResponse(await fetch('/api/acting/capabilities', { cache: 'no-store' }));
    } catch { capabilities = { human: false, animal: false }; }
    const supported = capabilities[mode.value] === true;
    prepare.disabled = !supported;
    status.textContent = mode.value === 'auto' ? 'Выбор по описанию действует в H3. Для подготовки видео выбери человека или животное.'
      : supported ? 'Готово к подготовке. GPU должен быть свободен.'
      : `LivePortrait ${mode.value === 'animal' ? 'Animals' : 'Human'} пока недоступен на GPU.`;
  }

  function updateSettings() {
    onSettingsChange({ mode: mode.value, owner: owner.value.trim(), voice: voice.value, timbre: timbre.value.trim(),
      texture: texture.value, accent: accent.value.trim(), emotion: emotion.value, pace: pace.value });
  }
  mode.addEventListener('change', () => { result = null; attach.disabled = true; preview.hidden = true; updateSettings(); void refresh(); });
  for (const control of [voice, timbre, texture, accent, emotion, pace]) control.addEventListener('change', updateSettings);
  owner.addEventListener('change', () => {
    const saved = voiceForOwner(owner.value.trim());
    if (saved) {
      voice.value = saved.voice || 'auto'; timbre.value = saved.timbre || '';
      texture.value = saved.texture || 'auto'; accent.value = saved.accent || '';
      emotion.value = saved.emotion || 'auto'; pace.value = saved.pace || 'auto';
    } else {
      voice.value = 'auto'; timbre.value = ''; texture.value = 'auto'; accent.value = '';
      emotion.value = 'auto'; pace.value = 'auto';
    }
    updateSettings();
  });
  attachVoice.addEventListener('click', async () => {
    if (!reference.value || !owner.value.trim() || !voiceSample.files?.[0]) { showToast('Выбери героя из @референсов и образец его голоса'); return; }
    attachVoice.disabled = true;
    try { await onAttachVoice({ asset: await upload(voiceSample.files[0], 'Голос'), owner: owner.value.trim(), referenceId: reference.value });
      status.textContent = `Голосовой референс прикреплён к ${owner.value.trim()}.`; }
    catch (error) { showToast(error.message); }
    finally { attachVoice.disabled = false; }
  });
  prepare.addEventListener('click', async () => {
    if (!reference.value || !driving.files?.[0]) { showToast('Выбери героя из @референсов и видеообразец'); return; }
    if (!owner.value.trim()) { showToast('Укажи имя персонажа для привязки'); owner.focus(); return; }
    prepare.disabled = true; attach.disabled = true; preview.hidden = true; result = null;
    status.textContent = 'Сохраняем референсы и готовим актёрскую игру на GPU…';
    try {
      sourceAsset = references().find((item) => item.id === reference.value);
      const driverAsset = await upload(driving.files[0], 'Движение');
      result = await jsonResponse(await fetch('/api/acting/prepare', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode.value, source_asset_id: sourceAsset.asset_id,
          driving_asset_id: driverAsset.asset_id }),
      }));
      preview.src = `/api/assets/${encodeURIComponent(result.asset_id)}/file`;
      preview.hidden = false; attach.disabled = false;
      status.textContent = 'Подготовленный ролик готов. Просмотри его и прикрепи к персонажу.';
    } catch (error) { status.textContent = `Подготовка не выполнена: ${error.message}`; }
    finally { prepare.disabled = capabilities[mode.value] !== true; }
  });
  attach.addEventListener('click', async () => {
    if (!result || !sourceAsset) return;
    try {
      await onAttach({ source: sourceAsset, prepared: result, owner: owner.value.trim(), referenceId: reference.value });
      status.textContent = `Референс актёрской игры прикреплён к ${owner.value.trim()} в текущей сцене.`;
      attach.disabled = true;
    } catch (error) { showToast(error.message); }
  });
  owner.addEventListener('focus', () => {
    const list = $('#actingOwners');
    list.replaceChildren(...owners().map((name) => new Option(name, name)));
  });
  void refresh();
  return { refreshReferences, show(visible) { panel.hidden = !visible; jump.hidden = !visible; if (visible) refreshReferences(); }, sync(settings = {}) {
    mode.value = settings.mode || 'auto'; owner.value = settings.owner || '';
    voice.value = settings.voice || 'auto'; timbre.value = settings.timbre || '';
    texture.value = settings.texture || 'auto'; accent.value = settings.accent || '';
    emotion.value = settings.emotion || 'auto'; pace.value = settings.pace || 'auto';
    void refresh();
  } };
}
