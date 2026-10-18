-- =============================================================================
-- Rail ticketing: OLTP source schema (MariaDB)
-- Simulates the production database of a FICTIONAL rail operator.
--
-- Conventions (see docs/adr/0003-source-schema-design.md):
--   * Every table has a single-column surrogate primary key, plus created_at /
--     updated_at maintained by the DATABASE (DEFAULT / ON UPDATE), never by the
--     application. The incremental extractor relies on updated_at.
--   * Every table has an index on (updated_at, <primary key>): the extractor
--     reads "rows changed since X" as an index range scan, in key order.
--   * No physical DELETE: rows change status instead (soft delete). The
--     application account has no DELETE privilege (see 02_users.sh).
--   * Every DATETIME is UTC (server default-time-zone = +00:00).
--   * Money is DECIMAL(10, 2), never FLOAT (binary floats cannot store 0.10).
--
-- This file runs once, when the container starts with an empty data volume,
-- against the database named by MARIADB_DATABASE.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Reference data: network
-- -----------------------------------------------------------------------------

CREATE TABLE stations (
    station_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    station_code CHAR(5) NOT NULL,
    station_name VARCHAR(100) NOT NULL,
    city VARCHAR(100) NOT NULL,
    region VARCHAR(100) NOT NULL,
    latitude DECIMAL(8, 6) NULL,
    longitude DECIMAL(9, 6) NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (station_id),
    UNIQUE KEY uq_stations_code (station_code),
    KEY ix_stations_updated_at (updated_at, station_id)
) ENGINE = InnoDB;

-- "LINES" is a reserved word in MariaDB (LOAD DATA ... LINES TERMINATED BY).
CREATE TABLE rail_lines (
    line_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    line_code VARCHAR(10) NOT NULL,
    line_name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (line_id),
    UNIQUE KEY uq_rail_lines_code (line_code),
    KEY ix_rail_lines_updated_at (updated_at, line_id)
) ENGINE = InnoDB;

-- Ordered stops of a line. km_from_origin lets us compute the distance of any
-- journey (destination km - origin km), needed for passenger-km load factor.
CREATE TABLE line_stops (
    line_stop_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    line_id SMALLINT UNSIGNED NOT NULL,
    station_id INT UNSIGNED NOT NULL,
    stop_sequence SMALLINT UNSIGNED NOT NULL,
    km_from_origin DECIMAL(7, 2) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (line_stop_id),
    UNIQUE KEY uq_line_stops_sequence (line_id, stop_sequence),
    UNIQUE KEY uq_line_stops_station (line_id, station_id),
    KEY ix_line_stops_updated_at (updated_at, line_stop_id),
    CONSTRAINT fk_line_stops_line FOREIGN KEY (line_id) REFERENCES rail_lines (line_id),
    CONSTRAINT fk_line_stops_station FOREIGN KEY (station_id) REFERENCES stations (station_id),
    CONSTRAINT ck_line_stops_km CHECK (km_from_origin >= 0)
) ENGINE = InnoDB;


-- -----------------------------------------------------------------------------
-- Reference data: rolling stock and timetable
-- -----------------------------------------------------------------------------

-- A physical train unit and its seat capacity per class.
CREATE TABLE trains (
    train_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    train_code VARCHAR(10) NOT NULL,
    train_model VARCHAR(50) NOT NULL,
    seats_first SMALLINT UNSIGNED NOT NULL,
    seats_second SMALLINT UNSIGNED NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (train_id),
    UNIQUE KEY uq_trains_code (train_code),
    KEY ix_trains_updated_at (updated_at, train_id),
    CONSTRAINT ck_trains_capacity CHECK (seats_first + seats_second > 0)
) ENGINE = InnoDB;

