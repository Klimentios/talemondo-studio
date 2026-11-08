import asyncio
from types import SimpleNamespace

import pytest

from local_studio.context_dispatch import context_dispatch_loop


def test_agent_context_runs_without_gpu_after_reconciliation_and_releases_lock(tmp_path):
    calls = []
    class Queue:
        studio = SimpleNamespace(root=tmp_path)
        async def reconcile(self):
            calls.append('reconcile')
        async def run_once(self):
            calls.append('local_agent')
            raise asyncio.CancelledError
    for _ in range(2):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(context_dispatch_loop(Queue()))
    assert calls == ['reconcile', 'local_agent', 'reconcile', 'local_agent']
