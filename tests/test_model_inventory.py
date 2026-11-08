import hashlib
import json
import os

import pytest

from app.model_inventory import attest_weights, check_weight, write_inventory


def test_attested_digest_is_invalid_after_edit_even_if_mtime_is_restored(tmp_path):
    path = tmp_path / 'model.safetensors'
    path.write_bytes(b'weights1')
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    inventory = attest_weights(tmp_path, {path.name: expected})
    dest = tmp_path / 'inventory.json'
    write_inventory(dest, inventory)
    saved = json.loads(dest.read_text())
    assert check_weight(tmp_path, saved, path.name)['sha256'] == expected
    stat = path.stat()
    path.write_bytes(b'weights2')
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = check_weight(tmp_path, saved, path.name)
    assert result['state'] == 'changed_or_missing' and result['sha256'] is None
    with pytest.raises(ValueError, match='SHA-256'):
        attest_weights(tmp_path, {path.name: expected})
    assert check_weight(tmp_path, saved, 'unknown')['state'] == 'unknown'
    with pytest.raises(ValueError, match='хранилище'):
        check_weight(tmp_path, saved, '../outside')