-- One scheduled run of a train on a line, in one direction ("circulation").
-- Swapping the train of a trip updates train_id, hence updated_at: the change
-- of capacity reaches the warehouse like any other change.
CREATE TABLE trips (
    trip_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    service_number VARCHAR(10) NOT NULL,
    line_id SMALLINT UNSIGNED NOT NULL,
    train_id SMALLINT UNSIGNED NOT NULL,
    direction ENUM('OUTBOUND', 'RETURN') NOT NULL,
    scheduled_departure_at DATETIME NOT NULL,
    scheduled_arrival_at DATETIME NOT NULL,
    trip_status ENUM('SCHEDULED', 'CANCELLED', 'COMPLETED') NOT NULL DEFAULT 'SCHEDULED',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (trip_id),
    UNIQUE KEY uq_trips_service_departure (service_number, scheduled_departure_at),
    -- Serves the application's main search: "trips on this line on this day".
    KEY ix_trips_line_departure (line_id, scheduled_departure_at),
    KEY ix_trips_updated_at (updated_at, trip_id),
    CONSTRAINT fk_trips_line FOREIGN KEY (line_id) REFERENCES rail_lines (line_id),
    CONSTRAINT fk_trips_train FOREIGN KEY (train_id) REFERENCES trains (train_id),
    CONSTRAINT ck_trips_schedule CHECK (scheduled_arrival_at > scheduled_departure_at)
) ENGINE = InnoDB;


-- -----------------------------------------------------------------------------
-- Commercial data
-- -----------------------------------------------------------------------------

-- A table rather than an ENUM: adding a channel is data, not a schema change,
-- and the table maps directly to a warehouse dimension.
CREATE TABLE sales_channels (
    channel_id TINYINT UNSIGNED NOT NULL AUTO_INCREMENT,
    channel_code VARCHAR(20) NOT NULL,
    channel_name VARCHAR(50) NOT NULL,
    is_online BOOLEAN NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (channel_id),
    UNIQUE KEY uq_sales_channels_code (channel_code),
    KEY ix_sales_channels_updated_at (updated_at, channel_id)
) ENGINE = InnoDB;

-- Personal data is synthetic. Closing an account sets is_active = FALSE.
CREATE TABLE customers (
    customer_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    phone VARCHAR(20) NULL,
    birth_date DATE NULL,
    city VARCHAR(100) NULL,
    loyalty_tier ENUM('NONE', 'SILVER', 'GOLD') NOT NULL DEFAULT 'NONE',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (customer_id),
    UNIQUE KEY uq_customers_email (email),
    KEY ix_customers_updated_at (updated_at, customer_id)
) ENGINE = InnoDB;

-- A purchase (basket) of one or more tickets.
-- customer_id is NULL for anonymous purchases (counter, ticket machine).
-- ordered_at is the BUSINESS time of the sale; created_at is the TECHNICAL
-- time the row was written. They differ, e.g. for offline sales synced later.
CREATE TABLE orders (
    order_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_reference CHAR(10) NOT NULL,
    customer_id INT UNSIGNED NULL,
    channel_id TINYINT UNSIGNED NOT NULL,
    ordered_at DATETIME(6) NOT NULL,
    order_status ENUM('PENDING', 'CONFIRMED', 'CANCELLED') NOT NULL DEFAULT 'PENDING',
    total_amount DECIMAL(10, 2) NOT NULL,
    currency CHAR(3) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (order_id),
    UNIQUE KEY uq_orders_reference (order_reference),
    KEY ix_orders_updated_at (updated_at, order_id),
    -- The application polls its rare PENDING orders: without this index, each
    -- poll scans every order ever made.
    KEY ix_orders_status (order_status, ordered_at),
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT fk_orders_channel FOREIGN KEY (channel_id) REFERENCES sales_channels (channel_id),
    CONSTRAINT ck_orders_amount CHECK (total_amount >= 0)
) ENGINE = InnoDB;

