"""Read-only display routes for the CrisisOps dashboard.

Kept separate from ``backend/api.py`` (incident parse/apply) so the two can
change without merge conflicts. Nothing here writes to the database.

    GET  /state          Current State panel: counts, warehouses, shelters,
                         trucks, routes, and "Last Updated"
    GET  /plan           Food assignments + shelter shortages for the current state
    POST /plan/preview   Before vs after for one operation, without saving it

``POST /plan/preview`` takes the same body as ``POST /incidents/apply``, so the
frontend can preview an interpreted incident and then confirm it with the same
payload.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ai.incident_parser import IncidentUpdate
from backend.config import get_allocator_settings
from init_db import apply_operation, available_inventory, connect, resolve_operation
from optimization.allocator import plan_from_db

router = APIRouter(tags=["display"])

Method = Literal["optimal", "greedy", "compare"]


def _natural_key(value: str) -> list[Any]:
    """Sort 'S2' before 'S10'."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sqlite_utc_to_iso(value: str | None) -> str | None:
    """'2026-10-23 19:30:12' (SQLite datetime('now'), UTC) -> ISO-8601 with Z."""
    return value.replace(" ", "T") + "Z" if value else None


def _open() -> sqlite3.Connection:
    """Connect, mapping a missing or uninitialized database to HTTP 503."""
    conn = connect()
    try:
        conn.execute("SELECT 1 FROM warehouses LIMIT 1;")
    except sqlite3.OperationalError as exc:
        conn.close()
        raise HTTPException(
            status_code=503,
            detail=f"Database is not initialized ({exc}). Run: python init_db.py reset",
        ) from exc
    return conn


