import assert from 'node:assert/strict';
import test from 'node:test';
import { formatUsd, instanceControls } from './compute-model.mjs';

test('formats provider prices as USD per hour input', () => {
  assert.equal(formatUsd(1.5), '$1.50');
  assert.equal(formatUsd('2.41'), '$2.41');
  assert.equal(formatUsd(undefined), '—');
});

test('current tunnel server can be explicitly stopped, but cannot be destroyed or redeployed while running', () => {
  assert.deepEqual(instanceControls({actual_status:'running', is_current_tunnel_target:true}), {
    active:true, stopped:false, currentTunnelTarget:true, canStart:false, canStop:true,
    canDestroy:false, canDeploy:false,
  });
});

test('stopped current tunnel server can be deleted after provider confirms stopped', () => {
  const controls = instanceControls({actual_status:'stopped', is_current_tunnel_target:true});
  assert.equal(controls.canDestroy, true);
  assert.equal(controls.canStart, true);
});

test('stopped server can be started and cannot be installed before it is running', () => {
  const controls = instanceControls({actual_status:'stopped'});
  assert.equal(controls.canStart, true);
  assert.equal(controls.canStop, false);
  assert.equal(controls.canDeploy, false);
});

test('running replacement server offers install and lifecycle controls', () => {
  const controls = instanceControls({actual_status:'running', deployment_state:'not_started'});
  assert.equal(controls.canDeploy, true);
  assert.equal(controls.canStop, true);
  assert.equal(controls.canDestroy, true);
});

test('installation and reconciliation prevent a second deployment', () => {
  for (const deployment_state of ['installing', 'needs_reconciliation']) {
    const controls = instanceControls({actual_status:'running', deployment_state});
    assert.equal(controls.canDeploy, false);
    assert.equal(controls.canStop, false);
    assert.equal(controls.canDestroy, false);
  }
  const ready = instanceControls({actual_status:'running', deployment_state:'ready'});
  assert.equal(ready.canDeploy, false);
  assert.equal(ready.canStop, true);
  assert.equal(ready.canDestroy, false);
});
