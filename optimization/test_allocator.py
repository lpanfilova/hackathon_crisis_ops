"""Tests for the allocator and the demo scenario replay."""

import pytest

import optimization.allocator as allocator
from backend.config import AllocatorSettings
from init_db import connect, reset_db
from optimization.allocator import (
    Network,
    PlanningParams,
    Shelter,
    Truck,
    allocate,
    allocate_greedy,
    allocate_optimal,
    plan_from_db,
)
from optimization.scenario import Scenario

PARAMS = PlanningParams(horizon_hours=8, avg_speed_kmh=35, handling_hours_per_trip=0.5)
SCENARIO_PARAMS = PlanningParams(horizon_hours=6, avg_speed_kmh=35, handling_hours_per_trip=0.5)


def _check_plan(net: Network, params: PlanningParams, plan) -> None:
    """Every plan must respect stock, demand, truck time, capacity, and routes."""
    trucks = {t.id: t for t in net.trucks}
    shipped, hours = {}, {}
    for s in plan.shipments:
        truck = trucks[s.truck_id]                       # operational trucks only
        assert s.warehouse_id == truck.warehouse_id
        assert (s.warehouse_id, s.shelter_id) in net.routes   # open routes only
        assert s.trips >= 1 and s.kits <= s.trips * truck.capacity_kits
        shipped[s.warehouse_id] = shipped.get(s.warehouse_id, 0) + s.kits
        hours[s.truck_id] = (hours.get(s.truck_id, 0)
                             + s.trips * params.round_trip_hours(s.distance_km))
    for wid, kits in shipped.items():
        assert kits <= net.stock[wid]
    for tid, h in hours.items():
        assert h <= params.horizon_hours + 1e-6
    demand = {s.id: s.demand for s in net.shelters}
    for r in plan.shelters:
        assert 0 <= r.delivered <= demand[r.shelter_id]


@pytest.fixture
def bottleneck_net() -> Network:
    """W1 can reach S1 and S2; tiny W2 is the only way to S3."""
    return Network(
        stock={"W1": 100, "W2": 10},
        trucks=[Truck("T1", "W1", 100), Truck("T2", "W2", 100)],
        shelters=[Shelter("S1", 100, 1), Shelter("S2", 100, 2), Shelter("S3", 100, 2)],
        routes={("W1", "S1"): 1.0, ("W1", "S2"): 1.0, ("W2", "S3"): 1.0},
    )


def test_greedy_follows_priority(bottleneck_net):
    plan = allocate_greedy(bottleneck_net, PARAMS)
    got = {r.shelter_id: r.delivered for r in plan.shelters}
    assert got == {"S1": 100, "S2": 0, "S3": 10}
    _check_plan(bottleneck_net, PARAMS, plan)


def test_optimal_is_leximin_fair(bottleneck_net):
    """S3 is capped at 10% by its supply, but that must not drag S1/S2 down:
    they split W1 evenly instead of anything-above-10%."""
    plan = allocate_optimal(bottleneck_net, PARAMS)
    got = {r.shelter_id: r.delivered for r in plan.shelters}
    assert got == {"S1": 50, "S2": 50, "S3": 10}
    assert plan.status == "optimal"
    _check_plan(bottleneck_net, PARAMS, plan)


def test_truck_time_limits_trips():
    """One 40-kit truck, a 3.5 h round trip, 8 h window: two trips, 80 kits."""
    net = Network(stock={"W1": 500}, trucks=[Truck("T1", "W1", 40)],
                  shelters=[Shelter("S1", 500)], routes={("W1", "S1"): 52.5})
    for plan in (allocate_greedy(net, PARAMS), allocate_optimal(net, PARAMS)):
        assert plan.summary()["total_delivered"] == 80
        assert plan.summary()["trips"] == 2
        _check_plan(net, PARAMS, plan)


def test_unreachable_shelter_is_flagged():
    net = Network(stock={"W1": 50}, trucks=[Truck("T1", "W1", 50)],
                  shelters=[Shelter("S1", 50), Shelter("S2", 50)],
                  routes={("W1", "S1"): 5.0})
    plan = allocate_optimal(net, PARAMS)
    assert plan.unreachable == ["S2"]
    assert {r.shelter_id: r.delivered for r in plan.shelters} == {"S1": 50, "S2": 0}


