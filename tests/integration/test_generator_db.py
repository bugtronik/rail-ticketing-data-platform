"""The generator against the real MariaDB source (history, then live mode).

Everything runs in one transaction that is rolled back at the end: the
source is left empty, and other tests are not affected.
"""

import dataclasses
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pymysql.connections import Connection

from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.db import MariaDBWriter, source_is_empty
from rail_ticketing.generator.history import HistoryStats, generate_history
from rail_ticketing.generator.live import LiveSimulator
from rail_ticketing.generator.rows import OrderRow

pytestmark = pytest.mark.integration


class UncommittedWriter(MariaDBWriter):
    """Writes like the real writer, but never commits: the test rolls back."""

    def commit(self) -> None:
        pass


@pytest.fixture
def seeded(
    connect_as: Callable[[str], Connection], config: GeneratorConfig
) -> tuple[Connection, HistoryStats, datetime]:
    conn = connect_as("app")
    if not source_is_empty(conn):
        pytest.skip(
            "the source already holds data (after `make seed`); CI runs this test"
        )
    now = datetime.now(UTC).replace(tzinfo=None)
    stats = generate_history(config, UncommittedWriter(conn), now=now, days=7)
    return conn, stats, now


def _count(conn: Connection, sql: str, *params: object) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def test_history_rows_are_all_in_the_database(seeded) -> None:
    conn, stats, _ = seeded
    for table, expected in stats.rows.items():
        # Table names come from the generator's own list, not from input.
        assert _count(conn, f"SELECT COUNT(*) FROM {table}") == expected, table


def test_history_never_writes_in_the_future(seeded) -> None:
    conn, _, now = seeded
    for table in ("trips", "orders", "tickets", "payments", "refunds"):
        sql = f"SELECT COUNT(*) FROM {table} WHERE updated_at > %s"
        assert _count(conn, sql, now) == 0, table


def test_live_tick_writes_new_changes_that_a_watermark_can_see(
    seeded, config: GeneratorConfig
) -> None:
    conn, _, history_now = seeded
    busy = dataclasses.replace(
        config, live=dataclasses.replace(config.live, orders_per_hour=2000)
    )
    simulator = LiveSimulator(busy, conn, random.Random(1))

    done = simulator.tick(datetime.now(UTC).replace(tzinfo=None), timedelta(hours=1))

    assert done["orders"] > 0
    # This is what the extractor (J3) will query: rows changed after a watermark.
    sql = "SELECT COUNT(*) FROM orders WHERE updated_at > %s"
    assert _count(conn, sql, history_now) >= done["orders"]


def test_publishing_the_timetable_twice_creates_no_duplicate(
    seeded, config: GeneratorConfig
) -> None:
    conn, _, _ = seeded
    trips_before = _count(conn, "SELECT COUNT(*) FROM trips")
    # A fresh simulator republishes the whole booking horizon: INSERT IGNORE
    # on the (service_number, scheduled_departure_at) unique key skips it all.
    done = LiveSimulator(config, conn, random.Random(2)).tick(
        datetime.now(UTC).replace(tzinfo=None), timedelta(seconds=1)
    )
    assert done["trips_published"] == 0
    assert _count(conn, "SELECT COUNT(*) FROM trips") == trips_before


def test_live_retries_when_a_booking_reference_is_taken(
    seeded, config: GeneratorConfig
) -> None:
    conn, _, _ = seeded
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM orders ORDER BY order_id LIMIT 1")
        columns = [d[0] for d in cur.description]
        existing = OrderRow(**dict(zip(columns, cur.fetchone(), strict=True)))
    copy = dataclasses.replace(existing, order_id=None)  # same booking reference

    new_id = LiveSimulator(config, conn, random.Random(3)).insert_order(copy)

    assert new_id != existing.order_id
    assert copy.order_reference != existing.order_reference
