// AI scene plan is reviewed before it replaces a storyboard. No H3 job is sent here.
export function checkScenePlan(plan, targetSeconds) {
  if (!plan || !Array.isArray(plan.shots) || plan.shots.length < 2 || plan.shots.length > 12) throw new Error('План должен содержать 2–12 шотов');
  let streak = 0;
  for (const [index, shot] of plan.shots.entries()) {
    if (!shot.title?.trim() || !shot.action?.trim() || !shot.purpose?.trim()) throw new Error(`Шот ${index + 1}: не хватает действия или цели`);
    if (/[А-Яа-яЁё]/.test(shot.action)) throw new Error(`Шот ${index + 1}: действие для H3 должно быть на английском`);
    if (!shot.state_before?.trim() || !shot.state_after?.trim() || shot.state_before.trim().toLocaleLowerCase() === shot.state_after.trim().toLocaleLowerCase())
      throw new Error(`Шот ${index + 1}: укажи разные состояния до и после действия`);
    if (!shot.first_frame?.trim() || !Array.isArray(shot.characters))
      throw new Error(`Шот ${index + 1}: не хватает первого кадра или состава героев — обнови план`);
    if (!Number.isInteger(shot.duration) || shot.duration < 10 || shot.duration > 15) throw new Error(`Шот ${index + 1}: длительность вне 10–15 с`);
    if (!['new_keyframe','previous_end'].includes(shot.start_frame) || (index === 0 && shot.start_frame !== 'new_keyframe')) throw new Error(`Шот ${index + 1}: неверный стартовый кадр`);
    streak = shot.start_frame === 'previous_end' ? streak + 1 : 0;
    if (streak > 3) throw new Error(`Шот ${index + 1}: слишком длинная цепь продолжений`);
  }
  const seconds = plan.shots.reduce((sum, shot) => sum + shot.duration, 0);
  if (seconds !== targetSeconds) throw new Error(`План длится ${seconds} с вместо ${targetSeconds} с`);
  return seconds;
}

export function directorReview(plan) {
  const notes = [];
  const shots = plan?.shots || [];
  for (let index = 2; index < shots.length; index += 1) {
    if (shots[index].framing === shots[index - 1].framing && shots[index].framing === shots[index - 2].framing)
      notes.push({index, type:'framing', text:`Шоты ${index - 1}–${index + 1}: три одинаковых плана подряд. Проверь, нужна ли смена крупности.`});
  }
  let streak = 0;
  for (const [index, shot] of shots.entries()) {
    streak = shot.start_frame === 'previous_end' ? streak + 1 : 0;
    if (streak === 3 && shots[index + 1]?.start_frame === 'previous_end')
      notes.push({index:index + 1, type:'continuity', text:`Шот ${index + 2}: после трёх продолжений нужен чистый ключевой кадр.`});
  }
  for (const [index, shot] of shots.entries()) {
    if (shot.duration >= 11 && ['close','insert'].includes(shot.framing))
      notes.push({index, type:'pacing', text:`Шот ${index + 1}: ${shot.duration} с для ${shot.framing === 'insert' ? 'детали' : 'крупного плана'} — проверь темп.`});
  }
  return notes;
}

export function applyDirectorRecommendations(plan) {
  const next = structuredClone(plan);
  const shots = next.shots;
  for (const note of directorReview(next)) {
    if (note.type === 'framing') {
      // Change the middle of three repeated compositions, preserving the story order.
      const middle = shots[note.index - 1];
      middle.framing = middle.framing === 'close' ? 'wide' : 'close';
      middle.lens_mm = middle.framing === 'close' ? 85 : 24;
    }
    if (note.type === 'continuity') shots[note.index].start_frame = 'new_keyframe';
    if (note.type === 'pacing') {
      const current = shots[note.index];
      const recovered = current.duration - 10;
      const receiver = shots.find((shot, index) => index !== note.index && ['wide','medium'].includes(shot.framing) && shot.duration + recovered <= 15);
      if (receiver) {current.duration = 10; receiver.duration += recovered;}
    }
  }
  return next;
}

