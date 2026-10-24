"""Tests for the backend API.

The Bedrock client is mocked, so these tests exercise the HTTP layer
(status codes, request/response shape, error mapping) without calling AWS.
"""

import pytest
from fastapi.testclient import TestClient

import ai.incident_parser as parser
from backend.api import app

client = TestClient(app)


def _tool_response(**fields):
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "name": "record_inventory_update",
                            "input": fields,
                        }
                    }
                ]
            }
        }
    }


def _text_response(text):
    return {"output": {"message": {"content": [{"text": text}]}}}


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def converse(self, **kwargs):
        return self._response


@pytest.fixture
def mock_bedrock(monkeypatch):
    def _install(response):
        monkeypatch.setattr(
            parser, "_get_bedrock_client", lambda: _FakeClient(response)
        )

    return _install


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_parse_success(mock_bedrock):
    mock_bedrock(
        _tool_response(
            action="decrease_stock",
            warehouse="A",
            item="food kits",
            quantity=40,
        )
    )
    r = client.post(
        "/inventory/parse", json={"text": "Warehouse A lost 40 food kits."}
    )
    assert r.status_code == 200
    assert r.json() == {
        "action": "decrease_stock",
        "warehouse": "A",
        "item": "food_kits",
        "quantity": 40,
    }


def test_parse_ambiguous_returns_422(mock_bedrock):
    mock_bedrock(_text_response("CANNOT_PARSE: not an inventory update"))
    r = client.post("/inventory/parse", json={"text": "hello"})
    assert r.status_code == 422
    assert r.json()["detail"] == "not an inventory update"


def test_empty_text_returns_422():
    """Empty text is rejected by request validation before hitting Bedrock."""
    r = client.post("/inventory/parse", json={"text": ""})
    assert r.status_code == 422


def test_missing_field_returns_422():
    r = client.post("/inventory/parse", json={})
    assert r.status_code == 422
