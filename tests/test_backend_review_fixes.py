"""Backend review fixes: old upload temps are cleaned, production buttons never hang on the dispatch lock."""
import asyncio
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from local_studio import dispatcher
from local_studio.production import _held


def test_only_old_upload_temps_are_removed(tmp_path):
    studio = SimpleNamespace(results_dir=tmp_path / "results", assets_dir=tmp_path / "assets")
    studio.results_dir.mkdir()
    studio.assets_dir.mkdir()
    old, fresh = studio.assets_dir / "upload-old", studio.assets_dir / "upload-new"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    stale = time.time() - dispatcher.STALE_UPLOAD_S - 60
    os.utime(old, (stale, stale))
    assert dispatcher.remove_stale_partials(studio) == 1
    assert not old.exists() and fresh.exists()


def test_production_lock_wait_gives_up_with_503(monkeypatch):
    monkeypatch.setattr("local_studio.production.LOCK_WAIT_S", 0.05)

    async def run():
        lock = asyncio.Lock()
        await lock.acquire()  # the dispatcher is busy uploading
        with pytest.raises(HTTPException) as caught:
            async with _held(lock):
                pass
        assert caught.value.status_code == 503
        lock.release()
        async with _held(lock):
            assert lock.locked()
        assert not lock.locked()

    asyncio.run(run())
