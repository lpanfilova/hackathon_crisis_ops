"""Stock reallocation for CrisisOps.

Given a snapshot of what is available right now (operational warehouses and
their stock, operational trucks, open routes, shelter demand), produce a
delivery plan for the next planning window. Recompute after every incident.

Two planners share one interface and return the same ``AllocationPlan``:

* ``allocate_greedy``: the obvious dispatcher. Serve shelters in priority
  order, each from its nearest warehouse, until stock or truck time runs out.
  Kept as the baseline for comparison and as a fallback.
* ``allocate_optimal``: fairness-first optimizer on SciPy's HiGHS solver, in
  two phases:

  1. Fair targets (linear programs, fractional trips allowed). Minimize the
     worst shelter's unmet share, lock in shelters that can't do better, and
     repeat for the rest (leximin). Exact and fast.
  2. Whole trips (one mixed-integer program). Find an integer trip plan that
     gets as close to those targets as possible, then delivers the most kits,
     then uses the fewest truck-hours.

  Solving the fairness rounds directly with integer trips is also exact, but
  HiGHS can need tens of seconds per round when supply is tight. The
  two-phase version answers in well under a second.

Time model
----------
Trucks make repeated round trips from their home warehouse within a planning
horizon. A round trip to a shelter takes
``2 * distance / avg_speed + handling`` hours and carries up to the truck's
capacity. Trip counts are integers, so a half-empty trip still costs a full
trip. Deliberately simple: no traffic, no multi-stop routes, no shift changes.

Shelter priority is used by the greedy baseline only. The optimizer treats
every shelter's unmet share equally.

Nothing in this module writes to the database.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

_EPS = 1e-6


def _natural_key(value: str) -> list[Any]:
    """Sort 'S2' before 'S10'."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value)]


# --- Inputs ------------------------------------------------------------------


@dataclass(frozen=True)
class PlanningParams:
    """Assumptions for one planning window (defaults match backend/config.py)."""

    horizon_hours: float = 6.0
    avg_speed_kmh: float = 35.0
    handling_hours_per_trip: float = 0.5

    @classmethod
    def from_settings(cls, settings) -> "PlanningParams":
        """Build from ``backend.config.AllocatorSettings``."""
        return cls(
            horizon_hours=settings.planning_horizon_hours,
            avg_speed_kmh=settings.avg_speed_kmh,
            handling_hours_per_trip=settings.handling_hours_per_trip,
        )

    def round_trip_hours(self, distance_km: float) -> float:
        return 2.0 * distance_km / self.avg_speed_kmh + self.handling_hours_per_trip


@dataclass(frozen=True)
class Truck:
    id: str
    warehouse_id: str
    capacity_kits: int


@dataclass(frozen=True)
class Shelter:
    id: str
    demand: int
    priority: int = 1


@dataclass
class Network:
    """What is available right now.

    Only operational warehouses and trucks and open routes belong here. The
    retrieval queries in ``init_db.py`` already filter that way.
    """

    stock: dict[str, int]                  # warehouse_id -> usable kits
    trucks: list[Truck]
    shelters: list[Shelter]
    routes: dict[tuple[str, str], float]   # (warehouse_id, shelter_id) -> km

    @classmethod
    def from_db(cls, conn) -> "Network":
        """Build a snapshot from the CrisisOps SQLite database."""
        from init_db import (
            available_inventory,
            open_routes,
            operational_trucks,
            shelter_demand,
        )

        return cls(
            stock={r["id"]: r["usable_stock"] for r in available_inventory(conn)},
            trucks=[
                Truck(r["id"], r["warehouse_id"], r["capacity_kits"])
                for r in operational_trucks(conn)
            ],
            shelters=[
                Shelter(r["id"], r["demand"], r["priority"])
                for r in shelter_demand(conn)
            ],
            routes={
                (r["warehouse_id"], r["shelter_id"]): r["distance_km"]
                for r in open_routes(conn)
            },
        )


# --- Outputs -----------------------------------------------------------------


@dataclass
class Shipment:
    truck_id: str
    warehouse_id: str
    shelter_id: str
    kits: int
    trips: int
    hours: float
    distance_km: float


