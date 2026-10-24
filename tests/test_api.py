"""Tests for the backend API.

The Bedrock client is mocked, so these tests exercise the HTTP layer
(status codes, request/response shape, error mapping) without calling AWS.
"""

import pytest
from fastapi.testclient import TestClient

import ai.incident_parser as parser
from backend.api import ACTION_EXAMPLES, app
from init_db import connect, reset_db

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


@pytest.fixture
def apply_db(tmp_path, monkeypatch):
    """Point the apply route at a fresh demo database."""
    path = tmp_path / "crisisops.db"
    reset_db(path)

    def _connect(db_path=path):
        return connect(path)

    monkeypatch.setattr("backend.api.connect", _connect)
    return path


def _stock(path, warehouse_id):
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT usable_stock FROM warehouses WHERE id = ?;", (warehouse_id,)
        ).fetchone()
        incidents = conn.execute("SELECT COUNT(*) FROM incidents;").fetchone()[0]
    finally:
        conn.close()
    return row[0], incidents


def test_list_actions_has_an_example_for_each_action():
    r = client.get("/incidents/actions")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == set(ACTION_EXAMPLES)
    for action, fields in ACTION_EXAMPLES.items():
        assert body[action]["operation"] == {"action": action, **fields}
        assert body[action]["text"]


def test_apply_decrease_stock_then_replans(apply_db):
    r = client.post(
        "/incidents/apply",
        json={
            "text": "We lost 40 food kits to flooding at Warehouse A.",
            "operation": {"action": "decrease_stock", "warehouse": "W1", "quantity": 40},
            "method": "greedy",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["operation"] == {
        "action": "decrease_stock",
        "warehouse": "W1",
        "quantity": 40,
    }
    assert set(body["plans"]) == {"greedy"}
    assert body["plans"]["greedy"]["summary"]["total_demand"] == 900
    assert _stock(apply_db, "W1") == (260, 1)


def test_apply_every_example_action_then_replans(apply_db):
    for action, example in client.get("/incidents/actions").json().items():
        r = client.post("/incidents/apply", json={**example, "method": "greedy"})
        assert r.status_code == 200, action
        assert r.json()["plans"]["greedy"]["method"] == "greedy"
    _, incidents = _stock(apply_db, "W1")
    assert incidents == len(ACTION_EXAMPLES)


def test_apply_unknown_id_does_not_record_an_incident(apply_db):
    r = client.post(
        "/incidents/apply",
        json={
            "text": "Unknown truck.",
            "operation": {"action": "truck_offline", "truck": "T99"},
            "method": "greedy",
        },
    )
    assert r.status_code == 422
    assert "T99" in r.json()["detail"]
    assert _stock(apply_db, "W1")[1] == 0


def test_apply_negative_stock_rolls_back(apply_db):
    r = client.post(
        "/incidents/apply",
        json={
            "text": "Lost more kits than the warehouse holds.",
            "operation": {"action": "decrease_stock", "warehouse": "W1", "quantity": 100000},
            "method": "greedy",
        },
    )
    assert r.status_code == 422
    assert _stock(apply_db, "W1") == (300, 0)
