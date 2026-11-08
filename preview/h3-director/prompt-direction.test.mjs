import test from 'node:test';
import assert from 'node:assert/strict';
import { parsePromptDirection, writePromptDirection, promptDirectionErrors } from './prompt-direction.mjs';
import { directorConfigForState } from './director-config.mjs';
import { DIRECTOR_PRESETS, DIRECTOR_CATEGORIES } from './director-presets.mjs';
import { cameraStateForPresets, cameraOpticsForPresets, NATIVE_CAMERA_MOVES } from './director-camera.mjs';

const block = (body, scene = '<Picture 1> — герой. Герой улыбается.') => `[Режиссура]\n${body}\n[/Режиссура]\n\n${scene}`;
test('paste selects catalogue presets and omitted categories are Auto', () => {
  const p = parsePromptDirection(block('Кадр: Крупный план\nДвижение: медленный наезд\nФокус: По герою\nСвет: Пасмурный экстерьер'));
  assert.deepEqual(p.errors, []);
  assert.deepEqual(p.state, { shot:'shot.close', movement:'movement.push', focus:'focus.auto', lighting:'lighting.overcast' });
  assert.ok(!p.state.color);
  assert.ok(p.scene.includes('<Picture 1>'));
});
test('plain prompts retain legacy card behaviour', () => {
  assert.equal(parsePromptDirection('Кадр: крупный план. Герой улыбается.').present, false);
  assert.deepEqual(promptDirectionErrors('Сцена', { movement:'movement.push' }), []);
});
test('every preset and Auto round-trip with stable IDs', () => {
  for (const preset of DIRECTOR_PRESETS) {
    const p = parsePromptDirection(writePromptDirection('Сцена', { [preset.category]: preset.id }));
    assert.deepEqual(p.errors, []); assert.equal(p.state[preset.category], preset.id);
  }
  assert.deepEqual(parsePromptDirection(block('Камера: Авто\nЦвет: auto')).state, {});
});
test('manual card change updates one block without altering story, timing or references', () => {
  const story = '0–3 сек: <Picture 1> идёт. 3–5 сек: говорит «Стой!».\nКамера: это часть истории.';
  const before = block('Кадр: Крупный', story);
  const after = writePromptDirection(before, { shot:'shot.wide', movement:'movement.static' });
  assert.ok(after.endsWith(story));
  assert.equal(after.match(/\[Режиссура\]/g).length, 1);
  assert.equal(parsePromptDirection(after).state.shot, 'shot.wide');
});
test('freeform direction reaches scene text without selecting a fictional preset', () => {
  const p = parsePromptDirection(block('Объектив: 35mm natural perspective\nДвижение: smooth backward tracking'));
  assert.deepEqual(p.errors, []);
  assert.deepEqual(p.state, {});
  assert.match(p.scene, /Объектив: 35mm natural perspective/);
  assert.match(p.scene, /Движение: smooth backward tracking/);
  assert.match(writePromptDirection(block('Объектив: 35mm natural perspective'), { shot: 'shot.full' }), /Объектив: 35mm natural perspective/);
  const combined = parsePromptDirection(writePromptDirection(block('Кадр: feet remain visible'), { shot: 'shot.full' }));
  assert.equal(combined.state.shot, 'shot.full');
  assert.match(combined.scene, /feet remain visible/);
});
test('duplicate, unknown, empty and malformed entries cannot silently apply', () => {
  for (const text of [block('Кадр: Крупный\nКадр: Общий'), block('Ошибка: Авто'), block('Кадр:'), '[Режиссура]\nКадр: Крупный', block('Кадр: Крупный') + block('Свет: Авто')]) {
    assert.ok(parsePromptDirection(text).errors.length, text);
    assert.ok(promptDirectionErrors(text, {}).length);
    assert.throws(() => writePromptDirection(text, {}));
  }
});
test('custom presets with duplicate names require their IDs', () => {
  const custom = [{ category:'lighting', id:'custom.a', name:'Мой свет' }, { category:'lighting', id:'custom.b', name:'Мой свет' }];
  assert.ok(parsePromptDirection(block('Свет: Мой свет'), custom).errors.length);
  assert.equal(parsePromptDirection(block('Свет: custom.a'), custom).state.lighting, 'custom.a');
});
test('explicit static camera conflict and stale cards block submission', () => {
  const text = block('Движение: Наезд', 'Камера неподвижна. Герой улыбается.');
  assert.match(promptDirectionErrors(text, {movement:'movement.push'}).join(' '), /Конфликт/);
  assert.match(promptDirectionErrors(block('Кадр: Крупный'), {shot:'shot.wide'}).join(' '), /расходятся/);
});
test('selected prompt presets feed real config, trajectory and optics', () => {
  const state = parsePromptDirection(block('Объектив: lens.85\nКадр: shot.close\nДвижение: movement.push')).state;
  const config = directorConfigForState(state, [], DIRECTOR_PRESETS);
  assert.equal(config.lens.parameters.focal_length_mm, 85);
  assert.equal(config.shot.parameters.framing, 'close');
  assert.equal(config.movement.parameters.movement, 'dolly');
  const camera = cameraStateForPresets(state, DIRECTOR_PRESETS, 121);
  assert.ok(camera); assert.ok(cameraOpticsForPresets(state, DIRECTOR_PRESETS, camera));
  assert.equal(DIRECTOR_CATEGORIES.length, 9);
});
test('English camera grammar maps movement speed, start and end framing to config and camera geometry', () => {
  const prompt = block('Movement: movement.push\nSpeed: fast\nFraming: shot.medium\nEnd: shot.close\nКамера: Авто');
  const parsed = parsePromptDirection(prompt);
  assert.deepEqual(parsed.errors, []);
  assert.deepEqual(parsed.state, { movement: 'movement.push', shot: 'shot.medium' });
  assert.equal(parsed.camera.speed, 'fast');
  assert.equal(parsed.camera.end_framing, 'close');
  assert.doesNotMatch(parsed.scene, /End:/);
  const config = directorConfigForState(parsed.state, [], DIRECTOR_PRESETS, parsed.camera);
  assert.equal(config.movement.parameters.speed, 'fast');
  assert.equal(config.movement.parameters.end_framing, 'close');
  assert.equal(config.shot.parameters.framing, 'medium');
  const camera = cameraStateForPresets(parsed.state, DIRECTOR_PRESETS, 121, '', parsed.camera);
  assert.ok(camera.keys[0].distance > camera.keys[1].distance);
  assert.equal(camera.keys[1].distance, 1.3);
  assert.equal(camera.keys[1].target[1], 1.6);
  assert.ok(camera.keys[1].t < 120 / 24);
  assert.match(writePromptDirection(prompt, parsed.state), /Speed: fast[\s\S]*End: shot.close/);
});
test('60 camera-library moves parse into validated config and only native paths emit geometry', () => {
  const moves = DIRECTOR_PRESETS.filter((preset) => preset.category === 'movement' && preset.parameters.camera_move);
  assert.equal(moves.length, 60);
  assert.equal(new Set(moves.map((preset) => preset.parameters.camera_move)).size, 60);
  for (const preset of moves) {
    const parsed = parsePromptDirection(block(`Movement: ${preset.id}\nFraming: shot.medium`));
    assert.deepEqual(parsed.errors, [], preset.id);
    assert.equal(parsed.state.movement, preset.id);
    const config = directorConfigForState(parsed.state, [], DIRECTOR_PRESETS);
    assert.equal(config.movement.parameters.camera_move, preset.parameters.camera_move);
    const trajectory = cameraStateForPresets(parsed.state, DIRECTOR_PRESETS, 121);
    assert.equal(Boolean(trajectory), NATIVE_CAMERA_MOVES.includes(preset.parameters.movement), preset.id);
  }
});
test('zoom changes focal length at fixed position and pan turns from a fixed camera point', () => {
  const zoom = DIRECTOR_PRESETS.find((preset) => preset.id === 'movement.zoom_in_slow');
  const zoomState = cameraStateForPresets({ movement: zoom.id, shot: 'shot.medium' }, DIRECTOR_PRESETS, 121,
    '', { end_framing: 'close', speed: 'slow' });
  assert.ok(zoomState.keys[1].focal_length_mm > zoomState.keys[0].focal_length_mm);
  assert.equal(zoomState.keys[1].distance, zoomState.keys[0].distance);
  const pan = DIRECTOR_PRESETS.find((preset) => preset.id === 'movement.pan_right');
  const panState = cameraStateForPresets({ movement: pan.id, shot: 'shot.medium' }, DIRECTOR_PRESETS, 121);
  const cameraPosition = (key) => {
    const orbit = key.orbit * Math.PI / 180, elevation = key.elevation * Math.PI / 180;
    return [Math.sin(orbit) * Math.cos(elevation), Math.sin(elevation), Math.cos(orbit) * Math.cos(elevation)]
      .map((component, index) => key.target[index] + key.distance * component);
  };
  const before = cameraPosition(panState.keys[0]), after = cameraPosition(panState.keys[1]);
  before.forEach((value, index) => assert.ok(Math.abs(value - after[index]) < 1e-9));
  assert.ok(panState.keys[1].target[0] > panState.keys[0].target[0]);
});
test('slow zoom in and out change focal length in the requested direction without End framing', () => {
  for (const [id, direction] of [['movement.zoom_in_slow', 1], ['movement.zoom_out_slow', -1]]) {
    const parsed = parsePromptDirection(block(`Movement: ${id}\nFraming: shot.medium`));
    assert.deepEqual(parsed.errors, [], id);
    const camera = cameraStateForPresets(parsed.state, DIRECTOR_PRESETS, 121, '', parsed.camera);
    assert.ok(camera, id);
    const delta = camera.keys[1].focal_length_mm - camera.keys[0].focal_length_mm;
    assert.equal(Math.sign(delta), direction, id);
    assert.equal(camera.keys[1].distance, camera.keys[0].distance, id);
  }
});
test('camera speed auto-selects missing move; explicit static, duplicates and invalid speed fail closed', () => {
  assert.deepEqual(parsePromptDirection(block('Speed: fast')).errors, []);
  assert.match(parsePromptDirection(block('Movement: movement.static\nSpeed: fast')).errors.join(' '), /статичной камере/);
  assert.ok(parsePromptDirection(block('Movement: movement.push\nSpeed: fast\nSpeed: slow')).errors.length);
  assert.ok(parsePromptDirection(block('Movement: movement.push\nSpeed: very fast')).errors.length);
});
test('End fills omitted start framing and still requires a native camera movement', () => {
  assert.equal(parsePromptDirection(block('Movement: movement.push\nEnd: shot.close')).state.shot, 'shot.medium');
  assert.match(parsePromptDirection(block('Movement: movement.track\nFraming: shot.medium\nEnd: shot.close')).errors.join(' '), /нативной траекторией/);
  assert.ok(parsePromptDirection(block('Movement: movement.push\nFraming: shot.medium\nEnd: settle close')).errors.length);
});

 test('screenshot Speed and End resolve to real native camera inputs without changing acting or scene', () => {
  const prompt = block('Актёрская игра: acting.physical_theatre\nSpeed: medium\nEnd: shot.close');
  const parsed = parsePromptDirection(prompt);
  assert.deepEqual(parsed.errors, []);
  assert.equal(parsed.state.shot, 'shot.medium');
  assert.equal(parsed.state.movement, 'movement.push');
  assert.equal(parsed.state.acting, 'acting.physical_theatre');
  const config = directorConfigForState(parsed.state, [], DIRECTOR_PRESETS, parsed.camera);
  assert.equal(config.movement.parameters.speed, 'medium');
  assert.equal(config.movement.parameters.end_framing, 'close');
  const geometry = cameraStateForPresets(parsed.state, DIRECTOR_PRESETS, 241, parsed.scene, parsed.camera);
  assert.ok(geometry.keys.at(-1).distance < geometry.keys[0].distance);
  assert.match(parsed.scene, /Герой улыбается/);
  assert.deepEqual(promptDirectionErrors(prompt, parsed.state), []);
  assert.equal(parsePromptDirection(block('Framing: shot.close\nEnd: shot.wide')).state.movement, 'movement.pull');
  assert.equal(parsePromptDirection(block('End: shot.medium')).state.shot, 'shot.full');
 });
