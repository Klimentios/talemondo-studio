import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from local_studio import franchise_profile, franchises, idea_pack

PROFILE = {"revision": 0, "look": {"style": "Premium 3D animated feature look.", "palette": "warm amber"},
           "roles": [{"name": "Adam hoodie", "kind": "costume", "owner": "ADAM", "with_owner": True,
                      "assets": [{"asset_id": "hoodie"}]}]}


@pytest.fixture
def api(tmp_path):
    split = tmp_path / "split.png"
    sheet = Image.new("L", (800, 400), 0)
    sheet.paste(255, (400, 0, 800, 400))
    sheet.save(split)
    assets = {
        "vega-face": {"kind": "image", "role": "Лицо героя"}, "adam-body": {"kind": "image", "role": "Персонаж целиком"},
        "orion-face": {"kind": "image", "role": "Лицо героя"}, "hoodie": {"kind": "image", "role": "Костюм"},
        "kitchen": {"kind": "image", "role": "Окружение"}, "idea-art": {"kind": "image", "role": "Референс"},
        "idea-split": {"kind": "image", "role": "Референс", "path": str(split)},
        "street": {"kind": "image", "role": "Окружение"},
    }
    store = type("Store", (), {"root": tmp_path, "get_asset": lambda self, i: assets.get(i)})()
    app = FastAPI()
    for module in (franchises, franchise_profile, idea_pack):
        app.include_router(module.make_router(store))
    client = TestClient(app)
    fid = client.post("/api/franchises", json={"name": "K&A"}).json()["id"]
    other = client.post("/api/franchises", json={"name": "A&M"}).json()["id"]
    assert client.put(f"/api/franchises/{fid}/profile", json=PROFILE).status_code == 200
    for card in ({"type": "character", "title": "ВЕГА", "asset_ids": ["vega-face"]},
                 {"type": "character", "title": "АДАМ", "asset_ids": ["adam-body"]},
                 {"type": "location", "title": "Кухня", "asset_ids": ["kitchen"]}):
        client.post(f"/api/franchises/{fid}/entities", json=card)
    client.post(f"/api/franchises/{other}/entities", json={"type": "character", "title": "ОРИОН", "asset_ids": ["orion-face"]})
    idea = client.post(f"/api/franchises/{fid}/entities", json={
        "type": "idea", "title": "Вега ворует колбасу", "body": "Вега тянется к столу, Адам входит на кухню.",
        "asset_ids": ["idea-art", "idea-split"]}).json()
    return client, fid, other, idea


def build(client, fid, eid, **extra):
    return client.post(f"/api/franchises/{fid}/ideas/{eid}/pack", json={"expected_revision": 0, "mode": "animation3d", **extra})


def test_pack_takes_only_the_heroes_and_places_the_idea_names(api):
    client, fid, _, idea = api
    pack = build(client, fid, idea["id"], idea_assets=[{"asset_id": "idea-art", "role": "Стиль"}]).json()
    assert [h["name"] for h in pack["cast"]] == ["ВЕГА", "АДАМ"] and pack["places"] == ["Кухня"]
    roles = {r["asset_id"]: r["role"] for r in pack["references"]}
    # ADAM's signature hoodie replaces his full-body picture; ORION of another franchise never enters.
    assert roles == {"vega-face": "Лицо героя", "hoodie": "Костюм", "kitchen": "Окружение", "idea-art": "Стиль"}
    assert {v["asset_id"]: v["split"] for v in pack["idea_visuals"]} == {"idea-art": False, "idea-split": True}
    assert pack["style_lock"]["style"] == "Premium 3D animated feature look." and pack["revision"] == 1


def test_pack_refuses_split_sheets_hero_photos_and_unreasoned_library_pictures(api):
    client, fid, _, idea = api
    assert build(client, fid, idea["id"], idea_assets=[{"asset_id": "idea-split", "role": "Стиль"}]).status_code == 422
    assert build(client, fid, idea["id"], extra_assets=[{"asset_id": "street", "role": "Окружение"}]).status_code == 422
    assert build(client, fid, idea["id"], extra_assets=[{"asset_id": "orion-face", "role": "Стиль", "reason": "x"}]).status_code == 422
    assert build(client, fid, idea["id"], idea_assets=[{"asset_id": "idea-art", "role": "Лицо героя"}]).status_code == 422
    assert build(client, fid, idea["id"], extra_assets=[{"asset_id": "street", "role": "Окружение", "reason": "улица"}]).status_code == 200
    assert "expected_revision=1" in build(client, fid, idea["id"]).json()["detail"]


def test_agent_adds_an_implied_place_only_from_its_franchise(api):
    client, fid, _, _ = api
    idea = client.post(f"/api/franchises/{fid}/entities", json={"type": "idea", "title": "Вега ждёт Адам"}).json()
    assert build(client, fid, idea["id"], add_names=["ОРИОН"]).status_code == 422
    pack = build(client, fid, idea["id"], add_names=["кухня"]).json()
    assert pack["places"] == ["Кухня"] and "kitchen" in {r["asset_id"] for r in pack["references"]}


def test_scene_outside_its_pack_is_rejected_and_a_changed_idea_makes_it_stale(api):
    client, fid, other, idea = api
    pack = build(client, fid, idea["id"]).json()
    refs = [{"asset_id": r["asset_id"], "role": r["role"], "owner": r["owner"]} for r in pack["references"]]
    scene = {"franchise_id": fid, "style": pack["style_lock"]["style"], "mode": "animation3d", "references": refs,
             "shots": [{"characters": ["VEGA"]}, {"characters": ["Адам"]}]}
    check = lambda body: client.post(f"/api/franchises/{fid}/ideas/{idea['id']}/pack/check", json=body).json()
    assert check(scene) == {"ok": True, "problems": []}
    wrong = check({**scene, "style": "Gritty live action.", "mode": "realistic",
                   "references": refs + [{"asset_id": "orion-face", "role": "Лицо героя", "owner": "ORION"}],
                   "shots": [{"characters": ["ORION"]}]})
    assert not wrong["ok"] and len(wrong["problems"]) == 4
    assert client.get(f"/api/franchises/{other}/ideas/{idea['id']}/pack").status_code == 404
    client.put(f"/api/franchises/{fid}/entities/{idea['id']}", json={
        "type": "idea", "title": idea["title"], "body": "Новая версия", "asset_ids": [], "revision": idea["revision"]})
    assert client.get(f"/api/franchises/{fid}/ideas/{idea['id']}/pack").json()["stale"] is True
