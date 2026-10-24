-- CrisisOps database schema (SQLite)
--
-- One database, five tables, with foreign keys and CHECK constraints that
-- prevent negative inventory and demand. The allocation algorithm lives
-- elsewhere; this file only defines structure.
--
-- Run with foreign key enforcement ON (PRAGMA foreign_keys = ON), which
-- init_db.py sets per-connection since SQLite defaults it off.

-- Idempotent: drop in reverse-dependency order so re-running rebuilds cleanly.
DROP TABLE IF EXISTS incidents;
DROP TABLE IF EXISTS delivery_routes;
DROP TABLE IF EXISTS trucks;
DROP TABLE IF EXISTS shelters;
DROP TABLE IF EXISTS warehouses;

-- Warehouses hold the usable food-kit inventory.
CREATE TABLE warehouses (
    id             TEXT    PRIMARY KEY,                       -- e.g. 'W1'
    name           TEXT    NOT NULL,
    location       TEXT    NOT NULL,
    usable_stock   INTEGER NOT NULL
                           CHECK (usable_stock >= 0),         -- no negative inventory
    is_operational INTEGER NOT NULL DEFAULT 1
                           CHECK (is_operational IN (0, 1)),  -- boolean
    -- Optional map position on the simulated km grid (for the frontend map).
    x_km           REAL,
    y_km           REAL
);

-- Shelters request food kits; lower priority number = more urgent.
CREATE TABLE shelters (
    id       TEXT    PRIMARY KEY,                             -- e.g. 'S1'
    name     TEXT    NOT NULL,
    location TEXT    NOT NULL,
    demand   INTEGER NOT NULL
                     CHECK (demand >= 0),                     -- no negative demand
    priority INTEGER NOT NULL
                     CHECK (priority >= 1),
    -- Optional map position on the simulated km grid (for the frontend map).
    x_km     REAL,
    y_km     REAL
);

-- Trucks belong to a warehouse and carry up to capacity_kits.
CREATE TABLE trucks (
    id             TEXT    PRIMARY KEY,                       -- e.g. 'T1'
    warehouse_id   TEXT    NOT NULL,
    capacity_kits  INTEGER NOT NULL
                           CHECK (capacity_kits >= 0),
    is_operational INTEGER NOT NULL DEFAULT 1
                           CHECK (is_operational IN (0, 1)),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses (id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

-- Simulated warehouse -> shelter route network (not real road geometry).
CREATE TABLE delivery_routes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id TEXT    NOT NULL,
    shelter_id   TEXT    NOT NULL,
    distance_km  REAL    NOT NULL
                         CHECK (distance_km >= 0),
    is_open      INTEGER NOT NULL DEFAULT 1
                         CHECK (is_open IN (0, 1)),
    FOREIGN KEY (warehouse_id) REFERENCES warehouses (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (shelter_id) REFERENCES shelters (id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    UNIQUE (warehouse_id, shelter_id)                         -- one route per pair
);

-- Confirmed incidents parsed from the manager's natural-language input.
-- target_id references a warehouse, shelter, truck, or route id depending on
-- incident_type; it is intentionally untyped (no FK) since it may point at
-- different tables.
CREATE TABLE incidents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_type TEXT    NOT NULL,                            -- e.g. 'warehouse_offline'
    target_id     TEXT,                                        -- id of affected entity
    description   TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))   -- ISO-8601 UTC
);

-- Helpful indexes for the retrieval queries.
CREATE INDEX idx_trucks_warehouse ON trucks (warehouse_id);
CREATE INDEX idx_routes_warehouse ON delivery_routes (warehouse_id);
CREATE INDEX idx_routes_shelter   ON delivery_routes (shelter_id);
CREATE INDEX idx_incidents_target ON incidents (target_id);
