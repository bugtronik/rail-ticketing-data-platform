"""Integration tests for the MariaDB source: schema contract and privileges.

These tests encode the guarantees the incremental extractor will rely on
(see docs/adr/0003-source-schema-design.md). If one of them breaks, the
extraction could silently miss changes: that is why they run in CI.
"""

from collections.abc import Callable

import pymysql
import pytest
from pymysql.connections import Connection
from pymysql.cursors import Cursor

pytestmark = pytest.mark.integration

SOURCE_TABLES = [
    "stations",
    "rail_lines",
    "line_stops",
    "trains",
    "trips",
    "sales_channels",
    "customers",
    "orders",
    "tickets",
    "payments",
    "refunds",
]


# --- Schema contract ----------------------------------------------------------


def test_source_has_exactly_the_expected_tables(etl_conn: Connection) -> None:
    with etl_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert tables == set(SOURCE_TABLES)


@pytest.mark.parametrize("table", SOURCE_TABLES)
def test_audit_columns_are_maintained_by_the_database(etl_conn: Connection, table: str) -> None:
    with etl_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, is_nullable, LOWER(column_default), LOWER(extra)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name IN ('created_at', 'updated_at')
            """,
            (table,),
        )
        columns = {row[0]: row[1:] for row in cur.fetchall()}

    assert columns["created_at"][:2] == ("NO", "current_timestamp(6)")
    assert columns["updated_at"] == (
        "NO",
        "current_timestamp(6)",
        "on update current_timestamp(6)",
    )


@pytest.mark.parametrize("table", SOURCE_TABLES)
def test_watermark_index_starts_with_updated_at_then_primary_key(
    etl_conn: Connection, table: str
) -> None:
    with etl_conn.cursor() as cur:
        cur.execute(
            """
            SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = %s
            GROUP BY index_name
            """,
            (table,),
        )
        indexes = dict(cur.fetchall())

    primary_key = indexes["PRIMARY"]
    assert "," not in primary_key, "the extractor expects a single-column primary key"
    assert f"updated_at,{primary_key}" in indexes.values()


def test_server_stores_times_in_utc(etl_conn: Connection) -> None:
    with etl_conn.cursor() as cur:
        cur.execute("SELECT @@global.time_zone, @@session.time_zone")
        assert cur.fetchone() == ("+00:00", "+00:00")


# --- Behaviour of updated_at --------------------------------------------------


def test_updated_at_is_refreshed_on_update_without_application_help(
    app_conn: Connection,
) -> None:
    old = "2020-01-01 00:00:00"
    with app_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sales_channels
                (channel_code, channel_name, is_online, created_at, updated_at)
            VALUES ('TEST', 'Test channel', TRUE, %s, %s)
            """,
            (old, old),
        )
        channel_id = cur.lastrowid

        # The UPDATE does not mention updated_at: the database must set it.
        cur.execute(
            "UPDATE sales_channels SET channel_name = 'Renamed' WHERE channel_id = %s",
            (channel_id,),
        )
        cur.execute(
            "SELECT created_at, updated_at FROM sales_channels WHERE channel_id = %s",
            (channel_id,),
        )
        created_at, updated_at = cur.fetchone()

    assert str(created_at) == old, "created_at must never change"
    assert str(updated_at) > old


# --- Integrity constraints ------------------------------------------------------


def insert_minimal_order_and_trip(cur: Cursor) -> dict[str, int]:
    """Insert the smallest valid graph a ticket needs. Returns the new ids."""
    ids: dict[str, int] = {}
    for key, code in (("station_a", "TSTAA"), ("station_b", "TSTBB")):
        cur.execute(
            "INSERT INTO stations (station_code, station_name, city, region) "
            "VALUES (%s, 'Test station', 'Test city', 'Test region')",
            (code,),
        )
        ids[key] = cur.lastrowid
    cur.execute("INSERT INTO rail_lines (line_code, line_name) VALUES ('TST', 'Test line')")
    ids["line"] = cur.lastrowid
    cur.execute(
        "INSERT INTO trains (train_code, train_model, seats_first, seats_second) "
        "VALUES ('TST-1', 'Test model', 20, 180)"
    )
    ids["train"] = cur.lastrowid
    cur.execute(
        """
        INSERT INTO trips (service_number, line_id, train_id, direction,
                           scheduled_departure_at, scheduled_arrival_at)
        VALUES ('T001', %s, %s, 'OUTBOUND', '2030-01-01 08:00', '2030-01-01 10:00')
        """,
        (ids["line"], ids["train"]),
    )
    ids["trip"] = cur.lastrowid
    cur.execute(
        "INSERT INTO sales_channels (channel_code, channel_name, is_online) "
        "VALUES ('TEST', 'Test channel', TRUE)"
    )
    ids["channel"] = cur.lastrowid
    cur.execute(
        """
        INSERT INTO orders (order_reference, channel_id, ordered_at, total_amount, currency)
        VALUES ('TEST000001', %s, '2029-12-01 12:00', 50.00, 'EUR')
        """,
        (ids["channel"],),
    )
    ids["order"] = cur.lastrowid
    return ids


