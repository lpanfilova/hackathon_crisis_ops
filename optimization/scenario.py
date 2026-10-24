"""Demo scenarios for the allocator.

Loads a scenario JSON (default: data/demo_scenario.json), replays the
manager's events in memory, and compares greedy vs optimal after each one.
Event operations use the same action names as ``ai.incident_parser`` with
database ids (W1, S3, T1), the same format ``init_db.apply_operation`` takes.
Planning numbers come from ``backend.config.AllocatorSettings`` (.env).
Nothing here touches the database; ``python init_db.py demo`` replays the
same events against the real database.

Usage:
    python -m optimization.scenario              # replay all events
    python -m optimization.scenario --detail     # also per-shelter coverage
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from backend.config import get_allocator_settings
from optimization.allocator import (
    AllocationPlan,
    Network,
    PlanningParams,
    Shelter,
    Truck,
    allocate_greedy,
    allocate_optimal,
)

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "demo_scenario.json"


class Scenario:
    """Mutable in-memory copy of a scenario's state."""

    def __init__(self, data: dict[str, Any]):
        self.data = copy.deepcopy(data)
        self._wh = {w["id"]: w for w in self.data["warehouses"]}
        self._sh = {s["id"]: s for s in self.data["shelters"]}
        self._tr = {t["id"]: t for t in self.data["trucks"]}
        self._rt = {(r["warehouse_id"], r["shelter_id"]): r
                    for r in self.data["delivery_routes"]}

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> "Scenario":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.data.get("events", [])

    def network(self) -> Network:
        """Same filtering as the init_db.py retrieval queries."""
        up = {wid for wid, w in self._wh.items() if w["is_operational"]}
        return Network(
            stock={wid: self._wh[wid]["usable_stock"] for wid in up},
            trucks=[Truck(t["id"], t["warehouse_id"], t["capacity_kits"])
                    for t in self._tr.values()
                    if t["is_operational"] and t["warehouse_id"] in up],
            shelters=[Shelter(s["id"], s["demand"], s["priority"])
                      for s in self._sh.values()],
            routes={k: r["distance_km"] for k, r in self._rt.items()
                    if r["is_open"] and k[0] in up},
        )

    def apply(self, op: dict[str, Any]) -> None:
        """Apply one operation, mirroring the init_db.py functions."""
        action = op["action"]
        if action in {"increase_stock", "decrease_stock", "set_stock"}:
            w = self._get(self._wh, op["warehouse"], "warehouse")
            qty = int(op["quantity"])
            new = {"increase_stock": w["usable_stock"] + qty,
                   "decrease_stock": w["usable_stock"] - qty,
                   "set_stock": qty}[action]
            if new < 0:
                raise ValueError(f"{w['id']} stock would go negative ({new})")
            w["usable_stock"] = new
        elif action in {"warehouse_offline", "warehouse_online"}:
            self._get(self._wh, op["warehouse"], "warehouse")["is_operational"] = (
                action == "warehouse_online")
        elif action == "set_demand":
            if int(op["quantity"]) < 0:
                raise ValueError("demand cannot be negative")
            self._get(self._sh, op["shelter"], "shelter")["demand"] = int(op["quantity"])
        elif action in {"truck_offline", "truck_online"}:
            self._get(self._tr, op["truck"], "truck")["is_operational"] = (
                action == "truck_online")
        elif action in {"close_route", "open_route"}:
            key = (op["warehouse"], op["shelter"])
            if key not in self._rt:
                raise ValueError(f"no route {key[0]} -> {key[1]}")
            self._rt[key]["is_open"] = action == "open_route"
        else:
            raise ValueError(f"unknown action {action!r}")

    @staticmethod
    def _get(table: dict[str, dict], key: str, kind: str) -> dict:
        if key not in table:
            raise ValueError(f"no {kind} with id {key!r}")
        return table[key]


# --- CLI ---------------------------------------------------------------------


def _line(label: str, plan: AllocationPlan) -> str:
    s = plan.summary()
    nothing = ",".join(s["shelters_with_nothing"]) or "-"
    return (f"  {label:8} delivered {s['total_delivered']:4}/{s['total_demand']:<4} "
            f"worst {s['worst_coverage']:>4.0%} ({s['worst_shelter']:>3})  "
            f"no kits: {nothing:<12} truck-hrs {s['truck_hours']:5.1f}  "
            f"{plan.solve_ms:6.1f} ms")


def _detail(greedy: AllocationPlan, optimal: AllocationPlan) -> str:
    opt = {s.shelter_id: s for s in optimal.shelters}
    rows = ["    shelter  pri  demand   greedy   optimal"]
    for g in greedy.shelters:
        o = opt[g.shelter_id]
        rows.append(f"    {g.shelter_id:>7}  {g.priority:>3}  {g.demand:>6}   "
                    f"{g.coverage:>6.0%}   {o.coverage:>7.0%}")
    return "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a CrisisOps scenario")
    ap.add_argument("--scenario", default=str(DEFAULT_PATH))
    ap.add_argument("--detail", action="store_true", help="per-shelter coverage")
    args = ap.parse_args()

    sc = Scenario.load(args.scenario)
    settings = get_allocator_settings()
    params = PlanningParams.from_settings(settings)
    print(f"{sc.data.get('name', 'Scenario')}  "
          f"(window {params.horizon_hours:g} h, {params.avg_speed_kmh:g} km/h, "
          f"{params.handling_hours_per_trip:g} h handling per trip)\n")

    steps = [("Baseline", None)] + [(e["text"], e["operation"]) for e in sc.events]
    for i, (text, op) in enumerate(steps):
        if op:
            sc.apply(op)
        net = sc.network()
        g = allocate_greedy(net, params)
        o = allocate_optimal(net, params,
                             time_limit_s=settings.optimizer_time_limit_s,
                             rounding_time_s=settings.optimizer_rounding_time_s)
        print(f"[{i}] {text}")
        print(_line("greedy", g))
        print(_line("optimal", o))
        if o.unreachable:
            print(f"  unreachable: {', '.join(o.unreachable)}")
        if o.status != "optimal":
            print(f"  note: {o.status}")
        if args.detail:
            print(_detail(g, o))
        print()


if __name__ == "__main__":
    main()