export function cutsFromScenePlan(plan, previousCuts = []) {
  const nextId = Math.max(0, ...previousCuts.map((cut) => Number(cut.id.split('-')[1]) || 0)) + 1;
  return plan.shots.map((shot, index) => ({
    id: `cut-${nextId + index}`, duration: shot.duration,
    prompt: shot.action.trim(),
    startFrame: shot.start_frame === 'previous_end' ? {source:'previous',asset_id:null} : {source:'keyframe',asset_id:null},
    sceneShot: {title:shot.title, purpose:shot.purpose, action:shot.action,
      first_frame:shot.first_frame || shot.state_before, characters:shot.characters || [],
      framing:shot.framing, lens_mm:shot.lens_mm,
      camera_motion:shot.camera_motion, state_before:shot.state_before, state_after:shot.state_after},
  }));
}

export function planForReview(state, plan, idea, targetSeconds) {
  return { ...state, idea, targetSeconds, plan, appliedAt: null };
}

const shotPrompt = (shot) => shot.action.trim();

export function applyBoardOperations(cuts, operations) {
  if (!Array.isArray(cuts) || !cuts.length || !Array.isArray(operations) || !operations.length)
    throw new Error('Нет шотов или правок для применения');
  const next = structuredClone(cuts);
  const known = new Set(next.map((cut) => cut.id));
  const nextId = () => `cut-${Math.max(0, ...next.map((cut) => Number(cut.id?.split('-')[1]) || 0)) + 1}`;
  const insertedAfter = new Map();
  for (const operation of operations) {
    if (operation?.op === 'update') {
      const cut = next.find((item) => item.id === operation.shot_id);
      if (!cut || !operation.changes || typeof operation.changes !== 'object') throw new Error('Шот для правки не найден');
      const {title, action, duration, framing, lens_mm, camera_motion, start_frame: startFrame} = operation.changes;
      if (duration !== undefined && (!Number.isInteger(duration) || duration < 10 || duration > 15)) throw new Error('Длительность нового дубля должна быть 10–15 с');
      if (action !== undefined && (!action.trim() || /[А-Яа-яЁё]/.test(action))) throw new Error('Действие для H3 должно быть на английском');
      if (duration !== undefined) cut.duration = duration;
      if (action !== undefined) cut.prompt = action.trim();
      if (title !== undefined || action !== undefined || framing !== undefined || lens_mm !== undefined || camera_motion !== undefined) {
        cut.sceneShot = {...(cut.sceneShot || {}), ...(title !== undefined ? {title} : {}), ...(framing !== undefined ? {framing} : {}),
          ...(action !== undefined ? {action} : {}), ...(lens_mm !== undefined ? {lens_mm} : {}), ...(camera_motion !== undefined ? {camera_motion} : {})};
      }
      if (startFrame !== undefined) cut.startFrame = startFrame === 'previous_end'
        ? {source:'previous',asset_id:null} : {source:'keyframe',asset_id:null};
      continue;
    }
    if (operation?.op !== 'add_after') throw new Error('Неизвестная операция доски');
    const anchor = operation.after_shot_id;
    if (anchor !== null && !known.has(anchor)) throw new Error('Место вставки не найдено');
    const shot = operation.shot;
    if (!shot?.title || !shot?.action || !Number.isInteger(shot.duration)) throw new Error('В новой карточке не хватает данных');
    if (shot.duration < 10 || shot.duration > 15 || /[А-Яа-яЁё]/.test(shot.action)) throw new Error('Новый дубль: 10–15 с, действие для H3 на английском');
    const id = nextId();
    const cut = {id, duration:shot.duration, prompt:shotPrompt(shot),
      startFrame:shot.start_frame === 'previous_end' ? {source:'previous',asset_id:null} : {source:'keyframe',asset_id:null},
      sceneShot:{title:shot.title, action:shot.action, framing:shot.framing, lens_mm:shot.lens_mm, camera_motion:shot.camera_motion}};
    const base = anchor === null ? next.length - 1 : next.findIndex((item) => item.id === anchor);
    const offset = insertedAfter.get(anchor) || 0;
    next.splice(base + 1 + offset, 0, cut);
    insertedAfter.set(anchor, offset + 1);
    known.add(id);
  }
  return next;
}
