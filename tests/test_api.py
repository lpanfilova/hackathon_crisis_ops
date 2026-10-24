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
                            "name": "record_incident",
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


def test_parse_decrease_stock(mock_bedrock):
    mock_bedrock(
        _tool_response(action="decrease_stock", warehouse="A", quantity=40)
    )
    r = client.post(
        "/incidents/parse",
        json={"text": "We lost 40 food kits to flooding at Warehouse A."},
    )
    assert r.status_code == 200
    assert r.json() == {
        "action": "decrease_stock",
        "warehouse": "A",
        "shelter": None,
        "truck": None,
        "quantity": 40,
    }


def test_parse_close_route(mock_bedrock):
    mock_bedrock(
        _tool_response(action="close_route", warehouse="A", shelter="2")
    )
    r = client.post(
        "/incidents/parse",
        json={"text": "The road between Warehouse A and Shelter 2 is blocked."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "close_route"
    assert body["warehouse"] == "A"
    assert body["shelter"] == "2"


def test_parse_truck_offline(mock_bedrock):
    mock_bedrock(_tool_response(action="truck_offline", truck="T1"))
    r = client.post(
        "/incidents/parse", json={"text": "Our delivery truck T1 broke down."}
    )
    assert r.status_code == 200
    assert r.json()["truck"] == "T1"


def test_legacy_inventory_path_still_works(mock_bedrock):
    """The deprecated /inventory/parse alias still responds."""
    mock_bedrock(_tool_response(action="warehouse_offline", warehouse="B"))
    r = client.post(
        "/inventory/parse",
        json={"text": "Warehouse B lost power and stopped shipping."},
    )
    assert r.status_code == 200
    assert r.json()["action"] == "warehouse_offline"


def test_parse_ambiguous_returns_422(mock_bedrock):
    mock_bedrock(_text_response("CANNOT_PARSE: not an operation"))
    r = client.post("/incidents/parse", json={"text": "hello"})
    assert r.status_code == 422
    assert r.json()["detail"] == "not an operation"


def test_empty_text_returns_422():
    """Empty text is rejected by request validation before hitting Bedrock."""
    r = client.post("/incidents/parse", json={"text": ""})
    assert r.status_code == 422


def test_missing_field_returns_422():
    r = client.post("/incidents/parse", json={})
    assert r.status_code == 422
