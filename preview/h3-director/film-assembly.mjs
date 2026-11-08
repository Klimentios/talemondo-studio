import { shotStatus, acceptedShot } from './storyboard-model.mjs?v=20261102-take-admission-1';

export const MAX_FILM_SHOTS = 60;

// Every scene contributes every selected shot in the visible film order.
// Never omit a failed, pending, or stale shot from the final film silently.
export function planFilm(scenes, jobsById) {
  const list = Array.isArray(scenes) ? scenes : [];
  const ids = [];
  const issues = [];
  let seconds = 0;
  let shots = 0;
  for (const [sceneIndex, scene] of list.entries()) {
    const cuts = Array.isArray(scene.cuts) ? scene.cuts : [];
    for (const [cutIndex, cut] of cuts.entries()) {
      shots += 1;
      seconds += Number(cut.duration) || 0;
      const status = shotStatus(cuts, cutIndex, jobsById);
      if (status === 'done' && acceptedShot(cut)) ids.push(cut.shotJobId);
      else {
        const where = `${scene.title || `Сцена ${sceneIndex + 1}`} · шот ${cutIndex + 1}`;
        const reason = status === 'stale' ? 'продолжение устарело после смены предыдущего шота'
          : status === 'idle' ? 'нет выбранного дубля'
            : status === 'failed' ? 'генерация завершилась ошибкой'
              : 'дубль ещё не готов';
        issues.push(`${where}: ${reason}`);
      }
    }
  }
  if (!shots) issues.push('Добавь хотя бы один шот.');
  if (shots > MAX_FILM_SHOTS) issues.push(`Для одной сборки доступно не более ${MAX_FILM_SHOTS} шотов; сейчас ${shots}.`);
  return { ids, issues, scenes: list.length, shots, seconds, ready: issues.length === 0 };
}
