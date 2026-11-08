// Evidence is entered by the author. No status here claims automatic viewing,
// scientific verification or acceptance of a generated film.
export function removePattern(patterns, id, beats) {
  if (beats.some(beat => (beat.behavior_pattern_ids || []).includes(id)))
    throw new Error('Сначала убери паттерн из действий шота.');
  return patterns.filter(pattern => pattern.id !== id);
}

export function installBehaviorEditor(container, { patterns, characters, getBeats, onChange, say }) {
  const heading = document.createElement('h3'); heading.textContent = 'Поведенческие паттерны';
  const note = document.createElement('p');
  note.textContent = 'Черновик можно сохранить для разработки. Для использования в генерации нужны указанные источник, доказательства и явная отметка автора о проверке.';
  const list = document.createElement('div'); list.className = 'production-spec-fields';
  const add = document.createElement('button'); add.type = 'button'; add.className = 'k-btn'; add.textContent = 'Добавить паттерн поведения';
  container.append(heading, note, list, add);
  const rows = [];
  const read = () => rows.map(row => row.read());
  function append(pattern) {
    const box = document.createElement('fieldset');
    const legend = document.createElement('legend'); legend.textContent = pattern.id; box.append(legend);
    const field = (label, value, options = null) => {
      const wrapper = document.createElement('label'); wrapper.textContent = label;
      const control = document.createElement(options ? 'select' : 'textarea'); control.className = 'k-field';
      if (options) options.forEach(([text, id]) => control.add(new Option(text, id)));
      else { control.rows = 2; control.maxLength = 2000; }
      control.value = value || ''; wrapper.append(control); box.append(wrapper);
      return control;
    };
    const actor = field('Герой', pattern.actor_id, characters.map(item => [item.id, item.id]));
    const trigger = field('Когда возникает реакция', pattern.trigger);
    const action = field('Наблюдаемая последовательность движений', pattern.observed_body_action);
    const source = field('Источник наблюдения или разбора', pattern.source);
    const evidence = field('Доказательства: файл, таймкод или ссылка — по одному на строку', (pattern.evidence || []).join('\n'));
    const status = field('Проверка автором', pattern.status,
      [['Черновик', 'draft'], ['Проверено по указанному источнику', 'verified']]);
    const row = { id: pattern.id, read: () => ({ id: pattern.id, actor_id: actor.value,
      trigger: trigger.value, observed_body_action: action.value, source: source.value,
      evidence: evidence.value.split('\n').map(value => value.trim()).filter(Boolean), status: status.value }) };
    // Changing the observation invalidates its previous author verification.
    // The author must explicitly verify the revised source/action before rendering.
    const invalidate = () => { status.value = 'draft'; onChange(); };
    actor.onchange = invalidate;
    for (const control of [trigger, action, source, evidence]) control.oninput = invalidate;
    status.onchange = () => onChange();
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'k-btn'; remove.textContent = 'Удалить паттерн';
    remove.onclick = () => {
      try { removePattern(read(), row.id, getBeats()); rows.splice(rows.indexOf(row), 1); box.remove(); add.disabled = false; onChange(); }
      catch (error) { say(error.message); }
    };
    box.append(remove); list.append(box); rows.push(row); add.disabled = rows.length >= 120;
  }
  patterns.forEach(append);
  add.onclick = () => {
    const ids = new Set(rows.map(row => row.id)); let number = rows.length + 1;
    while (ids.has(`behavior${number}`)) number += 1;
    append({ id: `behavior${number}`, actor_id: characters[0]?.id || '', trigger: '',
      observed_body_action: '', source: '', evidence: [], status: 'draft' });
    onChange();
  };
  return { read };
}
