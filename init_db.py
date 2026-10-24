#!/usr/bin/env python3
"""Initialize, reset, and operate the CrisisOps SQLite database.

This script is the single entry point for creating `crisisops.db`, resetting
it to a known demo state, and running the manager's confirmed operations inside
transactions (each of which also records a row in `incidents`).

Two datasets can be loaded:

* the Bay County demo scenario, `data/demo_scenario.json` (default):
  4 warehouses, 10 shelters, 8 trucks, 40 routes
* the small seed, `backend/seed.sql` (`--small`): 2 warehouses, 3 shelters,
  2 trucks, 6 routes; used by unit tests

The allocation algorithm lives in `optimization/allocator.py`, not here.

Usage:
    python init_db.py init            # create db if missing (no-op if present)
    python init_db.py reset           # drop + rebuild with the demo scenario
    python init_db.py reset --small   # drop + rebuild with the small seed
    python init_db.py status          # print inventory/trucks/routes/demand
    python init_db.py demo            # reset, then apply the scenario's events

    Any command accepts --scenario PATH to use another scenario JSON.

Programmatic use:
    from init_db import connect, apply_operation, reset_db, resolve_operation
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

# --- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "crisisops.db"
SCHEMA_PATH = BASE_DIR / "backend" / "schema.sql"
SEED_PATH = BASE_DIR / "backend" / "seed.sql"
DEFAULT_SCENARIO_PATH = BASE_DIR / "data" / "demo_scenario.json"

# Scenario JSON keys that map to tables, in foreign-key order (parents first).
_SCENARIO_TABLES = ("warehouses", "shelters", "trucks", "delivery_routes")


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


def load_scenario(path: Path | str = DEFAULT_SCENARIO_PATH) -> dict[str, Any]:
    """Read a scenario JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _insert_scenario_rows(conn: sqlite3.Connection, data: Mapping[str, Any]) -> None:
    """Insert a scenario's rows with named columns.

    Every key in a row must be a real column of its table, so a typo in the
    JSON fails loudly instead of being silently dropped. Keys outside the four
    tables (name, description, events) are ignored: events become incidents
    only when they are actually applied.
    """
    for table in _SCENARIO_TABLES:
        columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({table});")]
        for row in data.get(table, []):
            unknown = sorted(set(row) - set(columns))
            if unknown:
                raise ValueError(f"{table}: unknown column(s) {unknown} in scenario row {row}")
            use = [c for c in columns if c in row]
            conn.execute(
                f"INSERT INTO {table} ({', '.join(use)}) "
                f"VALUES ({', '.join('?' * len(use))});",
                [row[c] for c in use],
            )


def reset_db(
    db_path: Path | str = DB_PATH,
    scenario: Path | str | None = DEFAULT_SCENARIO_PATH,
) -> None:
    """Rebuild the database from scratch.

    Args:
        db_path: database file to (re)create.
        scenario: scenario JSON to load (default: the Bay County demo).
            Pass None to load the small `seed.sql` instead.

    schema.sql drops existing tables first, so this is a clean rebuild whether
    or not the file already exists. The data load runs in one transaction.
    """
    data = load_scenario(scenario) if scenario is not None else None
    conn = connect(db_path)
    try:
        _run_sql_file(conn, SCHEMA_PATH)
        if data is None:
            _run_sql_file(conn, SEED_PATH)
        else:
            with conn:  # commits on success, rolls back on error
                _insert_scenario_rows(conn, data)
    finally:
        conn.close()


def init_db(
    db_path: Path | str = DB_PATH,
    scenario: Path | str | None = DEFAULT_SCENARIO_PATH,
) -> bool:
    """Create the database only if it does not already exist.

    Returns True if it created the database, False if it was already present.
    """
    if Path(db_path).exists():
        return False
    reset_db(db_path, scenario)
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