def _names(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    warehouses = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM warehouses;")}
    shelters = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM shelters;")}
    return warehouses, shelters


def _available_kits(conn: sqlite3.Connection) -> int:
    return sum(r["usable_stock"] for r in available_inventory(conn))


def _decorate(plans: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """Add display names and a drive-time ETA so the frontend needs no joins.

    eta_minutes is the one-way drive time of a trip (distance / average
    speed), excluding loading.
    """
    wh_names, sh_names = _names(conn)
    speed = get_allocator_settings().avg_speed_kmh
    for plan in plans.values():
        for s in plan["shipments"]:
            s["warehouse_name"] = wh_names.get(s["warehouse_id"], s["warehouse_id"])
            s["shelter_name"] = sh_names.get(s["shelter_id"], s["shelter_id"])
            s["eta_minutes"] = round(s["distance_km"] / speed * 60)
        for s in plan["shelters"]:
            s["shelter_name"] = sh_names.get(s["shelter_id"], s["shelter_id"])
    return plans


# --- GET /state --------------------------------------------------------------


@router.get("/state")
def get_state() -> dict[str, Any]:
    """Everything the Current State panel shows, from one consistent snapshot.

    Lists include offline warehouses, broken-down trucks, and closed routes
    (flagged), unlike the planner's retrieval queries, which hide them.
    ``last_updated`` is the newest incident time (UTC), or null before any
    incident; ``generated_at`` is when this response was built.
    """
    conn = _open()
    try:
        warehouses = [dict(r) for r in conn.execute(
            """SELECT w.id, w.name, w.location, w.usable_stock, w.is_operational,
                      w.x_km, w.y_km,
                      COUNT(t.id) AS trucks_total,
                      COALESCE(SUM(t.is_operational), 0) AS trucks_operational
               FROM warehouses w LEFT JOIN trucks t ON t.warehouse_id = w.id
               GROUP BY w.id;""")]
        shelters = [dict(r) for r in conn.execute(
            "SELECT id, name, location, demand, priority, x_km, y_km FROM shelters;")]
        trucks = [dict(r) for r in conn.execute(
            "SELECT id, warehouse_id, capacity_kits, is_operational FROM trucks;")]
        routes = [dict(r) for r in conn.execute(
            "SELECT warehouse_id, shelter_id, distance_km, is_open FROM delivery_routes;")]
        available = _available_kits(conn)
        last = conn.execute("SELECT MAX(created_at) FROM incidents;").fetchone()[0]
    finally:
        conn.close()

    for row in warehouses + trucks:
        row["is_operational"] = bool(row["is_operational"])
    for row in routes:
        row["is_open"] = bool(row["is_open"])
    warehouses.sort(key=lambda r: _natural_key(r["id"]))
    shelters.sort(key=lambda r: _natural_key(r["id"]))
    trucks.sort(key=lambda r: _natural_key(r["id"]))
    routes.sort(key=lambda r: (_natural_key(r["warehouse_id"]), _natural_key(r["shelter_id"])))

    return {
        "last_updated": _sqlite_utc_to_iso(last),
        "generated_at": _now_iso(),
        "summary": {
            "available_kits": available,
            "total_stock": sum(w["usable_stock"] for w in warehouses),
            "warehouses": len(warehouses),
            "warehouses_operational": sum(w["is_operational"] for w in warehouses),
            "shelters": len(shelters),
            "total_demand": sum(s["demand"] for s in shelters),
            "trucks": len(trucks),
            "trucks_operational": sum(t["is_operational"] for t in trucks),
            "routes_closed": sum(not r["is_open"] for r in routes),
        },
        "warehouses": warehouses,
        "shelters": shelters,
        "trucks": trucks,
        "routes": routes,
    }


# --- GET /plan ---------------------------------------------------------------


@router.get("/plan")
def get_plan(
    method: Method | None = Query(
        default=None, description="Planner to run. Omit to use ALLOCATOR_METHOD."
    ),
) -> dict[str, Any]:
    """Delivery plan for the current database state.

    ``plans`` is keyed by method name, the same shape ``POST /incidents/apply``
    returns. In each plan, ``shipments`` fills the Food Assignments table and
    ``shelters`` (demand, delivered, unmet, coverage) the Shelter Shortage
    table.
    """
    conn = _open()
    try:
        plans = _decorate(plan_from_db(conn, method=method), conn)
    finally:
        conn.close()
    return {"generated_at": _now_iso(), "plans": plans}


# --- POST /plan/preview ------------------------------------------------------


class PreviewRequest(BaseModel):
    """Same shape as the apply request; ``text`` is optional here."""

    operation: IncidentUpdate
    text: str = Field(default="Preview (not saved)", min_length=1)
    method: Method | None = Field(
        default=None, description="Planner to run. Omit to use ALLOCATOR_METHOD."
    )


def _metric_rows(before: dict, after: dict, kits_before: int, kits_after: int) -> list[dict]:
    """Rows for the Before vs After table."""
    b, a = before["summary"], after["summary"]

    def pct(part: int, whole: int) -> float:
        return round(100.0 * part / whole, 1) if whole else 100.0

    rows = [
        ("kits_delivered", "Kits delivered", b["total_delivered"], a["total_delivered"]),
        ("coverage_pct", "Demand covered (%)",
         pct(b["total_delivered"], b["total_demand"]), pct(a["total_delivered"], a["total_demand"])),
        ("worst_shelter_coverage_pct", "Worst-off shelter covered (%)",
         round(100 * b["worst_coverage"], 1), round(100 * a["worst_coverage"], 1)),
        ("unmet_kits", "Unmet demand (kits)",
         b["total_demand"] - b["total_delivered"], a["total_demand"] - a["total_delivered"]),
        ("unassigned_kits", "Available kits not shipped",
         kits_before - b["total_delivered"], kits_after - a["total_delivered"]),
        ("shelters_with_no_kits", "Shelters receiving nothing",
         len(b["shelters_with_nothing"]), len(a["shelters_with_nothing"])),
        ("truck_hours", "Truck-hours used", b["truck_hours"], a["truck_hours"]),
    ]
    return [{"key": k, "label": label, "before": x, "after": y, "change": round(y - x, 2)}
            for k, label, x, y in rows]


def _shelter_rows(before: dict, after: dict) -> list[dict]:
    """Per-shelter changes; demand can change too (set_demand)."""
    prior = {s["shelter_id"]: s for s in before["shelters"]}
    rows = []
    for s in after["shelters"]:
        p = prior.get(s["shelter_id"], {"demand": 0, "delivered": 0})
        rows.append({
            "shelter_id": s["shelter_id"],
            "shelter_name": s["shelter_name"],
            "demand_before": p["demand"],
            "demand_after": s["demand"],
            "delivered_before": p["delivered"],
            "delivered_after": s["delivered"],
            "change": s["delivered"] - p["delivered"],
        })
    return rows


@router.post("/plan/preview")
def preview_plan(request: PreviewRequest) -> dict[str, Any]:
    """Show what one operation would do to the plan, without saving it.

    The current database is copied into memory, the operation is applied to
    the copy, and both states are planned. Returns HTTP 422 for an invalid
    operation (unknown id, missing field, stock or demand going negative).
    Targets may be database ids (W1, S3, T1) or what /incidents/parse returns
    (A, 3), as for /incidents/apply; the response echoes the resolved ids.
    """
    operation = request.operation.model_dump(mode="json", exclude_none=True)
    disk = _open()
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    try:
        disk.backup(scratch)
        scratch.execute("PRAGMA foreign_keys = ON;")
        before = _decorate(plan_from_db(disk, method=request.method), disk)
        kits_before = _available_kits(disk)
        try:
            operation = resolve_operation(scratch, operation)
            apply_operation(scratch, operation, request.text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=422,
                detail="Operation rejected because stock or demand would become negative.",
            ) from exc
        after = _decorate(plan_from_db(scratch, method=request.method), scratch)
        kits_after = _available_kits(scratch)
    finally:
        scratch.close()
        disk.close()

    comparison = {
        name: {
            "metrics": _metric_rows(before[name], after[name], kits_before, kits_after),
            "shelters": _shelter_rows(before[name], after[name]),
        }
        for name in before
    }
    return {
        "saved": False,
        "generated_at": _now_iso(),
        "operation": operation,
        "before": before,
        "after": after,
        "comparison": comparison,
    }
