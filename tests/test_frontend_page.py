"""The dashboard is served from the API's origin (backend/frontend_page.py)."""

from fastapi.testclient import TestClient

from backend.api import app
from frontend.app import PAGE

client = TestClient(app)


def test_dashboard_is_the_unchanged_frontend_page():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text == PAGE
    assert client.get("/index.html").text == PAGE


def test_dashboard_keeps_security_policy():
    csp = client.get("/").headers["content-security-policy"]
    assert "connect-src 'self'" in csp       # page may call this origin's API
    assert "default-src 'self'" in csp


def test_api_routes_still_respond_alongside_page():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/favicon.ico").status_code == 204


def test_page_is_not_in_api_docs():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths and "/index.html" not in paths