def set_warehouse_stock(
    conn: sqlite3.Connection, warehouse_id: str, quantity: int, description: str
) -> None:
    """Set a warehouse's usable stock to an absolute value (>= 0 enforced)."""
    with conn:
        cur = conn.execute(
            "UPDATE warehouses SET usable_stock = ? WHERE id = ?;",
            (quantity, warehouse_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No warehouse with id {warehouse_id!r}")
        _record_incident(conn, "stock_set", warehouse_id, description)


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


# --- Id resolution -----------------------------------------------------------
# The parser keeps what the manager said ("Warehouse A" -> 'A', "Shelter 3" ->
# '3'), while rows use ids ('W1', 'S3'). Resolution is a lookup against the
# current rows, not a fixed table, so new warehouses and shelters just work.

# operation field -> (table, leading word of names in that table)
_TARGETS: dict[str, tuple[str, str]] = {
    "warehouse": ("warehouses", "warehouse"),
    "shelter": ("shelters", "shelter"),
    "truck": ("trucks", "truck"),
}


def _squash(text: Any) -> str:
    """Case- and whitespace-insensitive form: '  Warehouse   A ' -> 'warehouse a'."""
    return " ".join(str(text).split()).casefold()


def _drop_word(text: str, word: str) -> str:
    """'warehouse a' -> 'a'; text without that leading word is returned as-is."""
    head, _, rest = text.partition(" ")
    return rest if head == word and rest else text


def resolve_id(conn: sqlite3.Connection, kind: str, value: Any) -> str:
    """Map a warehouse/shelter/truck reference to its database id.

    Accepts, ignoring case and extra spaces, in this order of precedence:
      1. the id itself:           'W1', 'w1', 'Truck T1'
      2. the full name:           'Warehouse A', 'Shelter 3'
      3. the code after the word: 'A', '3' (what the parser returns)

    Raises ValueError if nothing matches, or if a reference matches more than
    one row at the same level.
    """
    if kind not in _TARGETS:
        raise ValueError(f"Unknown target kind {kind!r}")
    table, word = _TARGETS[kind]
    wanted = _squash(value)
    if not wanted:
        raise ValueError(f"Empty {kind} reference")
    code = _drop_word(wanted, word)

    has_name = kind != "truck"                     # trucks have no name column
    rows = conn.execute(
        f"SELECT id{', name' if has_name else ''} FROM {table};"
    ).fetchall()

    levels = [lambda r: _squash(r["id"]) in (wanted, code)]
    if has_name:
        levels += [
            lambda r: _squash(r["name"]) == wanted,
            lambda r: _drop_word(_squash(r["name"]), word) == code,
        ]
    for matches in levels:
        hits = sorted({r["id"] for r in rows if matches(r)})
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"{kind.capitalize()} {value!r} is ambiguous: matches {', '.join(hits)}")

    known = ", ".join(
        f"{r['id']} ({r['name']})" if has_name else r["id"]
        for r in sorted(rows, key=lambda r: _natural_key(r["id"]))
    )
    raise ValueError(f"No {kind} matching {value!r}. Known: {known}")


def resolve_operation(conn: sqlite3.Connection, operation: Mapping[str, Any]) -> dict[str, Any]:
    """Copy of ``operation`` with warehouse/shelter/truck replaced by ids.

    Idempotent: an operation that already uses ids comes back unchanged.
    """
    resolved = dict(operation)
    for field in _TARGETS:
        value = resolved.get(field)
        if value is not None and value != "":
            resolved[field] = resolve_id(conn, field, value)
    return resolved


def _natural_key(value: str) -> list[Any]:
    """Sort 'S2' before 'S10'."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", value)]


def apply_operation(
    conn: sqlite3.Connection, operation: Mapping[str, Any], description: str
) -> None:
    """Apply one structured operation to the database.

    ``operation`` uses the incident parser's action names and fields
    (action, warehouse, shelter, truck, quantity). Targets may be database ids
    ('W1', 'S3', 'T1') or what the parser returns ('A', '3'); see
    ``resolve_id``. Raises ValueError for an unknown action, a missing field,
    an unknown or ambiguous target, or a negative quantity, and
    sqlite3.IntegrityError if the change would make stock or demand negative.
    """
    operation = resolve_operation(conn, operation)
    action = operation.get("action")

    def need(field: str) -> Any:
        value = operation.get(field)
        if value is None or value == "":
            raise ValueError(f"Action {action!r} requires {field!r}")
        return value

    def quantity() -> int:
        qty = int(need("quantity"))
        if qty < 0:
            raise ValueError(f"quantity must be >= 0, got {qty}")
        return qty

    if action in ("increase_stock", "decrease_stock"):
        qty = quantity()
        adjust_warehouse_stock(
            conn, need("warehouse"), qty if action == "increase_stock" else -qty, description
        )
    elif action == "set_stock":
        set_warehouse_stock(conn, need("warehouse"), quantity(), description)
    elif action in ("warehouse_offline", "warehouse_online"):
        set_warehouse_operational(
            conn, need("warehouse"), action == "warehouse_online", description
        )
    elif action == "set_demand":
        set_shelter_demand(conn, need("shelter"), quantity(), description)
    elif action in ("truck_offline", "truck_online"):
        set_truck_operational(conn, need("truck"), action == "truck_online", description)
    elif action in ("close_route", "open_route"):
        set_route_open(
            conn, need("warehouse"), need("shelter"), action == "open_route", description
        )
    else:
        raise ValueError(f"Unknown action {action!r}")


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


# The six example events for the small seed.
_SMALL_SEED_EVENTS = [
    ("Warehouse B lost power and stopped shipping.",
     {"action": "warehouse_offline", "warehouse": "W2"}),
    ("We lost 40 food kits to flooding at Warehouse A.",
     {"action": "decrease_stock", "warehouse": "W1", "quantity": 40}),
    ("Shelter 3 now needs 100 kits instead of 40.",
     {"action": "set_demand", "shelter": "S3", "quantity": 100}),
    ("Warehouse B is operational again.",
     {"action": "warehouse_online", "warehouse": "W2"}),
    ("Our delivery truck T1 broke down.",
     {"action": "truck_offline", "truck": "T1"}),
    ("The road between Warehouse A and Shelter 2 is blocked.",
     {"action": "close_route", "warehouse": "W1", "shelter": "S2"}),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="CrisisOps database tool")
    parser.add_argument(
        "command",
        choices=["init", "reset", "status", "demo"],
        help="init: create if missing; reset: rebuild; status: print current "
        "state; demo: reset, then apply the dataset's example events",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--small", action="store_true",
                        help="use the small seed.sql instead of a scenario")
    source.add_argument("--scenario", default=str(DEFAULT_SCENARIO_PATH),
                        help="scenario JSON to load (default: %(default)s)")
    args = parser.parse_args()
    scenario = None if args.small else args.scenario
    label = "small seed" if scenario is None else Path(scenario).name

    if args.command == "init":
        created = init_db(scenario=scenario)
        print(f"Database {'created from ' + label if created else 'already exists'}: {DB_PATH}")
    elif args.command == "reset":
        reset_db(scenario=scenario)
        print(f"Database reset from {label}: {DB_PATH}")
    elif args.command == "status":
        init_db(scenario=scenario)
        conn = connect()
        try:
            _print_status(conn)
        finally:
            conn.close()
    elif args.command == "demo":
        reset_db(scenario=scenario)
        events = (_SMALL_SEED_EVENTS if scenario is None else
                  [(e["text"], e["operation"]) for e in load_scenario(scenario).get("events", [])])
        conn = connect()
        try:
            for text, operation in events:
                apply_operation(conn, operation, text)
            print(f"Reset from {label} and applied {len(events)} events. Resulting state:\n")
            _print_status(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
