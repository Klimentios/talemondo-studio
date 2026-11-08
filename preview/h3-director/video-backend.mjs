// «Свой GPU / MiniMax API»: the Studio-wide video route. The choice lives on the Mac
// Studio (/api/studio/video-backend), so agents and MCP jobs follow the same switch.
// GPU: ComfyUI graph with LoRA, megapixels, 16:9. API: 768P 9:16, no LoRA, no steps.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { $, apiJson, showToast } from './app-shared.mjs?v=20261106-refmatch';

export const isCloudBackend = () => appState.videoBackend === 'minimax_api';

export function applyVideoHealth(health) {
  appState.gpuReachable = Boolean(health.gpu_reachable);
  appState.cloudApiConfigured = Boolean(health.minimax_api_configured);
  appState.videoBackend = health.video_backend === 'minimax_api' ? 'minimax_api' : 'gpu';
  renderVideoBackend();
}

export function renderVideoBackend() {
  const cloud = isCloudBackend();
  const ready = cloud ? appState.cloudApiConfigured : appState.gpuReachable;
  $('.gpu-badge')?.classList.toggle('is-ready', ready);
  $('.gpu-badge')?.classList.toggle('is-offline', !ready);
  const badge = $('.preview-badge');
  if (badge) badge.textContent = cloud
    ? (ready ? 'Видео · MiniMax API 768P' : 'Видео · ключ MiniMax API не настроен')
    : (ready ? 'Видео · свой GPU подключён' : 'Видео · свой GPU · ждёт сервер');
  for (const button of document.querySelectorAll('#videoBackend [data-backend]')) {
    button.setAttribute('aria-pressed', String(button.dataset.backend === appState.videoBackend));
    if (button.dataset.backend === 'minimax_api') button.disabled = !appState.cloudApiConfigured;
  }
  const note = $('#videoBackendNote');
  if (note) note.textContent = cloud
    ? 'MiniMax API: 768P, только 9:16, без LoRA, шагов и кэша. Замена по видео — по рецепту API: части 4–8 с, карта действий по шотам и защита от утечек исходника.'
    : 'Свой GPU (ComfyUI): LoRA character_swap, мегапиксели, шаги, кэш safe, 9:16 и 16:9.';
  const quality = $('#qualityPreset');
  if (quality) quality.closest('.select-wrap').hidden = cloud;
  const pill = $('#cloudQualityPill');
  if (pill) pill.hidden = !cloud;
  const wide = $('#aspect option[value="16:9"]');
  if (wide) wide.disabled = cloud;
  if (cloud && appState.project?.aspect && appState.project.aspect !== '9:16') {
    appState.project.aspect = '9:16';
    $('#aspect').value = '9:16';
  }
}

export function installVideoBackend() {
  $('#videoBackend')?.addEventListener('click', async (event) => {
    const value = event.target.closest('[data-backend]')?.dataset.backend;
    if (!value || value === appState.videoBackend) return;
    try {
      const result = await apiJson('/api/studio/video-backend', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ video_backend: value }) });
      appState.videoBackend = result.video_backend;
      renderVideoBackend();
      showToast(value === 'gpu' ? 'Новые видео пойдут на свой GPU-сервер' : 'Новые видео пойдут в MiniMax API 768P');
    } catch (error) {
      showToast(error.message);
    }
  });
}
