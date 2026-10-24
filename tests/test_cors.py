"""CORS for the standalone frontend on port 8000 (backend/api.py)."""

import pytest
from fastapi.testclient import TestClient

from backend.api import CORS_ORIGINS, app

client = TestClient(app)


@pytest.mark.parametrize("origin", CORS_ORIGINS)
def test_simple_request_from_frontend_is_allowed(origin):
    r = client.get("/health", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("origin", CORS_ORIGINS)
def test_preflight_for_json_post_is_allowed(origin):
    """fetch() with a JSON body triggers a preflight; it must pass."""
    r = client.options("/plan/preview", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert "POST" in r.headers["access-control-allow-methods"]


@pytest.mark.parametrize("origin", ["http://evil.example", "http://127.0.0.1:9999"])
def test_other_origins_are_not_allowed(origin):
    r = client.get("/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in r.headers
    preflight = client.options("/incidents/apply", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST"})
    assert preflight.status_code == 400


def test_no_credentials_and_no_wildcard():
    r = client.get("/health", headers={"Origin": CORS_ORIGINS[0]})
    assert r.headers["access-control-allow-origin"] != "*"
    assert "access-control-allow-credentials" not in r.headers
