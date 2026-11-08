export function takeCount(value) {
  const count = Number(value);
  return Number.isInteger(count) ? Math.max(1, Math.min(4, count)) : 1;
}

export function settingsForTake(settings, index, count) {
  const variant = { ...settings };
  if (count > 1 && Number.isSafeInteger(variant.seed)) {
    variant.seed = (variant.seed + index) % 2147483647;
  }
  return variant;
}

export function bindCreateBatch({ button, countSelect, prepare, referenceIds, enqueue, onSubmitted, saveDraft, isReady, toast }) {
  const label = button.querySelector('span');
  const updateLabel = () => {
    const count = takeCount(countSelect.value);
    if (label) label.textContent = `Создать ${count} ${count === 1 ? 'вариант' : 'варианта'}`;
    button.setAttribute('aria-label', `Создать ${count} ${count === 1 ? 'вариант' : 'варианта'}`);
  };
  countSelect.addEventListener('change', updateLabel);
  updateLabel();

  button.addEventListener('click', async () => {
    button.disabled = true;
    let submitted = 0;
    try {
      const { prompt, settings, production_spec } = prepare();
      const ids = referenceIds();
      const count = takeCount(countSelect.value);
      for (let index = 0; index < count; index += 1) {
        if (label) label.textContent = `В очереди ${index + 1}/${count}…`;
        const job = await enqueue(prompt, ids, settingsForTake(settings, index, count), production_spec);
        submitted += 1;
        onSubmitted?.(job, index, count);
      }
      await saveDraft();
      toast(`${submitted} ${submitted === 1 ? 'задание отправлено' : 'варианта отправлено в очередь'}. Промпт и настройки сохранены.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Не удалось отправить задание');
      toast(submitted ? `${submitted} заданий отправлено; следующее не поставлено: ${message}` : message);
    } finally {
      updateLabel();
      button.disabled = !isReady();
    }
  });
}

export function updateCreateAvailability({ button, note, ready, paused, gpuReachable, cloudConfigured = false, en = false }) {
  const message = !ready ? (en ? 'Local Studio is unavailable.' : 'Локальная Studio недоступна.')
    : paused ? (en ? 'Queue paused: the job will be saved and wait for resume.' : 'Очередь на паузе: задание сохранится и будет ждать возобновления.')
      : !gpuReachable && !cloudConfigured ? (en ? 'GPU unavailable: the job will wait in the Mac queue.' : 'GPU недоступен: задание будет ждать в очереди Mac.') : '';
  button.title = message || (en ? 'Save the job and start generation.' : 'Сохранить задание и начать генерацию.');
  if (note) { note.textContent = message; note.hidden = !message; }
}
