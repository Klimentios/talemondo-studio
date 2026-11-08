"""director_schema.json is the single source for Studio (JS) and the GPU API (Python)."""
import json
from pathlib import Path

from app.director_config import FIELD_RULES, SCHEMA, normalize_director_config

ROOT = Path(__file__).resolve().parent.parent


def test_every_field_has_type_and_both_labels():
    for category, fields in SCHEMA["categories"].items():
        keys = [field["key"] for field in fields]
        assert len(keys) == len(set(keys)), f"{category}: duplicate keys"
        for field in fields:
            assert field["label"]["ru"] and field["label"]["en"], (category, field["key"])
            for option in field.get("options", []):
                assert option["label"]["ru"] and option["label"]["en"], (category, field["key"], option["value"])


def test_builtin_presets_validate_against_schema():
    # Every built-in preset's parameters must pass server validation.
    import subprocess
    script = (
        "const {DIRECTOR_PRESETS}=await import('./preview/h3-director/director-presets.mjs');"
        "console.log(JSON.stringify(DIRECTOR_PRESETS.map(p=>[p.category,p.id,p.parameters||{}])))"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    unknown = []
    for category, preset_id, params in json.loads(out):
        allowed = {k: v for k, v in params.items() if k in FIELD_RULES[category]}
        normalize_director_config({category: {"id": preset_id, "parameters": allowed}})
        unknown += [(preset_id, k) for k in params if k not in FIELD_RULES[category]]
    # Catalog-only facts (camera body specs) are allowed to stay UI-only.
    assert all(preset_id.startswith("camera.") for preset_id, _ in unknown), unknown


def test_studio_route_serves_same_file():
    from fastapi.testclient import TestClient
    from local_studio.main import app
    response = TestClient(app, base_url="http://127.0.0.1").get("/director-schema.json")
    assert response.status_code == 200
    assert response.json() == SCHEMA


def test_static_copy_for_running_studio_is_identical():
    # preview/h3-director/director-schema.json lets an already running Studio (static files only)
    # load the schema without a restart. It must stay byte-identical to the canonical file.
    # Refresh with: cp app/director_schema.json preview/h3-director/director-schema.json
    canonical = (ROOT / "app/director_schema.json").read_bytes()
    assert (ROOT / "preview/h3-director/director-schema.json").read_bytes() == canonical
