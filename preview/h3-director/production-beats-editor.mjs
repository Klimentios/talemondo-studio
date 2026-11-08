// Owns beat rows only; the parent editor validates and versions the complete ShotSpec.
export function removeBeat(beats, id) {
  if (beats.length <= 1) throw new Error('В шоте должно остаться хотя бы одно действие.');
  if (beats.some(beat => beat.cause_beat_id === id))
    throw new Error('Сначала измени причину у действий, которые ссылаются на это действие.');
  return beats.filter(beat => beat.id !== id);
}

export function installBeatsEditor(container, { beats, characters, duration, say, patterns = () => [] }) {
  const rows = [];
  const list = document.createElement('div');
  list.className = 'production-spec-fields';
  const add = document.createElement('button');
  add.type = 'button'; add.className = 'k-btn'; add.textContent = 'Добавить действие';
  container.append(list, add);
  const read = () => rows.map(row => row.read());
  function refreshPatterns() {
    for (const row of rows) {
      const selected = new Set([...row.patterns.selectedOptions].map(option => option.value));
      row.patterns.replaceChildren(...patterns().map(pattern => new Option(
        `${pattern.id} · ${pattern.actor_id} · ${pattern.status === 'verified' ? 'проверено автором' : 'черновик'}`,
        pattern.id, false, selected.has(pattern.id))));
    }
  }
  function refreshCauses() {
    for (const row of rows) {
      const selected = row.cause.value;
      row.cause.replaceChildren(new Option('Внешнее событие', ''),
        ...rows.filter(other => other.id !== row.id).map(other => new Option(other.id, other.id)));
      row.cause.value = selected;
    }
    add.disabled = rows.length >= 120;
  }
  function append(beat) {
    const box = document.createElement('fieldset');
    const title = document.createElement('legend'); title.textContent = `Действие ${beat.id}`; box.append(title);
    const field = (label, value, numeric = false) => {
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const input = document.createElement(numeric ? 'input' : 'textarea');
      if (numeric) { input.type = 'number'; input.min = '0'; input.max = String(duration); input.step = '0.1'; }
      else { input.rows = 2; input.maxLength = 2000; }
      input.value = value ?? ''; input.className = 'k-field'; wrapper.append(input); box.append(wrapper);
      return input;
    };
    const actorLabel = document.createElement('label'); actorLabel.textContent = 'Участники';
    const actors = document.createElement('select'); actors.multiple = true; actors.className = 'k-field';
    for (const actor of characters) actors.add(new Option(actor.id, actor.id, false, beat.actor_ids.includes(actor.id)));
    actorLabel.append(actors); box.append(actorLabel);
    const start = field('Начало, с', beat.start, true), end = field('Конец, с', beat.end, true);
    const causeLabel = document.createElement('label'); causeLabel.textContent = 'Причина — другое действие';
    const cause = document.createElement('select'); cause.className = 'k-field'; causeLabel.append(cause); box.append(causeLabel);
    const trigger = field('Что вызвало действие', beat.cause);
    const action = field('Что видно в движении тела', beat.observed_body_action);
    const effect = field('Результат и реакция', beat.effect);
    const patternLabel = document.createElement('label'); patternLabel.textContent = 'Поведенческие паттерны';
    const patternSelect = document.createElement('select'); patternSelect.multiple = true; patternSelect.className = 'k-field';
    patternLabel.append(patternSelect); box.append(patternLabel);
    const row = { id: beat.id, cause, patterns: patternSelect, read: () => ({ ...structuredClone(beat),
      behavior_pattern_ids: [...patternSelect.selectedOptions].map(option => option.value),
      actor_ids: [...actors.selectedOptions].map(option => option.value), start: Number(start.value), end: Number(end.value),
      cause_beat_id: cause.value || null, cause: trigger.value, observed_body_action: action.value, effect: effect.value }) };
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'k-btn'; remove.textContent = 'Удалить действие';
    remove.onclick = () => {
      try {
        removeBeat(read(), row.id); rows.splice(rows.indexOf(row), 1); box.remove(); refreshCauses(); say('');
      } catch (error) { say(error.message); }
    };
    box.append(remove); list.append(box); rows.push(row); refreshCauses();
    refreshPatterns();
    for (const option of patternSelect.options) option.selected = (beat.behavior_pattern_ids || []).includes(option.value);
    // Initial references may point to a row appended later. Restore them after all rows exist.
    cause.dataset.initial = beat.cause_beat_id || '';
  }
  beats.forEach(append);
  rows.forEach(row => { row.cause.value = row.cause.dataset.initial; });
  add.onclick = () => {
    const ids = new Set(rows.map(row => row.id));
    let number = rows.length + 1;
    while (ids.has(`b${number}`)) number += 1;
    append({ id: `b${number}`, actor_ids: characters.length ? [characters[0].id] : [],
      start: 0, end: Number(duration), cause: '', observed_body_action: '', effect: '', behavior_pattern_ids: [] });
    say('Заполни причину, движение и результат нового действия, затем проверь тайминг.');
  };
  return { read, refreshPatterns };
}
