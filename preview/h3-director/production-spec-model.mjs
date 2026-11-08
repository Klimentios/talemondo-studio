// Build an editable draft from explicitly assigned identities, without inventing
// acting, anatomy or scientific behavior rules. It becomes executable only after validation.
export function newProductionSpec(cut, references, franchiseId = '') {
  const heroes = new Map();
  for (const ref of references || []) {
    const owner = String(ref.owner || '').trim();
    if (ref.kind !== 'image' || !['Персонаж целиком', 'Лицо героя'].includes(ref.role) || !owner) continue;
    if (!heroes.has(owner)) heroes.set(owner, { id: owner, revision: 1, identity: owner,
      reference_asset_ids: [], physical_constraints: '', individual_behavior: '' });
    heroes.get(owner).reference_asset_ids.push(ref.id);
  }
  if (!heroes.size) throw new Error('Назначь имя героя фото «Персонаж целиком» или «Лицо героя» в материалах сцены.');
  return { schema_version: 1, franchise_id: franchiseId, characters: [...heroes.values()], behavior_patterns: [],
    shot: { id: cut.id, revision: 0, duration: Number(cut.duration), purpose: cut.sceneShot?.purpose || '',
      state_in: cut.sceneShot?.state_before || '', state_out: cut.sceneShot?.state_after || '', director_config: {},
      beats: [{ id: 'b1', actor_ids: [...heroes.keys()], start: 0, end: Number(cut.duration), cause: '',
        observed_body_action: '', effect: '', behavior_pattern_ids: [] }] } };
}
