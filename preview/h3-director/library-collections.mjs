// Library collections = the franchises in this Studio (GET /api/franchises), filled by
// loadFranchiseMemberships; a fresh install starts with none. [id, label] pairs.
export const COLLECTIONS = [];

const seasonLook = name => /хэллоуин|halloween/.test(name) ? 'Хэллоуин' : /рождество|christmas/.test(name) ? 'Рождество' : /супергерой|superhero/.test(name) ? 'Супергерой' : '';

// A file enters a franchise only from what the user saved for it (or from the franchise
// passport/entities below); file names never assign a franchise on their own.
export function inferredCollection(asset, saved = {}) {
  return { franchise: saved.franchise || '', characterName: saved.characterName || '', look: saved.look || '' };
}

// Location cards titled "Референсы для генерации · Улицы" hold stock photos of the
// world; each card becomes one sub-path inside a single library section.
export const GENERATION_REFS = 'Референсы для генерации';
const generationRefsPath = title => {
  const [head, ...rest] = String(title || '').split('·').map(part => part.trim());
  return head === GENERATION_REFS && rest.length ? rest.join(' · ') : '';
};


export async function loadFranchiseMemberships(apiJson, assets = new Map()) {
  const memberships = new Map();
  const franchises = await apiJson('/api/franchises');
  COLLECTIONS.splice(0, COLLECTIONS.length, ...franchises.map(franchise => [String(franchise.id), franchise.name]));
  const groups = await Promise.all(franchises
    .map(async franchise => ({ franchise,
      entities: await apiJson(`/api/franchises/${encodeURIComponent(franchise.id)}/entities`),
      profile: await apiJson(`/api/franchises/${encodeURIComponent(franchise.id)}/profile`) })));
  // Merge in source order, even when requests complete in a different order.
  for (const { franchise, entities, profile } of groups) {
    const label = franchise.name;
    for (const role of profile?.roles || []) for (const ref of role.assets || []) {
      const assetId = ref.asset_id;
      const defaultRole = { character: 'Персонаж целиком', costume: 'Костюм', location: 'Окружение',
        prop: 'Предмет', weapon: 'Предмет', style: 'Стиль', light: 'Свет', color: 'Цвет' }[role.kind] || '';
      memberships.set(assetId, [...(memberships.get(assetId) || []), { franchise: label,
        characterName: role.owner || (role.kind === 'character' ? role.name : ''),
        preferredRole: ref.role || defaultRole, title: role.name, state: 'approved', source: 'profile' }]);
    }
    for (const entity of entities) {
      const characterName = entity.type === 'character' ? entity.title.split('·')[0].trim().toLocaleLowerCase('ru').replace(/^./u, c => c.toLocaleUpperCase('ru')) : '';
      const refsPath = entity.type === 'location' ? generationRefsPath(entity.title) : '';
      for (const [index, assetId] of (entity.asset_ids || []).entries()) {
        if (entity.type === 'idea') {
          memberships.set(assetId, [...(memberships.get(assetId) || []), { franchise: label,
            characterName: '', title: entity.title, state: entity.state, entityId: entity.id,
            preferredRole: 'Стиль', look: '', category: `Идеи / ${entity.title}`,
            displayName: `${entity.title} · визуальный референс${entity.asset_ids.length > 1 ? ` ${index + 1}` : ''}` }]);
          continue;
        }
        if (entity.type === 'scene' || entity.type === 'rule') {
          memberships.set(assetId, [...(memberships.get(assetId) || []), { franchise: label,
            characterName: '', title: entity.title, state: entity.state, entityId: entity.id,
            preferredRole: entity.type === 'scene' ? 'Кадр сцены' : 'Референс', look: '',
            category: `${entity.type === 'scene' ? 'Сцены' : 'Правила'} / ${entity.title}`,
            displayName: assets.get(assetId)?.original_name || entity.title }]);
          continue;
        }
        if (refsPath) {
          const role = assets.get(assetId)?.role;
          memberships.set(assetId, [...(memberships.get(assetId) || []), { franchise: label, characterName: '', title: entity.title,
            state: entity.state, preferredRole: ['Предмет', 'Окружение'].includes(role) ? role : 'Окружение', look: '',
            category: `${GENERATION_REFS} / ${refsPath}`, displayName: `${refsPath} · ${String(index + 1).padStart(2, '0')}` }]);
          continue;
        }
        const originalName = String(assets.get(assetId)?.original_name || '');
        const namedSeason = seasonLook(originalName.toLocaleLowerCase('ru'));
        const namedLook = namedSeason;
        const isWhitePortrait = /· портрет · белый фон\.png$/iu.test(originalName);
        const savedRole = assets.get(assetId)?.role || '';
        const preferredRole = entity.type === 'location' ? 'Окружение'
          : entity.type === 'costume' ? 'Костюм'
          : isWhitePortrait ? 'Лицо героя'
          : /(?:costume|outfit|wardrobe|костюм|наряд|образ)/iu.test(originalName) ? 'Костюм'
          : savedRole === 'Костюм' ? 'Костюм'
          : savedRole === 'Персонаж целиком' ? 'Персонаж целиком'
          : savedRole === 'Лицо героя' ? 'Лицо героя'
          : /(?:portrait|face|портрет|лицо)/iu.test(originalName) ? 'Лицо героя'
          : /(?:body|full|тело|полнорост)/iu.test(originalName) ? 'Персонаж целиком'
          : namedSeason && originalName.toLocaleLowerCase('ru').startsWith(characterName.toLocaleLowerCase('ru') + ' · ') ? 'Костюм'
          : 'Референс';
        const entry = { franchise: label, characterName, title: entity.title, state: entity.state,
          preferredRole, look: preferredRole === 'Костюм' ? namedLook : '', source: 'entity' };
        if (characterName) {
          const original = originalName;
          const namedCostume = preferredRole === 'Костюм' && new RegExp(`^${characterName} · `, 'i').test(original);
          entry.displayName = namedCostume ? original.replace(/\.[^.]+$/, '') : `${characterName} · ${preferredRole === 'Костюм' ? 'основной костюм' : preferredRole === 'Персонаж целиком' ? 'тело' : 'портрет'}`;
        }
        memberships.set(assetId, [...(memberships.get(assetId) || []), entry]);
      }
    }
  }
  return memberships;
}

export function collectionId(franchise) {
  return COLLECTIONS.find(([, label]) => label === franchise)?.[0] || '';
}

export function sectionFor(asset, info) {
  if (asset.kind === 'video') return 'Видео и движение';
  if (asset.kind === 'audio') return 'Голос и звук';
  if (String(info.category || '').startsWith('Идеи / ')) return 'Идеи';
  if (String(info.category || '').startsWith('Сцены / ')) return 'Сцены и раскадровки';
  if (String(info.category || '').startsWith('Правила / ')) return 'Правила и эталоны';
  if (String(info.category || '').startsWith(`${GENERATION_REFS} / `)) return GENERATION_REFS;
  const role = info.preferredRole || asset.role;
  if (role === 'Костюм') return info.characterName ? `${info.characterName} · костюмы` : 'Костюмы';
  if (role === 'Окружение') return 'Локации';
  if (role === 'Лицо героя') return `${info.characterName || 'Герой'} · лицо`;
  if (role === 'Персонаж целиком') return `${info.characterName || 'Герой'} · тело`;
  if (info.characterName) return `${info.characterName} · другие материалы`;
  return 'Другие материалы';
}
