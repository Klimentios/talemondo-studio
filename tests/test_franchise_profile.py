import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import franchise_profile, franchises
from local_studio.franchise_profile import names_of

ASSETS = {
    "vega-face": {"asset_id": "vega-face", "kind": "image", "role": "Лицо героя"},
    "vega-body": {"asset_id": "vega-body", "kind": "image", "role": "Референс"},
    "adam-body": {"asset_id": "adam-body", "kind": "image", "role": "Персонаж целиком"},
    "hoodie": {"asset_id": "hoodie", "kind": "image", "role": "Костюм"},
    "kitchen": {"asset_id": "kitchen", "kind": "image", "role": "Окружение"},
    "sword": {"asset_id": "sword", "kind": "image", "role": "Предмет"},
    "style": {"asset_id": "style", "kind": "image", "role": "Стиль"},
    "music": {"asset_id": "music", "kind": "audio", "role": "Музыка"},
    **{f"x{i}": {"asset_id": f"x{i}", "kind": "image", "role": "Окружение"} for i in range(12)},
}


class FakeStore:
    def __init__(self, root):
        self.root = root

    def get_asset(self, asset_id):
        return ASSETS.get(asset_id)


@pytest.fixture
def api(tmp_path):
    store = FakeStore(tmp_path)
    app = FastAPI()
    app.include_router(franchises.make_router(store))
    app.include_router(franchise_profile.make_router(store))
    client = TestClient(app)
    fid = client.post("/api/franchises", json={"name": "K&A"}).json()["id"]
    for card in ({"type": "character", "title": "ВЕГА · эталон внешности", "asset_ids": ["vega-face", "vega-body"]},
                 {"type": "character", "title": "АДАМ", "asset_ids": ["adam-body"]},
                 {"type": "location", "title": "Кухня", "asset_ids": ["kitchen"]},
                 {"type": "rule", "title": "Правило", "asset_ids": ["style"]}):
        assert client.post(f"/api/franchises/{fid}/entities", json=card).status_code == 201
    return client, fid


PROFILE = {
    "revision": 0,
    "look": {"style": "Premium 3D animated feature look, soft subsurface fur.", "palette": "warm amber, teal night"},
    "director_defaults": {"lighting": {"scheme": "rembrandt"}},
    "camera": {"lens_min": 24, "lens_max": 85, "moves": ["dolly", "pan"], "signature": "motivated, eye-level for Vega"},
    "edit": {"transitions": ["cut", "match_cut", "whip_pan"], "music_asset_id": "music", "fade_out": 0.8},
    "roles": [
        {"name": "Adam hoodie", "kind": "costume", "owner": "ADAM", "with_owner": True, "assets": [{"asset_id": "hoodie"}]},
        {"name": "Laser sword", "kind": "weapon", "aliases": ["sword"], "owner": "VEGA", "assets": [{"asset_id": "sword"}],
         "prompt": "VEGA's fantasy laser sword glows cyan."},
        {"name": "Look plate", "kind": "style", "always": True, "assets": [{"asset_id": "style"}]},
    ],
}


def test_place_cards_are_named_by_the_place_not_the_heading():
    """Place cards titled «L02/L05/L07» or photo pools «Референсы для генерации» are named by the place."""
    assert names_of("Сезон 1 · L02 · Ночная закусочная", place=True) == ["Ночная закусочная", "nochnaya zakusochnaya", "L02"]
    assert names_of("ПРОЕКТ МИРА · W01 · Квартира: дом обоих главных героев", place=True)[::2] == ["Квартира", "W01"]
    assert names_of("Референсы для генерации · Улицы", place=True)[0] == "Улицы"
    assert names_of("Сезон 1 · L02 · Ночная закусочная")[0] == "L02"  # a character card keeps the old rule


def test_names_and_transliteration():
    assert names_of("ВЕГА · эталон внешности") == ["ВЕГА", "vega"]
    assert names_of("Kitchen") == ["Kitchen"]


def test_profile_round_trip_and_lock(api):
    client, fid = api
    assert client.get(f"/api/franchises/{fid}/profile").json()["revision"] == 0
    saved = client.put(f"/api/franchises/{fid}/profile", json=PROFILE)
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    assert client.put(f"/api/franchises/{fid}/profile", json=PROFILE).status_code == 409
    bad = {**PROFILE, "revision": 1, "director_defaults": {"lighting": {"scheme": "disco"}}}
    assert client.put(f"/api/franchises/{fid}/profile", json=bad).status_code == 422
    bad_role = {**PROFILE, "revision": 1, "roles": [{"name": "X", "kind": "prop", "assets": [{"asset_id": "a", "role": "Лицо"}]}]}
    assert client.put(f"/api/franchises/{fid}/profile", json=bad_role).status_code == 422


def test_bible_resolves_without_a_profile(api):
    client, fid = api
    shot = client.post(f"/api/franchises/{fid}/resolve-shots",
                       json={"shots": [{"characters": ["VEGA"], "location": "Кухня", "framing": "close"}]}).json()["shots"][0]
    assert shot["asset_ids"] == ["vega-face", "vega-body", "kitchen"]
    assert shot["reference_roles"] == {"vega-face": "Лицо героя", "vega-body": "Персонаж целиком", "kitchen": "Окружение"}
    assert shot["reference_owners"] == {"vega-face": "ВЕГА", "vega-body": "ВЕГА"}
    assert shot["director_config"] == {"shot": {"id": "shot.franchise", "parameters": {"framing": "close"}}}
    assert shot["missing"] == []


