"""Parser codes ('A', '3') resolve to database ids ('W1', 'S3')."""

import pytest
from fastapi.testclient import TestClient

import ai.incident_parser as parser
from backend.api import app
from init_db import apply_operation, connect, reset_db, resolve_id, resolve_operation

client = TestClient(app)


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "crisisops.db"
    reset_db(path)                                           # Bay County demo
    c = connect(path)
    yield c
    c.close()


@pytest.mark.parametrize("kind,value,expected", [
    ("warehouse", "A", "W1"),
    ("warehouse", "a", "W1"),
    ("warehouse", "D", "W4"),
    ("warehouse", "W1", "W1"),
    ("warehouse", "w2", "W2"),
    ("warehouse", "Warehouse C", "W3"),
    ("warehouse", "  warehouse   c ", "W3"),
    ("shelter", "3", "S3"),
    ("shelter", "1", "S1"),
    ("shelter", "10", "S10"),                                # not S1
    ("shelter", "S10", "S10"),
    ("shelter", "Shelter 7", "S7"),
    ("truck", "T1", "T1"),
    ("truck", "t8", "T8"),
    ("truck", "TRUCK T2", "T2"),                             # parser uppercases 'Truck T2'
])
def test_resolve_id(conn, kind, value, expected):
    assert resolve_id(conn, kind, value) == expected


@pytest.mark.parametrize("kind,value", [
    ("warehouse", "Z"), ("warehouse", "W9"), ("shelter", "11"),
    ("truck", "T99"), ("warehouse", "   "),
])
def test_unknown_reference_is_rejected(conn, kind, value):
    with pytest.raises(ValueError):
        resolve_id(conn, kind, value)


def test_unknown_reference_lists_known_ids(conn):
    with pytest.raises(ValueError, match=r"Known: W1 \(Warehouse A\), W2 \(Warehouse B\)"):
        resolve_id(conn, "warehouse", "Z")


def test_ambiguous_code_is_rejected(conn):
    conn.execute("INSERT INTO warehouses (id, name, location, usable_stock) "
                 "VALUES ('W5', 'Warehouse A', 'Duplicate', 10)")
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_id(conn, "warehouse", "A")
    assert resolve_id(conn, "warehouse", "W5") == "W5"       # ids stay unambiguous


def test_id_beats_name_code(conn):
    conn.execute("INSERT INTO warehouses (id, name, location, usable_stock) "
                 "VALUES ('A', 'Warehouse Z', 'Edge case', 10)")
    assert resolve_id(conn, "warehouse", "A") == "A"
    assert resolve_id(conn, "warehouse", "Z") == "A"


def test_resolve_operation_is_idempotent(conn):
    op = {"action": "close_route", "warehouse": "A", "shelter": "2"}
    resolved = resolve_operation(conn, op)
    assert resolved == {"action": "close_route", "warehouse": "W1", "shelter": "S2"}
    assert resolve_operation(conn, resolved) == resolved
    assert op["warehouse"] == "A"                            # input not mutated


def test_apply_operation_accepts_parser_codes(conn):
    apply_operation(conn, {"action": "close_route", "warehouse": "A", "shelter": "2"},
                    "The road between Warehouse A and Shelter 2 is blocked.")
    row = conn.execute("SELECT is_open FROM delivery_routes "
                       "WHERE warehouse_id = 'W1' AND shelter_id = 'S2'").fetchone()
    assert row["is_open"] == 0
    incident = conn.execute("SELECT incident_type, target_id FROM incidents").fetchone()
    assert tuple(incident) == ("route_closed", "W1->S2")     # logged with real ids


def test_small_seed_resolves_too(tmp_path):
    path = tmp_path / "small.db"
    reset_db(path, scenario=None)
    c = connect(path)
    try:
        assert resolve_id(c, "warehouse", "B") == "W2"
        assert resolve_id(c, "shelter", "3") == "S3"
    finally:
        c.close()


# --- The teammate's flow: parse -> preview -> apply --------------------------


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    path = tmp_path / "crisisops.db"
    reset_db(path)
    monkeypatch.setattr("backend.api.connect", lambda *a, **k: connect(path))
    monkeypatch.setattr("backend.display_api.connect", lambda *a, **k: connect(path))
    return path


@pytest.fixture
def parsed_decrease(monkeypatch):
    """Run /incidents/parse with Bedrock mocked to return warehouse 'A'."""
    response = {"output": {"message": {"content": [{"toolUse": {
        "name": "record_incident",
        "input": {"action": "decrease_stock", "warehouse": "Warehouse A", "quantity": 40},
    }}]}}}

    class _Fake:
        def converse(self, **kwargs):
            return response

    monkeypatch.setattr(parser, "_get_bedrock_client", lambda: _Fake())
    r = client.post("/incidents/parse",
                    json={"text": "We lost 40 food kits to flooding at Warehouse A."})
    assert r.status_code == 200
    assert r.json()["warehouse"] == "A"                      # parser output unchanged
    return r.json()


def _stock(path, wid="W1"):
    c = connect(path)
    try:
        return c.execute("SELECT usable_stock FROM warehouses WHERE id = ?", (wid,)).fetchone()[0]
    finally:
        c.close()


def test_parse_output_works_for_preview(api_db, parsed_decrease):
    r = client.post("/plan/preview", json={"operation": parsed_decrease, "method": "greedy"})
    assert r.status_code == 200
    assert r.json()["operation"] == {"action": "decrease_stock", "warehouse": "W1", "quantity": 40}
    assert _stock(api_db) == 300                             # preview saves nothing


def test_parse_output_works_for_apply(api_db, parsed_decrease):
    r = client.post("/incidents/apply", json={
        "text": "We lost 40 food kits to flooding at Warehouse A.",
        "operation": parsed_decrease,
        "method": "greedy",
    })
    assert r.status_code == 200
    assert r.json()["operation"] == {"action": "decrease_stock", "warehouse": "W1", "quantity": 40}
    assert _stock(api_db) == 260


def test_unknown_code_is_a_clear_422(api_db):
    r = client.post("/incidents/apply", json={
        "text": "Warehouse Z lost power.",
        "operation": {"action": "warehouse_offline", "warehouse": "Z"},
        "method": "greedy",
    })
    assert r.status_code == 422
    assert "No warehouse matching 'Z'" in r.json()["detail"]
    assert "W1 (Warehouse A)" in r.json()["detail"]
