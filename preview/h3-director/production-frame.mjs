// Compiles only the opening image, not the future events of the whole video.
// Required identities/style anchors never disappear silently to fit a model limit.
export function productionFrameRequest(cut, references, { maxImages = 3, sceneStyle = '', aspect = '16:9', previousFrame = null } = {}) {
  const spec = cut.productionSpec;
  const images = new Map(references.filter(ref => ref.kind === 'image').map(ref => [ref.id, ref]));
  const picked = [];
  const add = (id, role) => {
    if (!images.has(id) && id !== previousFrame) throw new Error(`Нет изображения ${id} для опорного кадра`);
    const existing = picked.find(item => item.id === id);
    if (existing && existing.role !== role) throw new Error(`Одно изображение ${id} назначено несовместимым ролям`);
    if (!existing) picked.push({ id, role });
  };
  try {
    const base = references.find(ref => ref.kind === 'image' && ['Кадр сцены', 'Окружение', 'Композиция'].includes(ref.role));
    // The previous shot's last frame or the scene's accepted establishing frame keeps the same room; a generic place photo is the fallback.
    if (previousFrame) add(previousFrame, 'Окружение');
    else if (base) add(base.id, base.role);
    for (const actor of spec.characters) {
      if (!actor.reference_asset_ids.length) throw new Error(`Нет фото героя ${actor.id}`);
      for (const id of actor.reference_asset_ids) {
        const ref = images.get(id);
        if (!ref || !['Лицо героя', 'Персонаж целиком', 'Костюм'].includes(ref.role))
          throw new Error(`Для ${actor.id} нужно явно назначенное фото героя или костюма`);
        add(id, ref.role);
      }
    }
    for (const id of spec.visual_profile?.reference_asset_ids || []) add(id, 'Стиль');
    if (picked.length > maxImages) throw new Error(`Для кадра назначено ${picked.length} фото, лимит выбранной модели — ${maxImages}. Выбери совместимый набор; обязательные референсы не отброшены.`);
    const camera = [cut.sceneShot?.framing, cut.sceneShot?.angle,
      cut.sceneShot?.lens_mm ? `${cut.sceneShot.lens_mm} mm lens` : ''].filter(Boolean).join(', ');
    const direction = { ...spec.visual_profile?.director_config, ...spec.shot.director_config };
    const parameters = Object.entries(direction).flatMap(([category, config]) => Object.entries(config.parameters || {})
      .filter(([, value]) => value !== '' && value !== null && value !== undefined)
      .map(([key, value]) => `${category}: ${key.replaceAll('_', ' ')} = ${value}`)).join('; ');
    const prompt = [
      `Opening image of one shot, before its later actions. ${spec.shot.first_frame_description || spec.shot.state_in}`,
      spec.shot.spatial_layout ? `Fixed geography: ${spec.shot.spatial_layout}` : '',
      ...spec.characters.map(actor => `${actor.id}: ${actor.identity}. Identity references: ${actor.reference_asset_ids.map(id => `image ${picked.findIndex(item => item.id === id) + 1}`).join(', ')}. ${actor.physical_constraints || ''}`),
      camera ? `Camera: ${camera}.` : '',
      parameters ? `Shared visual direction: ${parameters}.` : '',
      spec.visual_profile?.frame_direction ? `Visual treatment: ${spec.visual_profile.frame_direction}` : '',
      sceneStyle ? `Scene visual continuity: ${sceneStyle}` : '',
      'Render one coherent frame. Preserve each named character from its assigned images; style and location references do not replace character identities.',
    ].filter(Boolean).join('\n');
    if (prompt.length > 3000) throw new Error('Описание опорного кадра слишком длинное. Сократи постановку; текст не был обрезан.');
    return { ok: true, prompt, asset_ids: picked.map(item => item.id), roles: picked.map(item => item.role),
      size: aspect === '9:16' ? '768x1344' : aspect === '1:1' ? '1024x1024' : '1344x768', profile: 'qwen_edit_2511',
      role: 'Кадр сцены', keep_identity: true, framing: 'center' };
  } catch (error) { return { ok: false, message: error.message }; }
}
