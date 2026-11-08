// Local projects remain usable while a remote GPU health check is waiting on SSH.
export function startStudioLoad(apiJson) {
  const health = apiJson('/api/health').catch(() => ({ gpu_reachable: false, style_adapters: [] }));
  const workspace = Promise.all([apiJson('/api/assets'), apiJson('/api/draft')]);
  return { health, workspace };
}
