// Server workbench: a pasted SSH command from Vast/RunPod («ssh -p 41973 root@203.0.113.10 -L 8080:localhost:8080»,
// «root@203.0.113.10 -p 41973», «203.0.113.10:41973») becomes host + port, so a new server needs one paste.
export function parseSshTarget(text) {
  const value = String(text || '').trim();
  const host = value.match(/(?:^|\s|@)((?:\d{1,3}\.){3}\d{1,3}|[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)+)(?::(\d{1,5}))?(?=\s|$)/i);
  if (!host) return null;
  const flag = value.match(/(?:^|\s)-p\s*(\d{1,5})(?=\s|$)/);
  const port = Number(flag?.[1] || host[2] || 22);
  return port >= 1 && port <= 65535 ? { host: host[1], port } : null;
}
