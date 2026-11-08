// A chat hands its library image to one explicit shot; it remains subject to the normal frame review.
export async function setChatStartFrame({ studio, storyboard, sceneId, shotId, assetId, fetcher = fetch }) {
  const check = () => {
    if (studio.sceneState().activeSceneId !== sceneId) throw new Error('Открыта другая сцена');
    const build = storyboard?.state?.();
    if (build?.running || ['running', 'awaiting'].includes(build?.onMac?.state))
      throw new Error('Сцена уже собирается: сначала останови её сборку');
    const cut = studio.cuts().find((item) => item.id === shotId);
    if (!cut) throw new Error('Шот не найден');
    if (cut.shotJobId || cut.keyframeJobId || cut.keyframeJobIds?.length || cut.takes?.length)
      throw new Error('В шоте уже есть работа: сначала сверь его кадры и дубли');
    return cut;
  };
  check();
  const response = await fetcher('/api/assets');
  if (!response.ok) throw new Error('Библиотека Studio недоступна');
  const asset = (await response.json()).find((item) => item.asset_id === assetId && item.kind === 'image');
  if (!asset) throw new Error('Изображение не найдено в библиотеке');
  check();
  if (!storyboard?.setStartFrame) throw new Error('Раскадровка ещё не загружена');
  storyboard.setStartFrame(shotId, asset, 'library');
  studio.editCuts((list) => list.map((cut) => {
    if (cut.id !== shotId) return cut;
    const next = { ...cut };
    delete next.frameAcceptance; delete next.frameReview;
    return next;
  }));
  await studio.saveDraft();
  return { scene_id: sceneId, shot_id: shotId, asset_id: assetId, accepted: false };
}
