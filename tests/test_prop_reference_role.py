"""A prop image keeps its explicit meaning from Studio through the Mac job contract."""
import hashlib
import tempfile
from pathlib import Path

from app.director import DIRECTOR_ROLE_MAP, ROLE_INSTRUCTION, should_use_image_reference_plan
from local_studio.store import StudioStore


def test_prop_reference_is_accepted_and_compiled():
    with tempfile.TemporaryDirectory() as root:
        store = StudioStore(Path(root))
        path = store.assets_dir / "remote.jpg"
        path.write_bytes(b"jpeg-test-fixture")
        asset = store.add_asset(kind="image", role="Предмет", original_name="remote.jpg",
                                mime="image/jpeg", size=path.stat().st_size,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), path=path)
        job = store.create_job(prompt="<Picture 1> is the remote on the sofa", asset_ids=[asset["asset_id"]],
                               settings={}, reference_roles={asset["asset_id"]: "Предмет"})
        assert "<Picture 1> defines the referenced object" in job["compiled_prompt"]
        assert DIRECTOR_ROLE_MAP["Предмет"] == "prop"
        assert ROLE_INSTRUCTION["prop"][0] == "image"
        assert should_use_image_reference_plan(
            [{"kind": "image", "role_override": "prop"}], "realistic", emotional_direction=False)
