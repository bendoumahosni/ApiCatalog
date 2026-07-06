"""Tests for POST /validate's optional expected_entity_type query parameter.

This parameter lets entity-specific UI flows validate as explicitly as the
corresponding POST route does, while leaving /validate itself backward-
compatible: omitting the parameter still auto-detects the type, which is
what the free-form Validation page uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.deps import init_singletons
from app.graphdb.client import init_client

BASE = Path(__file__).resolve().parent.parent
EXAMPLES = BASE / "examples" / "schemas"


def _client() -> TestClient:
    init_singletons()
    init_client(get_settings())
    from app.main import app
    return TestClient(app)


def _catalog_example() -> dict:
    return json.loads((EXAMPLES / "service_catalog.schema.json").read_text())


def test_validate_without_expected_type_auto_detects():
    c = _client()
    resp = c.post("/validate", json=_catalog_example())
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity_type"] == "service_catalog"


def test_validate_with_matching_expected_type_passes():
    c = _client()
    resp = c.post("/validate?expected_entity_type=service_catalog", json=_catalog_example())
    assert resp.status_code == 200
    body = resp.json()
    assert body["conforms"] is True
    assert body["entity_type"] == "service_catalog"


def test_validate_with_mismatched_expected_type_rejected():
    c = _client()
    resp = c.post("/validate?expected_entity_type=service_candidate", json=_catalog_example())
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "entity_type_mismatch"
    assert body["details"]["expected_entity_type"] == "service_candidate"
    assert body["details"]["detected_entity_type"] == "service_catalog"


def test_validate_rejects_unknown_expected_type_value():
    c = _client()
    resp = c.post("/validate?expected_entity_type=not_a_real_type", json=_catalog_example())
    assert resp.status_code == 422
