"""Audit 42.3A №21: concats/exports keep the newest 20, never one the saved draft or montage uses."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from local_studio.retention import KEEP, prune
from local_studio.store import StudioStore


class RetentionTests(unittest.TestCase):
    def test_keeps_newest_and_referenced(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            directory = store.root / "storyboards"
            directory.mkdir()
            ids = []
            now = time.time()
            for index in range(KEEP + 5):
                concat_id = uuid.uuid4().hex
                for suffix in (".mp4", ".json"):
                    path = directory / f"{concat_id}{suffix}"
                    path.write_text("x")
                    os.utime(path, (now - 1000 + index, now - 1000 + index))
                ids.append(concat_id)
            oldest, second, third = ids[:3]
            store.save_draft({"storyboard": {"lastConcat": {"concat_id": oldest}},
                              "filmAssembly": {"url": f"/api/storyboard/concat/{second}/file"}})
            montage = store.root / "montages"
            montage.mkdir()
            (montage / "a.json").write_text(f'{{"preview": ["{third}"]}}')
            removed = prune(directory, store)
            self.assertEqual(len(removed), 2)
            self.assertEqual(set(removed), set(ids[3:5]))
            for concat_id in (oldest, second, third, *ids[-KEEP:]):
                self.assertTrue((directory / f"{concat_id}.mp4").is_file(), concat_id)
            self.assertFalse((directory / f"{ids[3]}.json").exists())
            self.assertEqual(prune(directory, store), [])

    def test_running_export_is_never_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            directory = store.root / "edl_renders"
            directory.mkdir()
            names = [str(uuid.uuid4()) for _ in range(KEEP + 1)]
            for index, name in enumerate(names):
                path = directory / f"{name}.mp4"
                path.write_text("x")
                os.utime(path, (index, index))
            self.assertEqual(prune(directory, store, busy={names[0]}), [])


if __name__ == "__main__":
    unittest.main()
