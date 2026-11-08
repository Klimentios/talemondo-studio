import hashlib
from types import SimpleNamespace

import pytest

from local_studio import production_review as review


def test_review_requires_current_hash_full_view_audio_and_technical_pass(tmp_path, monkeypatch):
    output = tmp_path / 'result.mp4'
    output.write_bytes(b'video')
    digest = hashlib.sha256(b'video').hexdigest()
    store = SimpleNamespace(root=tmp_path, get_job=lambda _: {
        'state': 'completed', 'output_path': str(output), 'output_sha256': digest})
    monkeypatch.setattr(review, 'probe', lambda _: (5.0, True))
    payload = dict(output_sha256=digest, reviewer='producer', evidence='Full continuous playback with sound',
                   viewed_ranges=[(0, 5)], audio_reviewed=True)
    with pytest.raises(ValueError, match='technical_pass'):
        review.record_review(store, 'j1', review.ReviewInput(verdict='creative_accepted', **payload))
    review.record_review(store, 'j1', review.ReviewInput(verdict='technical_pass', **payload))
    with pytest.raises(ValueError, match='просмотр всего'):
        review.record_review(store, 'j1', review.ReviewInput(verdict='creative_accepted', **{**payload, 'viewed_ranges': [(0, 2)]}))
    with pytest.raises(ValueError, match='прослушивание'):
        review.record_review(store, 'j1', review.ReviewInput(verdict='creative_accepted', **{**payload, 'audio_reviewed': False}))
    review.record_review(store, 'j1', review.ReviewInput(verdict='creative_accepted', **payload))
    assert review.require_creative_acceptance(store, 'j1')['verdict'] == 'creative_accepted'
    review.record_review(store, 'j1', review.ReviewInput(verdict='rejected', **payload))
    with pytest.raises(ValueError, match='приёмки'):
        review.require_creative_acceptance(store, 'j1')
    with pytest.raises(ValueError, match='technical_pass'):
        review.record_review(store, 'j1', review.ReviewInput(verdict='creative_accepted', **payload))
    review.record_review(store, 'j1', review.ReviewInput(verdict='technical_pass', **payload))
    review.record_review(store, 'j1', review.ReviewInput(verdict='creative_accepted', **payload))
    assert review.require_creative_acceptance(store, 'j1')['verdict'] == 'creative_accepted'
    output.write_bytes(b'changed')
    with pytest.raises(ValueError, match='изменён'):
        review.output_digest(store, 'j1')


@pytest.mark.parametrize('kind', ['concat', 'edl'])
def test_assembly_review_uses_exact_artifact_and_detects_later_change(tmp_path, monkeypatch, kind):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    uid = 'a' * 32
    folder = tmp_path / ('storyboards' if kind == 'concat' else 'edl_renders')
    folder.mkdir()
    receipt = folder / (uid + ('.json' if kind == 'concat' else '.state'))
    receipt.write_text(json.dumps({'state': 'completed', 'draft': False}))
    output = folder / (uid + '.mp4')
    output.write_bytes(b'assembled film')
    store = SimpleNamespace(root=tmp_path, get_job=lambda _: None)
    app = FastAPI()
    app.include_router(review.make_router(store))
    client = TestClient(app)
    url = f'/api/production/artifacts/{kind}/{uid}/reviews'
    digest = client.get(url).json()['output_sha256']
    monkeypatch.setattr(review, 'probe', lambda _: (10.0, True))
    payload = dict(output_sha256=digest, reviewer='producer', evidence='Full playback with sound',
                   viewed_ranges=[[0, 10]], audio_reviewed=True)
    assert client.post(url, json={**payload, 'verdict': 'creative_accepted'}).status_code == 409
    assert client.post(url, json={**payload, 'verdict': 'technical_pass'}).status_code == 201
    assert client.post(url, json={**payload, 'verdict': 'creative_accepted'}).status_code == 201
    assert review.require_creative_acceptance(store, uid, kind)['artifact_kind'] == kind
    assert review.load_reviews(store, uid) == []  # Same ID has no job acceptance.
    output.write_bytes(b'edited film')
    with pytest.raises(ValueError, match='приёмки'):
        review.require_creative_acceptance(store, uid, kind)
    if kind == 'edl':
        receipt.write_text(json.dumps({'state': 'completed', 'draft': True}))
        assert client.get(url).status_code == 409
    assert client.get(f'/api/production/artifacts/{kind}/invalid/reviews').status_code == 409


@pytest.mark.parametrize('kind', ['motion', 'asset'])
def test_motion_and_imported_video_share_hash_bound_acceptance(tmp_path, monkeypatch, kind):
    from local_studio.store import StudioStore
    from local_studio.motion_jobs import MotionQueue
    store = StudioStore(tmp_path)
    output = (store.assets_dir if kind == 'asset' else store.results_dir) / 'film.mp4'
    output.write_bytes(b'reviewed film')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    if kind == 'asset':
        asset = store.add_asset(kind='video', role='Исходное видео', original_name='film.mp4', mime='video/mp4',
                                size=output.stat().st_size, sha256=digest, path=output)
        uid = asset['asset_id']
    else:
        MotionQueue(store, '')  # Create its real durable schema; no dispatch or GPU.
        uid = 'motion-test'
        with store.connect() as db:
            db.execute("INSERT INTO motion_jobs(motion_id,scene_id,revision,plan_json,manifest_json,state,output_path,output_sha256) "
                       "VALUES (?,?,1,'{}','{}','completed',?,?)", (uid, 'scene-1', str(output), digest))
    monkeypatch.setattr(review, 'probe', lambda _: (5.0, True))
    evidence = dict(output_sha256=digest, reviewer='producer', evidence='Full playback with audio',
                    viewed_ranges=[(0, 5)], audio_reviewed=True)
    for verdict in ('technical_pass', 'creative_accepted'):
        review.record_review(store, uid, review.ReviewInput(verdict=verdict, **evidence), kind)
    assert review.require_creative_acceptance(store, uid, kind)['artifact_kind'] == kind
    assert review.load_reviews(store, uid) == []
    output.write_bytes(b'changed')
    with pytest.raises(ValueError, match='изменён'):
        review.require_creative_acceptance(store, uid, kind)