def test_no_trucks_means_nothing_moves():
    net = Network(stock={"W1": 50}, trucks=[], shelters=[Shelter("S1", 50)],
                  routes={("W1", "S1"): 5.0})
    plan = allocate_optimal(net, PARAMS)
    assert plan.shipments == [] and plan.unreachable == ["S1"]


def test_solver_failure_falls_back_to_greedy(bottleneck_net, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(allocator, "milp", broken)
    plan = allocate_optimal(bottleneck_net, PARAMS)
    assert plan.status.startswith("fallback_greedy")
    assert plan.summary() == allocate_greedy(bottleneck_net, PARAMS).summary()


def test_plan_serializes(bottleneck_net):
    d = allocate_optimal(bottleneck_net, PARAMS).to_dict()
    assert d["summary"]["worst_shelter"] == "S3"
    assert {"shelters", "shipments", "unreachable", "status"} <= d.keys()


def test_network_from_seed_database(tmp_path):
    db = tmp_path / "crisisops.db"
    reset_db(db, scenario=None)                            # small seed
    conn = connect(db)
    try:
        net = Network.from_db(conn)
    finally:
        conn.close()
    assert net.stock == {"W1": 100, "W2": 80}
    assert len(net.trucks) == 2 and len(net.routes) == 6
    plan = allocate_optimal(net, PARAMS)
    assert plan.summary()["total_delivered"] == 170       # supply 180 >= demand 170
    assert plan.summary()["worst_coverage"] == 1.0
    _check_plan(net, PARAMS, plan)


# --- Demo scenario -----------------------------------------------------------


def test_demo_replay_optimal_never_worse_for_worst_shelter():
    """The presentation claim: after every event, the optimizer's worst-off
    shelter is at least as well covered as greedy's, and every plan is valid."""
    sc = Scenario.load()
    params = SCENARIO_PARAMS
    for op in [None] + [e["operation"] for e in sc.events]:
        if op:
            sc.apply(op)
        net = sc.network()
        g, o = allocate_greedy(net, params), allocate_optimal(net, params)
        _check_plan(net, params, g)
        _check_plan(net, params, o)
        assert not o.status.startswith("fallback")
        assert o.summary()["worst_coverage"] >= g.summary()["worst_coverage"]


# --- Method selection --------------------------------------------------------


def test_allocate_method_selection(bottleneck_net):
    assert allocate(bottleneck_net, "optimal", PARAMS).keys() == {"optimal"}
    assert allocate(bottleneck_net, "greedy", PARAMS).keys() == {"greedy"}
    assert allocate(bottleneck_net, "compare", PARAMS).keys() == {"greedy", "optimal"}
    with pytest.raises(ValueError):
        allocate(bottleneck_net, "fastest", PARAMS)


def test_plan_from_db_uses_settings_and_allows_override(tmp_path):
    db = tmp_path / "crisisops.db"
    reset_db(db)                                           # demo scenario
    settings = AllocatorSettings(_env_file=None, allocator_method="greedy")
    conn = connect(db)
    try:
        assert plan_from_db(conn, settings=settings).keys() == {"greedy"}
        both = plan_from_db(conn, method="compare", settings=settings)
    finally:
        conn.close()
    assert both.keys() == {"greedy", "optimal"}
    assert both["optimal"]["summary"]["total_demand"] == 900   # Bay County


def test_scenario_rejects_negative_stock():
    sc = Scenario.load()
    with pytest.raises(ValueError):
        sc.apply({"action": "decrease_stock", "warehouse": "W4", "quantity": 10_000})


def test_scenario_rejects_unknown_ids_and_actions():
    sc = Scenario.load()
    with pytest.raises(ValueError):
        sc.apply({"action": "truck_offline", "truck": "T99"})
    with pytest.raises(ValueError):
        sc.apply({"action": "teleport", "warehouse": "W1"})
