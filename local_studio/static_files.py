"""Studio pages and modules with one build id.

Modules import each other as `./x.mjs?v=…`. With per-file versions bumped by hand, a parent module
served from the browser cache kept importing the child's old version, so a release showed up only
after one or two reloads (and two versions of one module could load side by side). Now every
`?v=…` in a served page, module or stylesheet is rewritten to the build id — a hash of all code files'
mtimes and sizes — so any edit gives every module a new URL at once, and an unchanged build is served
from the browser cache without a single request. The hand-written versions stay meaningful only for
tests (module-urls.test.mjs).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time

from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

CODE_SUFFIXES = {".mjs", ".js", ".css", ".html", ".json"}
REWRITTEN = {".mjs", ".js", ".css", ".html"}
VERSION_REF = re.compile(rb"(\.(?:mjs|js|css|json)\?v=)[A-Za-z0-9._-]+")
BUILD_TTL_S = 1.0


class StudioStaticFiles(StaticFiles):
    """Pages: always revalidated (ETag → 304). URLs carrying the current build id: immutable for a
    year. Anything else (no version, an old build id from a page loaded before an edit): revalidated."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._build: tuple[float, str] = (float("-inf"), "")
        self._bodies: dict[str, tuple[tuple, bytes]] = {}

    def build_id(self) -> str:
        checked, build = self._build
        if time.monotonic() - checked < BUILD_TTL_S:
            return build
        digest = hashlib.sha1()
        for path in sorted(Path(self.directory).rglob("*")):
            if path.suffix in CODE_SUFFIXES and path.is_file():
                stat = path.stat()
                digest.update(f"{path}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
        build = "b" + digest.hexdigest()[:12]
        self._build = (time.monotonic(), build)
        return build

    def _rewritten(self, file: Path, build: str) -> bytes:
        stat = file.stat()
        key = (stat.st_mtime_ns, stat.st_size, build)
        cached = self._bodies.get(str(file))
        if cached and cached[0] == key:
            return cached[1]
        body = VERSION_REF.sub(rb"\g<1>" + build.encode(), file.read_bytes())
        self._bodies[str(file)] = (key, body)
        return body

    async def get_response(self, path: str, scope) -> Response:
        # StaticFiles would answer 304 from its own mtime ETag before the body is rewritten (an old
        # page stuck in the cache): it always sends the file, conditionals are answered below.
        conditional = _header(scope, b"if-none-match")
        plain = {**scope, "headers": [(k, v) for k, v in scope.get("headers", [])
                                      if k not in (b"if-none-match", b"if-modified-since")]}
        response = await super().get_response(path, plain)
        build = self.build_id()
        query = scope.get("query_string", b"").decode("latin-1")
        current = f"v={build}" in query.split("&")
        file = Path(getattr(response, "path", "") or "")
        if response.status_code == 200 and file.suffix in REWRITTEN:
            body = self._rewritten(file, build)
            etag = '"' + hashlib.sha1(body).hexdigest() + '"'
            response = Response(body, media_type=response.media_type, headers={"ETag": etag})
        etag = response.headers.get("etag")
        if response.status_code == 200 and etag and etag in {value.strip() for value in conditional.split(",")}:
            response = Response(status_code=304, headers={"ETag": etag})
        immutable = current and file.suffix != ".html" and response.status_code in (200, 304)
        response.headers["Cache-Control"] = "max-age=31536000, immutable" if immutable else "no-cache"
        return response


def _header(scope, name: bytes) -> str:
    return next((value.decode("latin-1") for key, value in scope.get("headers", []) if key == name), "")