-- One seat for one passenger on one trip, between two stations of its line.
-- Cancelling is not refunding: a SAVER fare can be cancelled without refund.
-- That is why cancellation rate and refund rate are two different KPIs.
CREATE TABLE tickets (
    ticket_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_id BIGINT UNSIGNED NOT NULL,
    trip_id INT UNSIGNED NOT NULL,
    origin_station_id INT UNSIGNED NOT NULL,
    destination_station_id INT UNSIGNED NOT NULL,
    fare_class ENUM('FIRST', 'SECOND') NOT NULL,
    fare_type ENUM('FLEX', 'STANDARD', 'SAVER') NOT NULL,
    passenger_type ENUM('ADULT', 'CHILD', 'YOUTH', 'SENIOR') NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    ticket_status ENUM('ISSUED', 'CANCELLED', 'USED') NOT NULL DEFAULT 'ISSUED',
    cancelled_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (ticket_id),
    KEY ix_tickets_updated_at (updated_at, ticket_id),
    CONSTRAINT fk_tickets_order FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT fk_tickets_trip FOREIGN KEY (trip_id) REFERENCES trips (trip_id),
    CONSTRAINT fk_tickets_origin FOREIGN KEY (origin_station_id)
    REFERENCES stations (station_id),
    CONSTRAINT fk_tickets_destination FOREIGN KEY (destination_station_id)
    REFERENCES stations (station_id),
    CONSTRAINT ck_tickets_price CHECK (price >= 0),
    CONSTRAINT ck_tickets_journey CHECK (origin_station_id <> destination_station_id),
    -- A cancelled ticket always has a cancellation time, and only then.
    CONSTRAINT ck_tickets_cancellation CHECK (
        (ticket_status = 'CANCELLED') = (cancelled_at IS NOT NULL)
    )
) ENGINE = InnoDB;

-- Payment attempts for an order. A failed attempt stays (status FAILED):
-- the payment failure rate is itself a business indicator.
CREATE TABLE payments (
    payment_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    order_id BIGINT UNSIGNED NOT NULL,
    payment_method ENUM('CARD', 'MOBILE_MONEY', 'CASH', 'BANK_TRANSFER') NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    payment_status ENUM('PENDING', 'SUCCEEDED', 'FAILED') NOT NULL DEFAULT 'PENDING',
    provider_reference VARCHAR(64) NULL,
    attempted_at DATETIME(6) NOT NULL,
    settled_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (payment_id),
    KEY ix_payments_updated_at (updated_at, payment_id),
    KEY ix_payments_status (payment_status, attempted_at),  -- PENDING payments queue
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders (order_id),
    CONSTRAINT ck_payments_amount CHECK (amount > 0)
) ENGINE = InnoDB;

-- A refund of one ticket, paid back through one of the order's payments.
-- "amount <= ticket price" spans two tables: a CHECK cannot express it.
-- It is guaranteed by the application and verified by a dbt test.
CREATE TABLE refunds (
    refund_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    ticket_id BIGINT UNSIGNED NOT NULL,
    payment_id BIGINT UNSIGNED NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    reason ENUM('CUSTOMER_REQUEST', 'TRIP_CANCELLED', 'SERVICE_DISRUPTION') NOT NULL,
    refund_status ENUM('REQUESTED', 'APPROVED', 'REJECTED', 'PAID') NOT NULL
    DEFAULT 'REQUESTED',
    requested_at DATETIME(6) NOT NULL,
    processed_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (refund_id),
    KEY ix_refunds_updated_at (updated_at, refund_id),
    KEY ix_refunds_status (refund_status, requested_at),  -- refunds awaiting processing
    CONSTRAINT fk_refunds_ticket FOREIGN KEY (ticket_id) REFERENCES tickets (ticket_id),
    CONSTRAINT fk_refunds_payment FOREIGN KEY (payment_id) REFERENCES payments (payment_id),
    CONSTRAINT ck_refunds_amount CHECK (amount > 0)
) ENGINE = InnoDB;
