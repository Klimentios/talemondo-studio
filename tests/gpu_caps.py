"""GPU /api/capabilities fields a current Mac worker requires (versioned contract)."""
from app.contract import API_CONTRACT, director_schema_sha256

CONTRACT_CAPS = {"api_contract": API_CONTRACT, "director_schema_sha256": director_schema_sha256()}


def serving_caps(handler, caps=None):
    """Wrap an httpx request handler so /api/capabilities answers with a current contract."""
    import httpx

    def wrapped(request):
        if request.url.path == "/api/capabilities":
            return httpx.Response(200, json={**CONTRACT_CAPS, **(caps or {})})
        return handler(request)
    return wrapped
