"""Tests for the SQLite database layer (init_db.py)."""

import json
import sqlite3

import pytest

from ai.incident_parser import IncidentAction
from init_db import (
    apply_operation,
    connect,
    load_scenario,
    reset_db,
    set_warehouse_stock,
)
from optimization.allocator import Network
from optimization.scenario import Scenario


@pytest.fixture
def scenario_db(tmp_path):
    """A fresh database loaded with the default demo scenario."""
    path = tmp_path / "crisisops.db"
    reset_db(path)
    conn = connect(path)
    yield conn
    conn.close()


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _incidents(conn):
    return conn.execute("SELECT incident_type, target_id FROM incidents ORDER BY id").fetchall()


def test_default_reset_loads_demo_scenario(scenario_db):
    counts = {t: _count(scenario_db, t)
              for t in ("warehouses", "shelters", "trucks", "delivery_routes", "incidents")}
    assert counts == {"warehouses": 4, "shelters": 10, "trucks": 8,
                      "delivery_routes": 40, "incidents": 0}
    assert scenario_db.execute("PRAGMA foreign_key_check").fetchall() == []
    w1 = scenario_db.execute("SELECT * FROM warehouses WHERE id = 'W1'").fetchone()
    assert (w1["usable_stock"], w1["x_km"], w1["y_km"]) == (300, 8, 24)


def test_small_seed_reset(tmp_path):
    path = tmp_path / "crisisops.db"
    reset_db(path, scenario=None)
    conn = connect(path)
    try:
        rows = conn.execute("SELECT id, usable_stock, x_km FROM warehouses ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [("W1", 100, None), ("W2", 80, None)]


def test_scenario_with_unknown_column_is_rejected(tmp_path):
    data = load_scenario()
    data["warehouses"][0]["stock_typo"] = 5
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="stock_typo"):
        reset_db(tmp_path / "crisisops.db", scenario=bad)


def test_set_stock_and_incident(scenario_db):
    apply_operation(scenario_db, {"action": "set_stock", "warehouse": "W1", "quantity": 75},
                    "Set Warehouse A stock to 75 kits.")
    stock = scenario_db.execute("SELECT usable_stock FROM warehouses WHERE id = 'W1'").fetchone()[0]
    assert stock == 75
    assert [tuple(r) for r in _incidents(scenario_db)] == [("stock_set", "W1")]


def test_negative_set_stock_rolls_back(scenario_db):
    with pytest.raises(sqlite3.IntegrityError):
        set_warehouse_stock(scenario_db, "W1", -1, "impossible")
    stock = scenario_db.execute("SELECT usable_stock FROM warehouses WHERE id = 'W1'").fetchone()[0]
    assert stock == 300 and _incidents(scenario_db) == []


@pytest.mark.parametrize("operation", [
    {"action": "teleport_stock", "warehouse": "W1"},
    {"action": "decrease_stock", "warehouse": "W1"},                   # no quantity
    {"action": "decrease_stock", "warehouse": "W1", "quantity": -5},   # negative
    {"action": "close_route", "warehouse": "W1"},                      # no shelter
    {"action": "truck_offline", "truck": "T99"},                       # unknown id
])
def test_apply_operation_rejects_bad_input(scenario_db, operation):
    with pytest.raises(ValueError):
        apply_operation(scenario_db, operation, "bad input")
    assert _incidents(scenario_db) == []


def test_every_parser_action_is_supported(scenario_db):
    """Guards against the parser and the database drifting apart."""
    example = {
        "increase_stock": {"warehouse": "W1", "quantity": 10},
        "decrease_stock": {"warehouse": "W1", "quantity": 10},
        "set_stock": {"warehouse": "W1", "quantity": 50},
        "warehouse_offline": {"warehouse": "W2"},
        "warehouse_online": {"warehouse": "W2"},
        "set_demand": {"shelter": "S3", "quantity": 120},
        "truck_offline": {"truck": "T1"},
        "truck_online": {"truck": "T1"},
        "close_route": {"warehouse": "W4", "shelter": "S3"},
        "open_route": {"warehouse": "W4", "shelter": "S3"},
    }
    actions = [a.value for a in IncidentAction]
    assert set(actions) == set(example)
    for action in actions:
        apply_operation(scenario_db, {"action": action, **example[action]}, action)
    assert _count(scenario_db, "incidents") == len(actions)


def test_db_replay_matches_in_memory_replay(scenario_db):
    """The demo events give the same network in SQLite and in memory."""
    sc = Scenario.load()

    def key(net):
        return (net.stock, sorted((t.id, t.warehouse_id, t.capacity_kits) for t in net.trucks),
                sorted((s.id, s.demand, s.priority) for s in net.shelters), net.routes)

    assert key(Network.from_db(scenario_db)) == key(sc.network())
    for event in sc.events:
        apply_operation(scenario_db, event["operation"], event["text"])
        sc.apply(event["operation"])
        assert key(Network.from_db(scenario_db)) == key(sc.network())
    assert _count(scenario_db, "incidents") == len(sc.events)