@dataclass
class ShelterResult:
    shelter_id: str
    priority: int
    demand: int
    delivered: int

    @property
    def unmet(self) -> int:
        return self.demand - self.delivered

    @property
    def coverage(self) -> float:
        return 1.0 if self.demand == 0 else self.delivered / self.demand


@dataclass
class AllocationPlan:
    method: str               # "greedy" or "optimal"
    status: str               # "ok" | "optimal" | "time_limited" | "fallback_greedy: ..."
    shipments: list[Shipment]
    shelters: list[ShelterResult]
    unreachable: list[str]    # shelters with demand but no usable truck/route/stock
    solve_ms: float
    solver_calls: int = 0

    def summary(self) -> dict[str, Any]:
        needy = [s for s in self.shelters if s.demand > 0]
        worst = min(needy, key=lambda s: (s.coverage, _natural_key(s.shelter_id)),
                    default=None)
        return {
            "total_demand": sum(s.demand for s in self.shelters),
            "total_delivered": sum(s.delivered for s in self.shelters),
            "worst_coverage": round(worst.coverage, 3) if worst else 1.0,
            "worst_shelter": worst.shelter_id if worst else None,
            "shelters_with_nothing": [s.shelter_id for s in needy if s.delivered == 0],
            "truck_hours": round(sum(x.hours for x in self.shipments), 2),
            "trips": sum(x.trips for x in self.shipments),
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form for the API."""
        return {
            "method": self.method,
            "status": self.status,
            "solve_ms": round(self.solve_ms, 1),
            "solver_calls": self.solver_calls,
            "summary": self.summary(),
            "shelters": [
                {**asdict(s), "unmet": s.unmet, "coverage": round(s.coverage, 3)}
                for s in self.shelters
            ],
            "shipments": [asdict(x) for x in self.shipments],
            "unreachable": self.unreachable,
        }


def _build_plan(
    method: str,
    status: str,
    net: Network,
    params: PlanningParams,
    loads: dict[tuple[str, str], tuple[int, int]],
    started: float,
    solver_calls: int = 0,
) -> AllocationPlan:
    """Turn {(truck_id, shelter_id): (kits, trips)} into an AllocationPlan."""
    truck_wh = {t.id: t.warehouse_id for t in net.trucks}
    delivered = {s.id: 0 for s in net.shelters}
    shipments = []
    for (tid, sid), (kits, trips) in sorted(
        loads.items(), key=lambda kv: (_natural_key(kv[0][0]), _natural_key(kv[0][1]))
    ):
        if kits <= 0:
            continue
        wid = truck_wh[tid]
        km = net.routes[(wid, sid)]
        shipments.append(
            Shipment(tid, wid, sid, kits, trips,
                     round(trips * params.round_trip_hours(km), 2), km)
        )
        delivered[sid] += kits

    shelters = [
        ShelterResult(s.id, s.priority, s.demand, delivered[s.id])
        for s in sorted(net.shelters, key=lambda s: _natural_key(s.id))
    ]
    reachable = {p.shelter_id for p in _usable_pairs(net, params)}
    unreachable = [s.shelter_id for s in shelters
                   if s.demand > 0 and s.shelter_id not in reachable]
    return AllocationPlan(
        method=method,
        status=status,
        shipments=shipments,
        shelters=shelters,
        unreachable=unreachable,
        solve_ms=(time.perf_counter() - started) * 1000,
        solver_calls=solver_calls,
    )


# --- Greedy baseline ---------------------------------------------------------


def allocate_greedy(net: Network, params: PlanningParams = PlanningParams()) -> AllocationPlan:
    """Priority order, nearest warehouse first, biggest truck first."""
    started = time.perf_counter()
    stock = dict(net.stock)
    time_left = {t.id: params.horizon_hours for t in net.trucks}
    trucks_at: dict[str, list[Truck]] = {}
    for t in net.trucks:
        trucks_at.setdefault(t.warehouse_id, []).append(t)

    loads: dict[tuple[str, str], list[int]] = {}
    for shelter in sorted(net.shelters, key=lambda s: (s.priority, _natural_key(s.id))):
        need = shelter.demand
        nearest = sorted(
            (km, wid) for (wid, sid), km in net.routes.items()
            if sid == shelter.id and wid in stock
        )
        for km, wid in nearest:
            rt = params.round_trip_hours(km)
            for truck in sorted(trucks_at.get(wid, []),
                                key=lambda t: (-t.capacity_kits, _natural_key(t.id))):
                while need > 0 and stock[wid] > 0 and time_left[truck.id] >= rt - _EPS:
                    load = min(truck.capacity_kits, need, stock[wid])
                    entry = loads.setdefault((truck.id, shelter.id), [0, 0])
                    entry[0] += load
                    entry[1] += 1
                    stock[wid] -= load
                    need -= load
                    time_left[truck.id] -= rt

    return _build_plan("greedy", "ok", net, params,
                       {k: (v[0], v[1]) for k, v in loads.items()}, started)


# --- Fairness-first optimizer ------------------------------------------------


@dataclass(frozen=True)
class _Pair:
    """A truck that can serve a shelter: open route, stock, time for one trip."""

    truck: Truck
    shelter_id: str
    round_trip_hours: float
    max_trips: int


def _usable_pairs(net: Network, params: PlanningParams) -> list[_Pair]:
    demand = {s.id: s.demand for s in net.shelters}
    pairs = []
    for truck in sorted(net.trucks, key=lambda t: _natural_key(t.id)):
        if truck.capacity_kits <= 0 or net.stock.get(truck.warehouse_id, 0) <= 0:
            continue
        for sid in sorted(demand, key=_natural_key):
            km = net.routes.get((truck.warehouse_id, sid))
            if demand[sid] <= 0 or km is None:
                continue
            rt = params.round_trip_hours(km)
            max_trips = math.floor(params.horizon_hours / rt + 1e-9)
            if max_trips >= 1:
                pairs.append(_Pair(truck, sid, rt, max_trips))
    return pairs


class _SolveError(RuntimeError):
    pass


class _Model:
    """Trips n[p] and kits y[p] per truck-shelter pair, plus one continuous z
    (the worst unmet share in phase 1, the worst shortfall in phase 2).

    Variable layout: [n_0..n_{P-1}, y_0..y_{P-1}, z]

    Base constraints:
        y[p] <= capacity * n[p]                 (a trip carries at most a truckload)
        sum_p round_trip[p] * n[p] <= horizon   (per truck)
        sum_p y[p] <= stock                     (per warehouse)
        sum_p y[p] <= demand                    (per shelter)
    """

    def __init__(self, net: Network, params: PlanningParams,
                 pairs: list[_Pair], deadline: float):
        self.pairs = pairs
        self.P = len(pairs)
        self.n_vars = 2 * self.P + 1
        self.z = 2 * self.P
        self.deadline = deadline
        self.solver_calls = 0
        self.last_status = 0
        self.demand = {s.id: s.demand for s in net.shelters}

        self.to_shelter: dict[str, list[int]] = {}
        by_truck: dict[str, list[int]] = {}
        by_wh: dict[str, list[int]] = {}
        for p, pr in enumerate(pairs):
            self.to_shelter.setdefault(pr.shelter_id, []).append(p)
            by_truck.setdefault(pr.truck.id, []).append(p)
            by_wh.setdefault(pr.truck.warehouse_id, []).append(p)

        rows, lo, hi = [], [], []

        def add(coeffs: list[tuple[int, float]], low: float, high: float) -> None:
            row = np.zeros(self.n_vars)
            for i, v in coeffs:
                row[i] = v
            rows.append(row)
            lo.append(low)
            hi.append(high)

        P = self.P
        for p, pr in enumerate(pairs):
            add([(P + p, 1.0), (p, -float(pr.truck.capacity_kits))], -np.inf, 0.0)
        for ps in by_truck.values():
            add([(p, pairs[p].round_trip_hours) for p in ps], -np.inf, params.horizon_hours)
        for wid, ps in by_wh.items():
            add([(P + p, 1.0) for p in ps], -np.inf, float(net.stock[wid]))
        for sid, ps in self.to_shelter.items():
            add([(P + p, 1.0) for p in ps], -np.inf, float(self.demand[sid]))

        self.A = np.array(rows)
        self.lo = np.array(lo)
        self.hi = np.array(hi)

        self.ub = np.empty(self.n_vars)
        for p, pr in enumerate(pairs):
            self.ub[p] = pr.max_trips
            self.ub[P + p] = min(pr.truck.capacity_kits * pr.max_trips,
                                 self.demand[pr.shelter_id],
                                 net.stock[pr.truck.warehouse_id])
        self.ub[self.z] = 1.0
        self.integrality = np.ones(self.n_vars)
        self.integrality[self.z] = 0

    def delivery_row(self, sid: str) -> np.ndarray:
        row = np.zeros(self.n_vars)
        row[[self.P + p for p in self.to_shelter[sid]]] = 1.0
        return row

    def delivered(self, x: np.ndarray, sid: str) -> float:
        return float(sum(x[self.P + p] for p in self.to_shelter[sid]))

    def solve(self, c: np.ndarray, *, targets: dict[str, float] | None = None,
              floors: dict[str, float] | None = None, z_max: float = 1.0,
              integer: bool = False, time_limit: float | None = None) -> np.ndarray:
        """Minimize c @ x.

        targets: shelter -> kits; each shelter's shortfall from its target,
                 as a share of its demand, must be <= z
        floors:  shelter -> kits it must receive at least
        integer: False solves the relaxation (fractional trips and kits)
        """
        rows, lo, hi = [self.A], [self.lo], [self.hi]
        for sid, target in (targets or {}).items():
            row = self.delivery_row(sid) / self.demand[sid]
            row[self.z] = 1.0                      # delivered/demand + z >= target/demand
            rows.append(row[None, :]); lo.append([target / self.demand[sid]]); hi.append([np.inf])
        for sid, floor in (floors or {}).items():
            if floor > 0:
                rows.append(self.delivery_row(sid)[None, :])
                lo.append([floor]); hi.append([np.inf])

        remaining = self.deadline - time.perf_counter()
        if remaining <= 0:
            raise _SolveError("time limit reached")

        ub = self.ub.copy()
        ub[self.z] = z_max
        res = milp(
            c=c,
            constraints=LinearConstraint(np.vstack(rows), np.concatenate(lo), np.concatenate(hi)),
            integrality=self.integrality if integer else np.zeros(self.n_vars),
            bounds=Bounds(np.zeros(self.n_vars), ub),
            options={"time_limit": min(remaining, time_limit or remaining)},
        )
        self.solver_calls += 1
        self.last_status = res.status
        if res.x is None:                          # no feasible point found at all
            raise _SolveError(res.message)
        return res.x


def _fair_targets(model: _Model) -> dict[str, float]:
    """Phase 1: leximin kits per shelter with fractional trips allowed.

    Each round minimizes the worst unmet share among shelters not yet locked.
    Shelters at that level that cannot improve without someone else dropping
    below it are locked at it; the rest go to the next round.
    """
    demand = model.demand
    active = sorted(model.to_shelter, key=_natural_key)
    locked: dict[str, float] = {}
    minimize_z = np.zeros(model.n_vars)
    minimize_z[model.z] = 1.0

    while active:
        everyone = {sid: float(demand[sid]) for sid in active}
        x = model.solve(minimize_z, targets=everyone, floors=locked)
        share = {sid: 1 - model.delivered(x, sid) / demand[sid] for sid in active}
        z_star = max(share.values())               # read off the plan, not x[z]
        if z_star <= _EPS:
            locked.update(everyone)
            break

        candidates = [sid for sid in active if share[sid] >= z_star - 1e-7]
        blocked = []
        for sid in candidates:
            xs = model.solve(-model.delivery_row(sid), targets=everyone,
                             floors=locked, z_max=z_star + 1e-9)
            if model.delivered(xs, sid) <= demand[sid] * (1 - z_star) + 1e-4:
                blocked.append(sid)
        for sid in blocked or candidates:
            # A hair of slack keeps later rounds feasible under solver tolerance.
            locked[sid] = max(0.0, demand[sid] * (1 - z_star) - 1e-6)
            active.remove(sid)
    return locked


def allocate_optimal(net: Network, params: PlanningParams = PlanningParams(),
                     time_limit_s: float = 10.0,
                     rounding_time_s: float = 1.0) -> AllocationPlan:
    """Fairness-first plan (see module docstring for the two phases).

    status is "optimal" when the whole-trip plan is proven best,
    "time_limited" when the solver returned its best plan within
    ``rounding_time_s``, and "fallback_greedy: <reason>" if the solver failed.
    """
    started = time.perf_counter()
    pairs = _usable_pairs(net, params)
    if not pairs:
        return _build_plan("optimal", "optimal", net, params, {}, started)

    try:
        model = _Model(net, params, pairs, deadline=started + time_limit_s)
        targets = {sid: math.floor(t + 1e-6) for sid, t in _fair_targets(model).items()}

        # Phase 2 objective, in strict order of importance:
        #   1. worst shortfall from target (z)  2. most kits  3. fewest hours
        total_hours = sum(pr.round_trip_hours * pr.max_trips for pr in pairs)
        kit_weight = 1.0 / (total_hours + 1.0)               # one kit beats any hours
        total_kits = sum(model.ub[model.P:2 * model.P])
        z_weight = (total_kits + 1.0) * max(model.demand.values())  # one kit of z beats any kits
        c = np.zeros(model.n_vars)
        for p, pr in enumerate(pairs):
            c[p] = pr.round_trip_hours * kit_weight
            c[model.P + p] = -1.0
        c[model.z] = z_weight
        x = model.solve(c, targets=targets, integer=True, time_limit=rounding_time_s)
        status = "optimal" if model.last_status == 0 else "time_limited"
    except Exception as exc:  # noqa: BLE001 - any solver failure -> greedy
        plan = allocate_greedy(net, params)
        plan.method = "optimal"
        plan.status = f"fallback_greedy: {exc}"
        plan.solve_ms = (time.perf_counter() - started) * 1000
        return plan

    loads = {
        (pr.truck.id, pr.shelter_id): (int(round(x[model.P + p])), int(round(x[p])))
        for p, pr in enumerate(pairs)
    }
    return _build_plan("optimal", status, net, params, loads, started,
                       solver_calls=model.solver_calls)


METHODS = ("optimal", "greedy", "compare")


def allocate(
    net: Network,
    method: str = "optimal",
    params: PlanningParams = PlanningParams(),
    time_limit_s: float = 10.0,
    rounding_time_s: float = 1.0,
) -> dict[str, Any]:
    """Run one planner, or both, and return JSON-ready plans keyed by name.

    method "optimal" -> {"optimal": {...}}
    method "greedy"  -> {"greedy": {...}}
    method "compare" -> {"greedy": {...}, "optimal": {...}}
    """
    if method not in METHODS:
        raise ValueError(f"Unknown method {method!r}. Expected one of: {', '.join(METHODS)}")
    plans: dict[str, Any] = {}
    if method in ("greedy", "compare"):
        plans["greedy"] = allocate_greedy(net, params).to_dict()
    if method in ("optimal", "compare"):
        plans["optimal"] = allocate_optimal(
            net, params, time_limit_s=time_limit_s, rounding_time_s=rounding_time_s
        ).to_dict()
    return plans


def plan_from_db(conn, method: str | None = None, settings=None) -> dict[str, Any]:
    """Plan from the current database state using `.env` settings.

    This is the one call the API needs after applying an incident:

        plan_from_db(conn)                    # configured default method
        plan_from_db(conn, method="compare")  # per-request override

    ``settings`` defaults to ``backend.config.get_allocator_settings()``.
    """
    if settings is None:
        from backend.config import get_allocator_settings

        settings = get_allocator_settings()
    return allocate(
        Network.from_db(conn),
        method or settings.allocator_method,
        PlanningParams.from_settings(settings),
        time_limit_s=settings.optimizer_time_limit_s,
        rounding_time_s=settings.optimizer_rounding_time_s,
    )
