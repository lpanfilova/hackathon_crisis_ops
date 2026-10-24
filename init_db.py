#!/usr/bin/env python3
"""Initialize, reset, and operate the CrisisOps SQLite database.

This script is the single entry point for creating `crisisops.db` from
`schema.sql` + `seed.sql`, resetting it to the original demo state, and running
the manager's confirmed operations inside transactions (each of which also
records a row in `incidents`).

The allocation algorithm is intentionally NOT implemented here.

Usage:
    python init_db.py init          # create db if missing (no-op if present)
    python init_db.py reset         # drop + rebuild to original demo state
    python init_db.py status        # print current inventory/trucks/routes/demand
    python init_db.py demo          # reset, then apply the six example events

Programmatic use:
    from init_db import connect, apply_operation, reset_db
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

# --- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "crisisops.db"
SCHEMA_PATH = BASE_DIR / "backend" / "schema.sql"
SEED_PATH = BASE_DIR / "backend" / "seed.sql"


# --- Connection --------------------------------------------------------------
def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a connection with foreign keys enforced and row access by name.

    SQLite disables foreign key enforcement by default and it must be set per
    connection, so we always set it here.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# --- Build / reset -----------------------------------------------------------
def _run_sql_file(conn: sqlite3.Connection, path: Path) -> None:
    conn.executescript(path.read_text(encoding="utf-8"))


def reset_db(db_path: Path | str = DB_PATH) -> None:
    """Rebuild the database to the original demo state.

    schema.sql drops existing tables first, so this is a clean rebuild whether
    or not the file already exists. Wrapped in a single transaction.
    """
    conn = connect(db_path)
    try:
        with conn:  # transaction: commits on success, rolls back on error
            _run_sql_file(conn, SCHEMA_PATH)
            _run_sql_file(conn, SEED_PATH)
    finally:
        conn.close()


def init_db(db_path: Path | str = DB_PATH) -> bool:
    """Create the database only if it does not already exist.

    Returns True if it created the database, False if it was already present.
    """
    if Path(db_path).exists():
        return False
    reset_db(db_path)
    return True


# --- Incident recording ------------------------------------------------------
def _record_incident(
    conn: sqlite3.Connection,
    incident_type: str,
    target_id: str | None,
    description: str,
) -> None:
    conn.execute(
        "INSERT INTO incidents (incident_type, target_id, description) "
        "VALUES (?, ?, ?);",
        (incident_type, target_id, description),
    )


# --- Manager operations ------------------------------------------------------
# Each operation runs in a transaction (via `with conn:`) and records an
# incident. CHECK constraints in the schema prevent negative inventory/demand;
# a violating update raises sqlite3.IntegrityError and the whole transaction
# rolls back, so no incident is recorded for a failed change.


def set_warehouse_operational(
    conn: sqlite3.Connection, warehouse_id: str, operational: bool, description: str
) -> None:
    """Toggle a warehouse's operational status (e.g. power lost/restored)."""
    with conn:
        cur = conn.execute(
            "UPDATE warehouses SET is_operational = ? WHERE id = ?;",
            (1 if operational else 0, warehouse_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No warehouse with id {warehouse_id!r}")
        _record_incident(
            conn,
            "warehouse_online" if operational else "warehouse_offline",
            warehouse_id,
            description,
        )


def adjust_warehouse_stock(
    conn: sqlite3.Connection, warehouse_id: str, delta: int, description: str
) -> None:
    """Add (delta > 0) or remove (delta < 0) usable stock at a warehouse.

    The usable_stock >= 0 CHECK prevents the result from going negative; if it
    would, SQLite raises IntegrityError and the transaction rolls back.
    """
    with conn:
        row = conn.execute(
            "SELECT usable_stock FROM warehouses WHERE id = ?;", (warehouse_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No warehouse with id {warehouse_id!r}")
        conn.execute(
            "UPDATE warehouses SET usable_stock = usable_stock + ? WHERE id = ?;",
            (delta, warehouse_id),
        )
        _record_incident(
            conn,
            "stock_increase" if delta >= 0 else "stock_decrease",
            warehouse_id,
            description,
        )


def set_shelter_demand(
    conn: sqlite3.Connection, shelter_id: str, demand: int, description: str
) -> None:
    """Set a shelter's demand to an absolute value (demand >= 0 enforced)."""
    with conn:
        cur = conn.execute(
            "UPDATE shelters SET demand = ? WHERE id = ?;", (demand, shelter_id)
        )
        if cur.rowcount == 0:
            raise ValueError(f"No shelter with id {shelter_id!r}")
        _record_incident(conn, "demand_change", shelter_id, description)


def set_truck_operational(
    conn: sqlite3.Connection, truck_id: str, operational: bool, description: str
) -> None:
    """Toggle a truck's operational status (e.g. breakdown/repair)."""
    with conn:
        cur = conn.execute(
            "UPDATE trucks SET is_operational = ? WHERE id = ?;",
            (1 if operational else 0, truck_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No truck with id {truck_id!r}")
        _record_incident(
            conn,
            "truck_online" if operational else "truck_offline",
            truck_id,
            description,
        )


def set_route_open(
    conn: sqlite3.Connection,
    warehouse_id: str,
    shelter_id: str,
    is_open: bool,
    description: str,
) -> None:
    """Open or close the route between a warehouse and a shelter."""
    with conn:
        cur = conn.execute(
            "UPDATE delivery_routes SET is_open = ? "
            "WHERE warehouse_id = ? AND shelter_id = ?;",
            (1 if is_open else 0, warehouse_id, shelter_id),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"No route for warehouse {warehouse_id!r} -> shelter {shelter_id!r}"
            )
        _record_incident(
            conn,
            "route_open" if is_open else "route_closed",
            f"{warehouse_id}->{shelter_id}",
            description,
        )


# --- Retrieval queries -------------------------------------------------------
def available_inventory(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Usable stock at operational warehouses only."""
    return conn.execute(
        "SELECT id, name, usable_stock FROM warehouses "
        "WHERE is_operational = 1 ORDER BY id;"
    ).fetchall()


def operational_trucks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Operational trucks whose home warehouse is also operational."""
    return conn.execute(
        "SELECT t.id, t.warehouse_id, t.capacity_kits "
        "FROM trucks t JOIN warehouses w ON w.id = t.warehouse_id "
        "WHERE t.is_operational = 1 AND w.is_operational = 1 "
        "ORDER BY t.id;"
    ).fetchall()


def open_routes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Open routes between operational warehouses and shelters."""
    return conn.execute(
        "SELECT r.warehouse_id, r.shelter_id, r.distance_km "
        "FROM delivery_routes r JOIN warehouses w ON w.id = r.warehouse_id "
        "WHERE r.is_open = 1 AND w.is_operational = 1 "
        "ORDER BY r.warehouse_id, r.shelter_id;"
    ).fetchall()


def shelter_demand(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Shelter demand ordered by priority (most urgent first)."""
    return conn.execute(
        "SELECT id, name, demand, priority FROM shelters "
        "ORDER BY priority ASC, id ASC;"
    ).fetchall()


# --- CLI ---------------------------------------------------------------------
def _print_status(conn: sqlite3.Connection) -> None:
    print("Available inventory (operational warehouses):")
    for r in available_inventory(conn):
        print(f"  {r['id']} {r['name']}: {r['usable_stock']} kits")
    print("Operational trucks:")
    for r in operational_trucks(conn):
        print(f"  {r['id']} @ {r['warehouse_id']}: capacity {r['capacity_kits']}")
    print("Open routes:")
    for r in open_routes(conn):
        print(f"  {r['warehouse_id']} -> {r['shelter_id']}: {r['distance_km']} km")
    print("Shelter demand (by priority):")
    for r in shelter_demand(conn):
        print(f"  {r['id']} {r['name']}: demand {r['demand']} (priority {r['priority']})")


def _apply_demo_events(conn: sqlite3.Connection) -> None:
    """Apply the six example manager events from the spec."""
    set_warehouse_operational(
        conn, "W2", False, "Warehouse B lost power and stopped shipping."
    )
    adjust_warehouse_stock(
        conn, "W1", -40, "We lost 40 food kits to flooding at Warehouse A."
    )
    set_shelter_demand(
        conn, "S3", 100, "Shelter 3 now needs 100 kits instead of 40."
    )
    set_warehouse_operational(conn, "W2", True, "Warehouse B is operational again.")
    set_truck_operational(conn, "T1", False, "Our delivery truck T1 broke down.")
    set_route_open(
        conn, "W1", "S2", False,
        "The road between Warehouse A and Shelter 2 is blocked.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CrisisOps database tool")
    parser.add_argument(
        "command",
        choices=["init", "reset", "status", "demo"],
        help="init: create if missing; reset: rebuild demo state; "
        "status: print retrieval queries; demo: reset then apply 6 events",
    )
    args = parser.parse_args()

    if args.command == "init":
        created = init_db()
        print(f"Database {'created' if created else 'already exists'}: {DB_PATH}")
    elif args.command == "reset":
        reset_db()
        print(f"Database reset to original demo state: {DB_PATH}")
    elif args.command == "status":
        init_db()
        conn = connect()
        try:
            _print_status(conn)
        finally:
            conn.close()
    elif args.command == "demo":
        reset_db()
        conn = connect()
        try:
            _apply_demo_events(conn)
            print("Applied 6 demo events. Resulting state:\n")
            _print_status(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
