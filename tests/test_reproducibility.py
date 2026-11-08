import hashlib
import json
from types import SimpleNamespace

from local_studio.reproducibility import job_reproducibility


def test_job_recipe_detects_reference_drift_and_keeps_fingerprint_stable(tmp_path):
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"original")
    result = tmp_path / "result.mp4"
    result.write_bytes(b"video")
    expected = hashlib.sha256(b"original").hexdigest()
    job = {"job_id": "j1", "state": "completed", "original_prompt": "scene",
           "compiled_prompt": "compiled scene", "settings_json": json.dumps({"seed": 42}),
           "manifest_json": json.dumps({"images": [{"asset_id": "a1", "sha256": expected, "role": "hero"}]}),
           "mapping_json": "{}", "output_path": str(result)}
    store = SimpleNamespace(get_asset=lambda asset_id: {"path": str(reference)} if asset_id == "a1" else None)
    first = job_reproducibility(store, job)
    assert first["references_verified"] and first["output_sha256"]
    reference.write_bytes(b"changed")
    second = job_reproducibility(store, job)
    assert second["input_fingerprint_sha256"] == first["input_fingerprint_sha256"]
    assert second["blockers"] == ["reference_changed_or_missing:a1"]


def test_completed_receipt_detects_later_output_change(tmp_path):
    from local_studio.store import StudioStore
    store = StudioStore(tmp_path)
    image = store.assets_dir / 'image.png'
    image.write_bytes(b'image')
    asset = store.add_asset(kind='image', role='Лицо героя', original_name='image.png', mime='image/png',
                            size=5, sha256=hashlib.sha256(b'image').hexdigest(), path=image)
    job = store.create_job(prompt="<Picture 1> looks outside", asset_ids=[asset['asset_id']], settings={})
    output = store.results_dir / 'result.mp4'
    output.write_bytes(b'original')
    job = store.transition(job['job_id'], 'completed', output_path=str(output))
    assert job['output_sha256'] == hashlib.sha256(b'original').hexdigest()
    output.write_bytes(b'changed')
    report = job_reproducibility(store, job)
    assert report['output_integrity'] == 'changed'
    assert 'output_changed' in report['blockers']
