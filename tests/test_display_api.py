"""Tests for the read-only display routes (backend/display_api.py)."""

import pytest
from fastapi.testclient import TestClient

from backend.api import app
from init_db import apply_operation, connect, reset_db

client = TestClient(app)


@pytest.fixture
def display_db(tmp_path, monkeypatch):
    """Point the display routes at a fresh Bay County database."""
    path = tmp_path / "crisisops.db"
    reset_db(path)
    monkeypatch.setattr("backend.display_api.connect", lambda *a, **k: connect(path))
    return path


def _db_snapshot(path):
    conn = connect(path)
    try:
        return (
            [tuple(r) for r in conn.execute(
                "SELECT id, usable_stock, is_operational FROM warehouses ORDER BY id")],
            [tuple(r) for r in conn.execute("SELECT id, demand FROM shelters ORDER BY id")],
            conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
        )
    finally:
        conn.close()


def _apply(path, operation):
    conn = connect(path)
    try:
        apply_operation(conn, operation, "test")
    finally:
        conn.close()


# --- GET /state --------------------------------------------------------------


def test_state_summary_and_lists(display_db):
    body = client.get("/state").json()
    assert body["summary"] == {
        "available_kits": 880, "total_stock": 880,
        "warehouses": 4, "warehouses_operational": 4,
        "shelters": 10, "total_demand": 900,
        "trucks": 8, "trucks_operational": 8, "routes_closed": 0,
    }
    assert body["last_updated"] is None and body["generated_at"].endswith("Z")
    assert [s["id"] for s in body["shelters"]] == [f"S{i}" for i in range(1, 11)]
    w1 = body["warehouses"][0]
    assert w1 == {"id": "W1", "name": "Warehouse A", "location": "Northside Depot",
                  "usable_stock": 300, "is_operational": True, "x_km": 8, "y_km": 24,
                  "trucks_total": 2, "trucks_operational": 2}
    assert len(body["routes"]) == 40 and len(body["trucks"]) == 8


def test_state_keeps_offline_items_visible(display_db):
    _apply(display_db, {"action": "warehouse_offline", "warehouse": "W2"})
    _apply(display_db, {"action": "truck_offline", "truck": "T1"})
    _apply(display_db, {"action": "close_route", "warehouse": "W4", "shelter": "S3"})
    body = client.get("/state").json()
    w2 = next(w for w in body["warehouses"] if w["id"] == "W2")
    assert w2["is_operational"] is False                 # still listed
    assert body["summary"]["available_kits"] == 880 - 260
    assert body["summary"]["total_stock"] == 880
    assert body["summary"]["warehouses_operational"] == 3
    assert body["summary"]["trucks_operational"] == 7
    assert body["summary"]["routes_closed"] == 1
    assert body["last_updated"].endswith("Z")


def test_state_without_database_returns_503(tmp_path, monkeypatch):
    empty = tmp_path / "empty.db"
    monkeypatch.setattr("backend.display_api.connect", lambda *a, **k: connect(empty))
    r = client.get("/state")
    assert r.status_code == 503
    assert "init_db.py reset" in r.json()["detail"]


# --- GET /plan ---------------------------------------------------------------


def test_plan_has_table_fields(display_db):
    r = client.get("/plan", params={"method": "greedy"})
    assert r.status_code == 200
    plans = r.json()["plans"]
    assert set(plans) == {"greedy"}
    shipment = plans["greedy"]["shipments"][0]
    assert {"truck_id", "warehouse_name", "shelter_name", "kits", "trips",
            "eta_minutes"} <= shipment.keys()
    shelter = plans["greedy"]["shelters"][0]
    assert {"shelter_name", "demand", "delivered", "unmet", "coverage"} <= shelter.keys()


def test_plan_compare_returns_both(display_db):
    plans = client.get("/plan", params={"method": "compare"}).json()["plans"]
    assert set(plans) == {"greedy", "optimal"}


def test_plan_rejects_unknown_method(display_db):
    assert client.get("/plan", params={"method": "fastest"}).status_code == 422


def test_plan_does_not_write(display_db):
    before = _db_snapshot(display_db)
    client.get("/plan", params={"method": "greedy"})
    assert _db_snapshot(display_db) == before


# --- POST /plan/preview ------------------------------------------------------


def test_preview_shows_impact_without_saving(display_db):
    before_db = _db_snapshot(display_db)
    r = client.post("/plan/preview", json={
        "text": "Warehouse B lost power and stopped shipping.",
        "operation": {"action": "warehouse_offline", "warehouse": "W2"},
        "method": "optimal",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] is False
    assert _db_snapshot(display_db) == before_db         # nothing persisted

    metrics = {m["key"]: m for m in body["comparison"]["optimal"]["metrics"]}
    assert metrics["kits_delivered"]["after"] < metrics["kits_delivered"]["before"]
    assert metrics["worst_shelter_coverage_pct"]["change"] < 0
    assert body["before"]["optimal"]["summary"]["total_delivered"] == metrics["kits_delivered"]["before"]


def test_preview_tracks_demand_changes(display_db):
    body = client.post("/plan/preview", json={
        "operation": {"action": "set_demand", "shelter": "S3", "quantity": 160},
        "method": "greedy",
    }).json()
    s3 = next(s for s in body["comparison"]["greedy"]["shelters"] if s["shelter_id"] == "S3")
    assert (s3["demand_before"], s3["demand_after"]) == (90, 160)


def test_preview_body_matches_apply_body(display_db):
    """The examples from GET /incidents/actions work unchanged for preview."""
    example = client.get("/incidents/actions").json()["decrease_stock"]
    r = client.post("/plan/preview", json={**example, "method": "greedy"})
    assert r.status_code == 200
    assert r.json()["operation"] == example["operation"]


@pytest.mark.parametrize("operation", [
    {"action": "truck_offline", "truck": "T99"},                          # unknown id
    {"action": "decrease_stock", "warehouse": "W4", "quantity": 100000},  # negative stock
])
def test_preview_rejects_invalid_operation(display_db, operation):
    before_db = _db_snapshot(display_db)
    r = client.post("/plan/preview", json={"operation": operation, "method": "greedy"})
    assert r.status_code == 422
    assert _db_snapshot(display_db) == before_db


def test_display_routes_are_documented():
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/state", "/plan", "/plan/preview"} <= paths.keys()
