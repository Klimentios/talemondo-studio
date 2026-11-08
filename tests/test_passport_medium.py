"""Jobs keep the medium of the franchise passport of their hero pictures (franchise audit 05.11.2026)."""
import json
import sqlite3

from local_studio.passport_medium import medium_problem


def make_db(path, rendering_by_franchise, owners):
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE franchises(id TEXT, name TEXT); CREATE TABLE profiles(franchise_id TEXT, content TEXT);"
                     "CREATE TABLE entities(id TEXT, franchise_id TEXT, content TEXT);")
    for fid, rendering in rendering_by_franchise.items():
        db.execute("INSERT INTO franchises VALUES (?,?)", (fid, fid.upper()))
        roles = [{"assets": [{"asset_id": a}]} for a, f in owners.items() if f == fid]
        db.execute("INSERT INTO profiles VALUES (?,?)",
                   (fid, json.dumps({"director_defaults": {"style": {"rendering": rendering}}, "roles": roles})))
    db.commit()
    db.close()


def test_photoreal_heroes_refuse_a_3d_job_and_allow_replace(tmp_path):
    db = tmp_path / "franchises.sqlite3"
    make_db(db, {"demo": "photoreal", "adam": "animation3d"}, {"iris": "demo", "vega3d": "adam"})
    assert "photoreal" in medium_problem(db, ["iris"], "animation3d", "style3d")
    assert medium_problem(db, ["iris"], "replace", "character_swap") == ""
    assert medium_problem(db, ["iris"], "realistic", "none") == ""
    assert "LoRA" in medium_problem(db, ["iris"], "realistic", "style3d")
    assert medium_problem(db, ["vega3d"], "animation3d", "style3d") == ""
    assert medium_problem(db, ["vega3d", "iris"], "animation3d", "style3d") == ""  # mixed media: not judged
    assert medium_problem(db, ["unknown"], "animation3d", "style3d") == ""
    assert medium_problem(tmp_path / "missing.sqlite3", ["iris"], "animation3d", "style3d") == ""


def test_store_refuses_a_3d_job_of_photoreal_heroes_on_every_route(tmp_path):
    # Context IR and studio-settings packages call store.create_job directly, past /api/jobs.
    import pytest
    from local_studio.store import StudioStore
    store = StudioStore(tmp_path)
    path = store.assets_dir / "iris.png"
    path.write_bytes(b"iris")
    asset = store.add_asset(kind="image", role="Персонаж целиком", original_name="iris.png",
                            mime="image/png", size=4, sha256="iris", path=path)["asset_id"]
    make_db(tmp_path / "franchises.sqlite3", {"demo": "photoreal"}, {asset: "demo"})
    args = dict(prompt="IRIS from <Picture 1> waves at the camera.", asset_ids=[asset],
                reference_roles={asset: "Персонаж целиком"}, reference_owners={asset: "IRIS"})
    with pytest.raises(ValueError, match="photoreal"):
        store.create_job(**args, settings={"mode": "animation3d"})
    assert store.create_job(**args, settings={"mode": "realistic"})["job_id"]
