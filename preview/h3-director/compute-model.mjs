export function formatUsd(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `$${number.toFixed(2)}` : '—';
}

export function instanceControls(instance) {
  const status = String(instance?.actual_status || '').toLowerCase();
  const currentTunnelTarget = instance?.is_current_tunnel_target === true;
  const deployment = String(instance?.deployment_state || 'not_started');
  const active = ['running', 'loading', 'starting', 'creating'].includes(status);
  const stopped = status === 'stopped';
  const deploymentInProgress = ['installing', 'needs_reconciliation'].includes(deployment);
  return {
    active,
    stopped,
    currentTunnelTarget,
    canStart: stopped,
    canStop: active && !deploymentInProgress,
    canDestroy: (!currentTunnelTarget || stopped) && deployment !== 'ready' && !deploymentInProgress,
    canDeploy: !currentTunnelTarget && !deploymentInProgress && deployment !== 'ready' && active,
  };
}
