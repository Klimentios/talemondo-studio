// Hero replacement uses the native H3 video reference; the plain «Движение» role would promise
// a pose transfer that no longer exists (structural ControlNet was removed).
export function validateReplacementMotion(settings, references) {
  if (settings.mode !== 'replace') return;
  const images = references.filter((ref) => ref.kind === 'image');
  const videos = references.filter((ref) => ref.kind === 'video');
  if (!images.length || !videos.length) return;
  if (videos.length !== 1) throw new Error('Для замены героя нужен один видео-референс');
  const role = videos[0].role || '';
  if (role.startsWith('Авто'))
    throw new Error('Укажи роль видео: «Движение и камера» для переноса движения или «Исходное видео» для сцены.');
  if (role === 'Движение')
    throw new Error('Для замены героя выбери роль видео «Движение и камера» («Исходное видео» переносит сцену целиком, вместе с людьми).');
}
