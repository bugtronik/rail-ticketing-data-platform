-- =============================================================================
-- Warehouse (PostgreSQL): raw layer and extraction bookkeeping
--
-- raw.*   A faithful copy of the source tables, refreshed incrementally.
--         Same columns, same primary keys; types translated from MariaDB.
--         No foreign keys and no business constraints: raw accepts what the
--         source contains, so a load never fails on a data problem. Checking
--         quality is the job of dbt (staging and marts, from J4).
-- meta.*  Where each table's extraction stopped (the watermark) and a log of
--         every run.
--
-- Type mapping (MariaDB -> PostgreSQL), see ADR 0005:
--   INT UNSIGNED        -> BIGINT   (PostgreSQL has no unsigned types: an
--                                    unsigned INT can exceed INTEGER's range)
--   SMALLINT UNSIGNED   -> INTEGER
--   TINYINT UNSIGNED    -> SMALLINT
--   BIGINT UNSIGNED     -> BIGINT   (ids stay far below 2^63)
--   BOOLEAN (TINYINT(1))-> BOOLEAN
--   ENUM(...)           -> TEXT     (allowed values are tested in dbt)
--   DATETIME(6)         -> TIMESTAMP(6) WITHOUT TIME ZONE, holding UTC like
--                          the source; staging converts it to TIMESTAMPTZ.
--
-- Every raw table also has:
--   _extracted_at    when this version of the row was loaded
--   _extract_run_id  which extraction run loaded it (see meta.extract_runs)
-- =============================================================================

CREATE SCHEMA raw;
CREATE SCHEMA meta;


-- -----------------------------------------------------------------------------
-- raw: one table per source table
-- -----------------------------------------------------------------------------

CREATE TABLE raw.stations (
    station_id BIGINT PRIMARY KEY,
    station_code TEXT NOT NULL,
    station_name TEXT NOT NULL,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    latitude NUMERIC(8, 6),
    longitude NUMERIC(9, 6),
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.rail_lines (
    line_id INTEGER PRIMARY KEY,
    line_code TEXT NOT NULL,
    line_name TEXT NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.line_stops (
    line_stop_id BIGINT PRIMARY KEY,
    line_id INTEGER NOT NULL,
    station_id BIGINT NOT NULL,
    stop_sequence INTEGER NOT NULL,
    km_from_origin NUMERIC(7, 2) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.trains (
    train_id INTEGER PRIMARY KEY,
    train_code TEXT NOT NULL,
    train_model TEXT NOT NULL,
    seats_first INTEGER NOT NULL,
    seats_second INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.trips (
    trip_id BIGINT PRIMARY KEY,
    service_number TEXT NOT NULL,
    line_id INTEGER NOT NULL,
    train_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    scheduled_departure_at TIMESTAMP(0) NOT NULL,
    scheduled_arrival_at TIMESTAMP(0) NOT NULL,
    trip_status TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.sales_channels (
    channel_id SMALLINT PRIMARY KEY,
    channel_code TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    is_online BOOLEAN NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.customers (
    customer_id BIGINT PRIMARY KEY,
    email TEXT NOT NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    phone TEXT,
    birth_date DATE,
    city TEXT,
    loyalty_tier TEXT NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.orders (
    order_id BIGINT PRIMARY KEY,
    order_reference TEXT NOT NULL,
    customer_id BIGINT,
    channel_id SMALLINT NOT NULL,
    ordered_at TIMESTAMP(6) NOT NULL,
    order_status TEXT NOT NULL,
    total_amount NUMERIC(10, 2) NOT NULL,
    currency TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.tickets (
    ticket_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    trip_id BIGINT NOT NULL,
    origin_station_id BIGINT NOT NULL,
    destination_station_id BIGINT NOT NULL,
    fare_class TEXT NOT NULL,
    fare_type TEXT NOT NULL,
    passenger_type TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL,
    ticket_status TEXT NOT NULL,
    cancelled_at TIMESTAMP(6),
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.payments (
    payment_id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    payment_method TEXT NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    payment_status TEXT NOT NULL,
    provider_reference TEXT,
    attempted_at TIMESTAMP(6) NOT NULL,
    settled_at TIMESTAMP(6),
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);

CREATE TABLE raw.refunds (
    refund_id BIGINT PRIMARY KEY,
    ticket_id BIGINT NOT NULL,
    payment_id BIGINT NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    reason TEXT NOT NULL,
    refund_status TEXT NOT NULL,
    requested_at TIMESTAMP(6) NOT NULL,
    processed_at TIMESTAMP(6),
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    _extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    _extract_run_id BIGINT NOT NULL
);


-- -----------------------------------------------------------------------------
-- meta: extraction bookkeeping
-- -----------------------------------------------------------------------------

-- Where the extraction of each table stopped: the (updated_at, primary key)
-- of the last row loaded. Updated in the SAME transaction as the rows of the
-- batch: the watermark can never be ahead of the data.
CREATE TABLE meta.extract_state (
    table_name TEXT PRIMARY KEY,
    watermark_updated_at TIMESTAMP(6) NOT NULL,
    watermark_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per extraction run and table: what was read, what changed, how long.
CREATE TABLE meta.extract_runs (
    run_id BIGSERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'skipped')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    watermark_from TIMESTAMP(6),
    watermark_to TIMESTAMP(6),
    batches INTEGER NOT NULL DEFAULT 0,
    rows_read BIGINT NOT NULL DEFAULT 0,
    rows_inserted BIGINT NOT NULL DEFAULT 0,
    rows_updated BIGINT NOT NULL DEFAULT 0,
    source_seconds NUMERIC(10, 3) NOT NULL DEFAULT 0,
    message TEXT
);


-- -----------------------------------------------------------------------------
-- Privileges: the extractor can read and write raw and meta, nothing else.
-- -----------------------------------------------------------------------------

GRANT USAGE ON SCHEMA raw, meta TO extractor;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA raw TO extractor;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA meta TO extractor;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA meta TO extractor;
