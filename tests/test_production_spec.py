from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio.production_spec import make_router


def client():
    app = FastAPI()
    app.include_router(make_router())
    return TestClient(app)


def payload():
    return {"schema_version": 1, "franchise_id": "f1",
            "characters": [{"id": "cat", "revision": 1, "identity": "Approved character"}],
            "shot": {"id": "s1", "revision": 1, "duration": 3.0, "purpose": "Notice the object",
                     "state_in": "Unaware", "state_out": "Watching object",
                     "beats": [{"id": "b1", "actor_ids": ["cat"], "start": 0.0, "end": 2.0,
                                "cause": "Object moves", "observed_body_action": "Turns head toward it",
                                "effect": "Object is noticed"}]}}


def test_spec_validation_is_explicitly_not_generation_or_approval():
    result = client().post("/api/production/spec/validate", json=payload())
    assert result.status_code == 200
    assert result.json()["valid"] and not result.json()["creative_approved"]
    assert result.json()["scope"] == "description_validation_only"


def test_unknown_actor_bad_timing_and_causality_are_reported():
    value = payload()
    value["shot"]["beats"][0].update(actor_ids=["missing"], end=4.0, cause_beat_id="b1")
    result = client().post("/api/production/spec/validate", json=value).json()
    assert {item["code"] for item in result["issues"]} == {
        "unknown_beat_actor", "beat_outside_duration", "invalid_cause_reference"}
    assert not result["valid"]


def test_verified_behavior_needs_evidence_and_camera_uses_existing_schema():
    value = payload()
    value["behavior_patterns"] = [{"id": "p1", "actor_id": "cat", "trigger": "Object moves",
                                  "observed_body_action": "Turns head", "status": "verified"}]
    result = client().post("/api/production/spec/validate", json=value).json()
    assert result["issues"][0]["code"] == "verified_behavior_requires_evidence"
    value["shot"]["director_config"] = {"camera": {"imaginary_control": "yes"}}
    assert client().post("/api/production/spec/validate", json=value).status_code == 422


def test_library_references_checked_and_spec_fingerprint_changes():
    from types import SimpleNamespace
    app = FastAPI()
    app.include_router(make_router(SimpleNamespace(get_asset=lambda _: None)))
    c = TestClient(app)
    value = payload()
    first = c.post('/api/production/spec/validate', json=value).json()
    value['characters'][0]['reference_asset_ids'] = ['missing']
    second = c.post('/api/production/spec/validate', json=value).json()
    assert not second['valid']
    assert second['issues'] == [{'code': 'reference_asset_missing', 'item_id': 'missing'}]
    assert first['spec_sha256'] != second['spec_sha256']
    value = payload()
    del value["shot"]["state_out"]
    assert client().post("/api/production/spec/validate", json=value).status_code == 422
