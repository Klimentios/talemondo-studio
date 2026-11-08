from fastapi.testclient import TestClient

from local_studio.main import app
from local_studio.static_files import StudioStaticFiles


def static() -> StudioStaticFiles:
    return next(route.app for route in app.routes if isinstance(getattr(route, "app", None), StudioStaticFiles))


def test_every_version_reference_becomes_the_build_id_and_that_url_is_immutable():
    client = TestClient(app, base_url="http://127.0.0.1")
    build = static().build_id()
    page = client.get("/index.html")
    assert page.headers["cache-control"] == "no-cache"
    assert f"job-feed.css?v={build}" in page.text and "?v=2026" not in page.text
    module = client.get(f"/app-queue.mjs?v={build}")
    assert module.headers["cache-control"] == "max-age=31536000, immutable"
    assert f"job-feed.mjs?v={build}" in module.text and "?v=2026" not in module.text
    again = client.get(f"/app-queue.mjs?v={build}", headers={"If-None-Match": module.headers["etag"]})
    assert again.status_code == 304


def test_old_build_ids_and_unversioned_files_revalidate():
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/studio-rail.mjs?v=old").headers["cache-control"] == "no-cache"
    assert client.get("/studio-rail.mjs").headers["cache-control"] == "no-cache"
    assert client.get("/editor/").headers["cache-control"] == "no-cache"
    assert client.get(f"/index.html?v={static().build_id()}").headers["cache-control"] == "no-cache"


def test_build_id_changes_when_a_module_changes(tmp_path, monkeypatch):
    (tmp_path / "a.mjs").write_text("import './b.mjs?v=1';")
    files = StudioStaticFiles(directory=tmp_path)
    first = files.build_id()
    (tmp_path / "b.mjs").write_text("export {}")
    monkeypatch.setattr(files, "_build", (float("-inf"), ""))
    assert files.build_id() != first



def test_a_page_cached_before_the_rewrite_is_sent_again():
    """A browser holding StaticFiles' own mtime ETag must get the rewritten page, not 304."""
    import hashlib
    import os
    from local_studio import config
    client = TestClient(app, base_url="http://127.0.0.1")
    stat = os.stat(config.ROOT / "preview/h3-director/index.html")
    starlette_etag = '"' + hashlib.md5(f"{stat.st_mtime}-{stat.st_size}".encode(), usedforsecurity=False).hexdigest() + '"'
    page = client.get("/index.html", headers={"If-None-Match": starlette_etag})
    assert page.status_code == 200 and f"?v={static().build_id()}" in page.text
    assert client.get("/index.html", headers={"If-None-Match": page.headers["etag"]}).status_code == 304
