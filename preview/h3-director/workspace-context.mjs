// The empty viewer reflects the selected draft shot without inventing a rendered take.
export function shotSummary(prompt) {
  const officialSummary = String(prompt || '').match(/(?:^|\n)summary:\s*\n([\s\S]*?)(?=\n\s*retention_analysis:|$)/i);
  if (officialSummary) {
    const prose = officialSummary[1].trim().replace(/^\[[^\]]+\]\s*/, '').replace(/\s+/g, ' ');
    if (prose) return prose.slice(0, 220);
  }
  const lines = String(prompt || '').split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const target = lines.findIndex((line) => /^FINAL TARGET\s*:/i.test(line));
  const candidates = target >= 0 ? lines.slice(target + 1) : lines;
  const line = candidates.find((part) => !/^[A-Z][A-Z\s/—–:+\-0-9<>]{5,}$/.test(part) &&
    !/^(?:DURATION|FORMAT|SCENE TYPE|VISUAL TARGET|<Picture|<Video|<Audio)\b/i.test(part));
  return (line || lines[0] || '').replace(/\s+/g, ' ').slice(0, 220);
}

export function createShotPlaceholder(cut, index, references, onEdit) {
  const panel = document.createElement('div');
  panel.className = 'shot-placeholder';
  const eyebrow = document.createElement('span');
  eyebrow.className = 'shot-placeholder-kicker';
  eyebrow.textContent = `КАДР ${String(index + 1).padStart(2, '0')} · ЧЕРНОВИК`;
  const title = document.createElement('strong');
  title.textContent = cut?.prompt?.trim() ? 'Подготовка кадра' : 'Начните с действия в кадре';
  const description = document.createElement('p');
  description.textContent = cut?.prompt?.trim()
    ? shotSummary(cut.prompt)
    : 'Опишите героя, действие и изменение к концу кадра. Результат появится здесь после генерации.';
  const meta = document.createElement('div');
  meta.className = 'shot-placeholder-meta';
  const duration = document.createElement('span');
  duration.textContent = `${cut?.duration || 5} сек`;
  const assets = document.createElement('span');
  assets.textContent = `${references.length} ${references.length === 1 ? 'референс' : 'референсов'}`;
  meta.append(duration, assets);
  const action = document.createElement('button');
  action.type = 'button';
  action.textContent = cut?.prompt?.trim() ? 'Изменить описание' : 'Описать кадр';
  action.addEventListener('click', onEdit);
  panel.append(eyebrow, title, description, meta, action);
  return panel;
}
