"""CORS configuration tests.

Without CORSMiddleware, every browser request from a different origin (e.g.
the Vite dev server on :5173 calling the API on :8000) fails at the network
layer before reaching any route -- which is indistinguishable, from the
frontend's point of view, from the API being unreachable. These tests pin the
fix so it can't silently regress.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import init_singletons
from app.graphdb.client import init_client


def _client() -> TestClient:
    init_singletons()
    init_client(get_settings())
    from app.main import app
    return TestClient(app)


def test_allowed_origin_gets_cors_header_on_preflight():
    c = _client()
    resp = c.options(
        "/openapi.json",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_allowed_origin_gets_cors_header_on_actual_response():
    c = _client()
    resp = c.get("/openapi.json", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_unlisted_origin_does_not_get_cors_header():
    """The allowlist must be a real filter, not a disguised wildcard."""
    c = _client()
    resp = c.get("/openapi.json", headers={"Origin": "http://evil.example.com"})
    assert resp.headers.get("access-control-allow-origin") is None


def test_cors_origins_list_parses_comma_separated_setting():
    settings = get_settings()
    origins = settings.cors_origins_list
    assert "http://localhost:5173" in origins
    assert all(o == o.strip() for o in origins)
