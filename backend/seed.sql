-- CrisisOps demo seed data.
--
-- Loaded after schema.sql. Re-running after schema.sql rebuilds the exact
-- original demo state, which is how the reset works. All data is fictional.

-- Warehouses -----------------------------------------------------------------
INSERT INTO warehouses (id, name, location, usable_stock, is_operational) VALUES
    ('W1', 'Warehouse A', 'Northside Depot', 100, 1),
    ('W2', 'Warehouse B', 'Harbor Depot',     80, 1);

-- Shelters (lower priority number = more urgent) ------------------------------
INSERT INTO shelters (id, name, location, demand, priority) VALUES
    ('S1', 'Shelter 1', 'Central Gym',       70, 1),
    ('S2', 'Shelter 2', 'Eastside School',   60, 2),
    ('S3', 'Shelter 3', 'Riverside Church',  40, 2);

-- Trucks ---------------------------------------------------------------------
INSERT INTO trucks (id, warehouse_id, capacity_kits, is_operational) VALUES
    ('T1', 'W1', 100, 1),
    ('T2', 'W2',  80, 1);

-- Delivery routes: both warehouses to all three shelters (6 routes) -----------
-- Fictional distances for a simulated network (not real road geometry).
INSERT INTO delivery_routes (warehouse_id, shelter_id, distance_km, is_open) VALUES
    ('W1', 'S1',  8.5, 1),
    ('W1', 'S2', 12.0, 1),
    ('W1', 'S3', 20.5, 1),
    ('W2', 'S1', 15.0, 1),
    ('W2', 'S2',  6.5, 1),
    ('W2', 'S3', 10.0, 1);

-- incidents starts empty; rows are added as the manager confirms events.
