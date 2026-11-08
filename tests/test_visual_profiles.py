from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio.production_spec import VisualProfile
from local_studio.visual_profiles import make_router, visual_profiles


def test_catalog_profiles_are_strict_specs_with_evidence_limits():
    entries = visual_profiles()
    assert len(entries) == len({entry["visual_profile"]["id"] for entry in entries}) == 4
    for entry in entries:
        profile = VisualProfile.model_validate(entry["visual_profile"])
        assert profile.sources and profile.frame_direction and profile.video_direction
        assert not profile.director_config and not profile.reference_asset_ids
        assert entry["status"] == "authored_method_not_render_verified"
        assert entry["source_note"] and entry["evidence_limit"]


def test_read_only_profile_routes_and_missing_profile():
    app = FastAPI()
    app.include_router(make_router())
    client = TestClient(app)
    rows = client.get("/api/visual-profiles").json()
    profile_id = rows[0]["visual_profile"]["id"]
    assert client.get(f"/api/visual-profiles/{profile_id}").json() == rows[0]
    assert client.get("/api/visual-profiles/missing").status_code == 404
    assert client.post(f"/api/visual-profiles/{profile_id}").status_code == 405
