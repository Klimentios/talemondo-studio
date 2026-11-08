import { bindWorld, isFantasy } from './story-canvas-worlds.mjs?v=20261103-codex-world-1';
import { setChatStartFrame } from './chat-image-handoff.mjs?v=20261101-1';

export function companionLink(requestId) {
  if (!/^[a-f0-9]{32}$/.test(requestId)) throw new Error('Некорректный ID задания');
  return `codex://plugins/h3-image-companion@studio-local/app/image_companion?path=${encodeURIComponent(`/?request_id=${requestId}`)}`;
}

export function imagePurpose(cut, world) {
  return isFantasy(cut) && !world?.assetId ? 'fantasy_world' : 'frame';
}

// Studio prepares the work. An ordinary ChatGPT MCP client claims it and supplies its generated file.
export function installChatImages({ shell, studio, selectedShot, toast, sync }) {
  const dialog = document.createElement('dialog');
  dialog.className = 'scenarist-dialog chat-image-dialog';
  dialog.innerHTML = `<h2>Картинка для шота</h2>
    <p>Задание собрано из подготовленной агентом сцены. Сюжет, референсы и визуальный паспорт передаются вместе с ним.</p>
    <p data-image="context"></p><h3>Задание агента</h3><p data-image="brief"></p>
    <details><summary>Изменить задание · необязательно</summary>
    <label>Назначение<select data-image="purpose"><option value="frame">Стартовый кадр шота</option><option value="fantasy_world">Единый мир фантазии · без героев</option></select></label>
    <label>Дополнительное пожелание<textarea data-image="notes" rows="2" maxlength="2000" placeholder="Основное описание уже подготовлено агентом"></textarea></label></details>
    <p data-image="status" role="status"></p><img data-image="preview" alt="Результат задания ChatGPT" width="280" hidden>
    <p><a data-image="launch" hidden>Открыть Image Companion в ChatGPT</a></p>
    <p>Image Companion открывает беседу с готовым заданием через интерфейс клиента ChatGPT. В нём нужен обычный ChatGPT со встроенным ImageGen. Повторная генерация при потере связи не запускается.</p>
    <div class="scenarist-actions"><button type="button" data-image="send">Подготовить и открыть ChatGPT</button>
    <button type="button" data-image="apply" hidden>Применить картинку</button><button type="button" data-image="edit" hidden>Изменить запрос</button><button type="button" data-image="close">Закрыть</button></div>`;
  document.body.append(dialog);
  const el = (key) => dialog.querySelector(`[data-image="${key}"]`);
  let target = null, request = null, timer = 0;
  const api = async (path, body) => {
    const response = await fetch(`/api/chat-images${path}`, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Запрос картинки не принят');
    return data;
  };
  const status = (text) => { el('status').textContent = text; };
  const input = () => ({ scene_id: target.sceneId, shot_id: target.shotId,
    purpose: el('purpose').value, notes: el('notes').value.trim() });
  function show(row) {
    request = row;
    const labels = { waiting_chatgpt: 'Задание сохранено. Ожидает чата ChatGPT.', claimed: 'Чат взял задание. Ожидается картинка.',
      completed: 'Картинка готова в библиотеке. Проверь и примени её.', stale: 'Сцена изменилась. Картинка сохранена в библиотеке, автоматически применять её нельзя.',
      failed: `Чат сообщил об ошибке: ${row.error || 'нет результата'}` };
    status(labels[row.state] || row.state);
    el('apply').hidden = row.state !== 'completed';
    el('launch').hidden = row.state !== 'waiting_chatgpt';
    el('launch').href = companionLink(row.id);
    el('edit').hidden = ['waiting_chatgpt', 'claimed'].includes(row.state);
    el('preview').hidden = !row.asset_id;
    if (row.asset_id) el('preview').src = `/api/assets/${encodeURIComponent(row.asset_id)}/thumbnail`;
    el('send').disabled = ['waiting_chatgpt', 'claimed', 'completed'].includes(row.state);
    el('purpose').disabled = ['waiting_chatgpt', 'claimed', 'completed'].includes(row.state);
    el('notes').disabled = el('purpose').disabled;
  }
  async function poll() {
    if (!request || !dialog.open) return;
    try { show(await api(`/${encodeURIComponent(request.id)}`)); }
    catch (error) { status(error.message); }
    if (['waiting_chatgpt', 'claimed'].includes(request?.state)) timer = setTimeout(poll, 4000);
  }
  async function open() {
    clearTimeout(timer);
    const shot = selectedShot();
    if (!shot) { toast('Выбери шот для картинки'); return; }
    target = { sceneId: studio().sceneState().activeSceneId, shotId: shot.id };
    request = null;
    el('purpose').disabled = false; el('notes').disabled = false;
    el('send').disabled = true; el('apply').hidden = true; el('edit').hidden = true; el('launch').hidden = true; el('preview').hidden = true;
    el('notes').value = ''; el('purpose').value = imagePurpose(shot, studio().project().storyboard?.fantasyWorld);
    el('brief').textContent = ''; el('context').textContent = ''; dialog.querySelector('details').open = false;
    status('Собираю контекст сохранённой сцены…'); dialog.showModal();
    try {
      await studio().saveDraft();
      const context = await api('/context', input());
      if (!dialog.open) return;
      el('context').textContent = `${context.title || 'Сцена'} · ${context.franchise_kit.franchise || context.franchise_id} · ${context.references.length} референсов · ${context.aspect || ''}. Контекст и паспорт приложены.`;
      el('brief').textContent = context.image_brief;
      const rows = await api(`?scene_id=${encodeURIComponent(target.sceneId)}`);
      const prior = rows.find((r) => r.shot_id === target.shotId && r.purpose === el('purpose').value);
      if (prior) {
        el('purpose').value = prior.purpose; el('notes').value = prior.notes; show(prior); void poll();
        const priorContext = await api('/context', input()); el('brief').textContent = priorContext.image_brief;
      }
      else { el('send').disabled = false; status('Контекст готов. Можно сохранить задание для ChatGPT.'); }
    } catch (error) {
      el('send').disabled = true; status(error.message);
    }
  }
  shell.addEventListener('click', (event) => {
    if (event.target.closest('[data-chat-images="open"]')) { event.stopPropagation(); void open(); }
  });
  el('send').addEventListener('click', async () => {
    el('send').disabled = true;
    try {
      await studio().saveDraft(); show(await api('', input())); void poll();
      if (request.state === 'waiting_chatgpt') el('launch').click();
    }
    catch (error) { status(error.message); el('send').disabled = false; }
  });
  el('purpose').addEventListener('change', async () => {
    el('send').disabled = true;
    try {
      await studio().saveDraft(); const context = await api('/context', input());
      el('brief').textContent = context.image_brief; el('send').disabled = false;
    } catch (error) { status(error.message); }
  });
  el('apply').addEventListener('click', async () => {
    el('apply').disabled = true;
    try {
      if (studio().sceneState().activeSceneId !== target.sceneId) throw new Error('Вернись в сцену этого задания');
      await studio().saveDraft();
      const current = await api('/context', { scene_id: request.scene_id, shot_id: request.shot_id, purpose: request.purpose, notes: request.notes });
      if (current.fingerprint !== request.fingerprint) throw new Error('Сцена изменилась после запроса. Картинка остаётся в библиотеке.');
      if (request.purpose === 'frame') {
        await setChatStartFrame({ studio: studio(), storyboard: window.h3Storyboard, sceneId: target.sceneId,
          shotId: target.shotId, assetId: request.asset_id });
      } else {
        if (window.h3Storyboard?.state?.().running) throw new Error('Сначала останови сборку сцены');
        const asset = { asset_id: request.asset_id, kind: 'image', original_name: 'chatgpt-fantasy-world.png' };
        const ref = studio().addReferenceAsset(asset, 'Окружение');
        const p = studio().project();
        p.storyboard = { ...p.storyboard, fantasyWorld: { description: request.notes || current.fantasy_world.description || '',
          assetId: request.asset_id, refId: ref.id, state: 'completed', imageRequestId: request.id } };
        studio().editCuts((cuts) => bindWorld(cuts, studio().references(), ref.id));
        await studio().saveDraft();
      }
      sync(); status('Картинка применена. Приёмка и запуск видео — отдельные действия.'); el('apply').hidden = true;
    } catch (error) { status(error.message); }
    finally { el('apply').disabled = false; }
  });
  el('close').addEventListener('click', () => dialog.close());
  el('edit').addEventListener('click', () => {
    clearTimeout(timer); request = null;
    el('purpose').disabled = false; el('notes').disabled = false; el('send').disabled = false;
    el('apply').hidden = true; el('edit').hidden = true;
    el('launch').hidden = true;
    dialog.querySelector('details').open = true;
    status('Измени уточнение или назначение картинки. Одинаковый запрос вернёт прежнее задание.');
  });
  dialog.addEventListener('close', () => clearTimeout(timer));
}