def test_profile_roles_owner_costume_weapon_style_and_look(api):
    client, fid = api
    client.put(f"/api/franchises/{fid}/profile", json=PROFILE)
    shots = client.post(f"/api/franchises/{fid}/resolve-shots", json={"shots": [
        {"characters": ["ADAM", "VEGA"], "title": "Vega draws the sword", "framing": "medium"},
        {"characters": ["BOB"], "title": "Empty street"}]}).json()["shots"]
    first, second = shots
    assert {"hoodie", "sword", "style"} <= set(first["asset_ids"])
    assert "adam-body" not in first["asset_ids"]  # costume replaces the full-body outfit
    assert any("два наряда" in note for note in first["notes"])
    assert first["reference_owners"]["hoodie"] == "ADAM"
    assert first["reference_roles"]["sword"] == "Предмет" and "sword" not in first["reference_owners"]
    assert "VEGA's fantasy laser sword glows cyan." in first["look"]
    assert first["look"][0].startswith("Premium 3D")
    assert first["director_config"]["lighting"]["parameters"] == {"scheme": "rembrandt"}
    assert second["asset_ids"] == ["style"] and second["missing"] == ["BOB"]


def test_image_cap_per_shot(api):
    client, fid = api
    many = {**PROFILE, "roles": [{"name": "Crowd", "kind": "location", "assets": [{"asset_id": f"x{i}"} for i in range(8)]},
                                 {"name": "Street", "kind": "location", "assets": [{"asset_id": f"x{i}"} for i in range(8, 12)]}]}
    client.put(f"/api/franchises/{fid}/profile", json=many)
    shot = client.post(f"/api/franchises/{fid}/resolve-shots",
                       json={"shots": [{"title": "Crowd on the Street"}]}).json()["shots"][0]
    assert len(shot["asset_ids"]) == franchise_profile.MAX_IMAGES
    assert any("больше" in note for note in shot["notes"])


def test_franchise_lint_and_edit_defaults(api):
    client, fid = api
    client.put(f"/api/franchises/{fid}/profile", json=PROFILE)
    lint = client.post(f"/api/franchises/{fid}/shot-lint", json={"shots": [
        {"framing": "wide", "purpose": "establishing", "lens_mm": 14, "location": "k"},
        {"framing": "close", "angle": "dutch", "movement": "orbit", "motivation": "m", "transition_in": "dissolve",
         "location": "k"}]}).json()
    rules = {f["rule"] for f in lint["findings"]}
    assert {"franchise_lens", "franchise_move", "franchise_dutch", "franchise_transition"} <= rules
    sheet = client.post(f"/api/franchises/{fid}/edl-from-shots", json={"shots": [{"job_id": "a"}, {"job_id": "b"}]}).json()
    assert sheet["music"] == {"asset_id": "music", "volume": 0.35} and sheet["fade_out"] == 0.8
    assert client.get("/api/franchises/nope/profile").status_code == 404


def test_take_length_is_h3_not_the_cut_rhythm():
    """A franchise with a 1–6 s cut rhythm; checked against the take it would push the agent to 5 s takes."""
    from local_studio.franchise_profile import Profile, profile_findings

    profile = Profile.model_validate({"edit": {"min_shot": 1.0, "max_shot": 6.0}})
    codes = lambda shots: [f["rule"].removeprefix("franchise_") for f in profile_findings(profile, shots)]
    assert "take_length" not in codes([{"duration": 12}])
    assert "take_length" in codes([{"duration": 5}]) and "rhythm" not in codes([{"duration": 12}])


def test_card_names_skip_group_labels_and_rejected_designs():
    """«S1 · Анна Ли · …» and «УЛИЦА · Макс» must not merge many characters into «S1»/«УЛИЦА»."""
    assert names_of("S1 · Анна Ли · Дом Ли")[0] == "Анна Ли"
    assert names_of("УЛИЦА · Макс")[0] == "Макс" and names_of("ХОЗЯИН · Вера «Ржавчина»")[0] == "Вера «Ржавчина»"
    assert names_of("ВЕГА · эталон внешности")[0] == "ВЕГА" and names_of("S1")[0] == "S1"
    from local_studio.franchise_profile import Profile, bible_roles
    cards = [{"id": "a", "type": "character", "title": "УЛИЦА · Макс", "asset_ids": ["x"]},
             {"id": "b", "type": "character", "title": "УЛИЦА · Пудра", "asset_ids": ["y"]},
             {"id": "c", "type": "character", "title": "ОТКЛОНЕНО · Нина Крой · швея", "asset_ids": ["z"]},
             {"id": "d", "type": "character", "title": "РИКИ · предложение кроссовера", "asset_ids": ["w"],
              "body": "КАНДИДАТ НА КРОССОВЕР, НЕ УТВЕРЖДЁННЫЙ УЧАСТНИК СЕЗОНА."}]
    assert [r.name for r in bible_roles(Profile(), cards, {})] == ["Макс", "Пудра"]
