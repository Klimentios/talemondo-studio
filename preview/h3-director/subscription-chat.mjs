// Subscription transport is intentionally separate from agent execution.
export function referenceIds(text, profile) {
  const words = String(text).toLocaleLowerCase();
  const ids = new Set();
  for (const role of profile?.roles || []) {
    const named = [role.name, ...(role.aliases || [])].some((name) => name && words.includes(name.toLocaleLowerCase()));
    if (!named) continue;
    const refs = role.assets || [];
    const face = /лиц|портрет|морда/i.test(text);
    const selected = refs.find((a) => a.role === (face ? 'Лицо героя' : 'Персонаж целиком')) || refs[0];
    if (selected?.asset_id) ids.add(selected.asset_id);
  }
  return [...ids];
}
export async function subscriptionTurn({ chat, text, mode, attachments, franchiseId, requestId, fetcher = fetch }) {
  const call = async (url, body) => {
    const response = await fetcher(url, body === undefined ? {} : { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (response.status === 404 && url === '/api/subscription-bridge/status') throw new Error('Служба Studio ещё не загрузила коннектор. Нужен перезапуск службы; агентский режим доступен отдельно.');
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  };
  const status = await call('/api/subscription-bridge/status');
  if (!status.connected) throw new Error('Включите расширение TaleMondo Subscription Connector в Chrome. Сообщение остаётся в Studio.');
  const fid = chat.franchise_id || franchiseId;
  const profile = fid ? await call(`/api/franchises/${encodeURIComponent(fid)}/profile`) : null;
  const entities = fid ? await call(`/api/franchises/${encodeURIComponent(fid)}/entities`) : [];
  const namedRoles = (profile?.roles || []).filter((role) => [role.name, ...(role.aliases || [])]
    .some((name) => name && text.toLocaleLowerCase().includes(name.toLocaleLowerCase())));
  const selectedEntities = entities.filter((entity) => namedRoles.some((r) => r.entity_id === entity.id) ||
    (entity.type === 'idea' && text.toLocaleLowerCase().includes(entity.title.toLocaleLowerCase())));
  const ids = [...new Set([...attachments.map((a) => a.asset_id), ...referenceIds(text, profile)])];
  if (ids.length > 6) throw new Error('Не больше шести референсов для подписочного чата');
  return call('/api/subscription-bridge/turns', { request_id: requestId, chat_id: chat.id, text, mode,
    attachments: ids, context: { franchise_id: fid || null, profile, entities: selectedEntities,
      ideas: entities.filter((e) => e.type === 'idea').map(({ id, title, state }) => ({ id, title, state })) } });
}