def insert_ticket(cur: Cursor, ids: dict[str, int], **overrides: object) -> None:
    ticket: dict[str, object] = {
        "order_id": ids["order"],
        "trip_id": ids["trip"],
        "origin_station_id": ids["station_a"],
        "destination_station_id": ids["station_b"],
        "fare_class": "SECOND",
        "fare_type": "STANDARD",
        "passenger_type": "ADULT",
        "price": 50.00,
        "ticket_status": "ISSUED",
        "cancelled_at": None,
    } | overrides
    columns = ", ".join(ticket)
    placeholders = ", ".join(["%s"] * len(ticket))
    # Column names come from the dict above, never from user input.
    cur.execute(
        f"INSERT INTO tickets ({columns}) VALUES ({placeholders})",  # noqa: S608
        tuple(ticket.values()),
    )


def test_a_valid_ticket_is_accepted(app_conn: Connection) -> None:
    with app_conn.cursor() as cur:
        ids = insert_minimal_order_and_trip(cur)
        insert_ticket(cur, ids)
        assert cur.rowcount == 1


# Each case builds the invalid columns from the ids of the inserted graph.
INVALID_TICKETS = {
    "same-origin-and-destination": (
        lambda ids: {"destination_station_id": ids["station_a"]},
        "ck_tickets_journey",
    ),
    "negative-price": (lambda ids: {"price": -1}, "ck_tickets_price"),
    "cancelled-without-date": (
        lambda ids: {"ticket_status": "CANCELLED", "cancelled_at": None},
        "ck_tickets_cancellation",
    ),
    "date-without-cancellation": (
        lambda ids: {"ticket_status": "ISSUED", "cancelled_at": "2029-12-02"},
        "ck_tickets_cancellation",
    ),
}


@pytest.mark.parametrize(
    ("make_overrides", "violated_constraint"),
    INVALID_TICKETS.values(),
    ids=INVALID_TICKETS.keys(),
)
def test_check_constraints_reject_invalid_tickets(
    app_conn: Connection,
    make_overrides: Callable[[dict[str, int]], dict[str, object]],
    violated_constraint: str,
) -> None:
    with app_conn.cursor() as cur:
        ids = insert_minimal_order_and_trip(cur)
        with pytest.raises(pymysql.MySQLError, match=violated_constraint):
            insert_ticket(cur, ids, **make_overrides(ids))


def test_foreign_keys_reject_unknown_references(app_conn: Connection) -> None:
    with app_conn.cursor() as cur:
        ids = insert_minimal_order_and_trip(cur)
        with pytest.raises(pymysql.IntegrityError, match="fk_tickets_trip"):
            insert_ticket(cur, ids, trip_id=999_999_999)


# --- Privileges -----------------------------------------------------------------


def test_application_account_cannot_delete(app_conn: Connection) -> None:
    # Even a DELETE that would match no row is refused: the check is on the privilege.
    with app_conn.cursor() as cur, pytest.raises(pymysql.MySQLError, match="DELETE command denied"):
        cur.execute("DELETE FROM tickets WHERE ticket_id = 0")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO sales_channels (channel_code, channel_name, is_online) "
        "VALUES ('X', 'X', TRUE)",
        "UPDATE sales_channels SET channel_name = 'X' WHERE channel_id = 0",
        "DELETE FROM sales_channels WHERE channel_id = 0",
    ],
    ids=["insert", "update", "delete"],
)
def test_etl_account_is_read_only(etl_conn: Connection, statement: str) -> None:
    with etl_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sales_channels")  # reading is allowed
        with pytest.raises(pymysql.MySQLError, match="command denied"):
            cur.execute(statement)


def test_etl_account_statements_are_capped_at_60_seconds(etl_conn: Connection) -> None:
    with etl_conn.cursor() as cur:
        cur.execute("SELECT @@max_statement_time")
        assert float(cur.fetchone()[0]) == 60.0


def test_etl_account_is_limited_to_two_connections(
    connect_as: Callable[[str], Connection],
) -> None:
    connect_as("etl")
    connect_as("etl")
    with pytest.raises(pymysql.OperationalError, match="max_user_connections"):
        connect_as("etl")
