"""The extractor's guarantees, checked against real MariaDB and PostgreSQL.

Each test runs on throwaway databases (see conftest.py) filled with 3 days of
generated history.
"""

import dataclasses
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from pymysql.connections import Connection

from rail_ticketing.extract.config import ExtractorConfig, Window
from rail_ticketing.extract.extractor import RunResult, SchemaDriftError, TableExtractor

pytestmark = pytest.mark.integration

NIGHT = datetime(2026, 1, 1, 3, 0)  # a fixed "local time" for window tests


def run(
    cfg: ExtractorConfig,
    table: str,
    source: Connection,
    warehouse: psycopg.Connection,
    **kwargs: object,
) -> RunResult:
    full = bool(kwargs.pop("full", False))
    return TableExtractor(cfg, cfg.table(table), source, warehouse, **kwargs).run(full=full)


def run_all(cfg: ExtractorConfig, source: Connection, warehouse: psycopg.Connection) -> None:
    for spec in cfg.tables:
        TableExtractor(cfg, spec, source, warehouse).run()


def source_rows(conn: Connection, table: str, columns: str = "*") -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {columns} FROM {table} ORDER BY 1")  # noqa: S608
        return [_normalise(row) for row in cur.fetchall()]


def raw_rows(conn: psycopg.Connection, table: str, columns: str) -> list[tuple]:
    rows = conn.execute(f"SELECT {columns} FROM raw.{table} ORDER BY 1").fetchall()  # noqa: S608
    conn.commit()
    return [_normalise(row) for row in rows]


def _normalise(row: tuple) -> tuple:
    """MariaDB returns booleans as 0/1, PostgreSQL as False/True."""
    return tuple(int(v) if isinstance(v, bool) else v for v in row)


def count(conn: psycopg.Connection, query: str, *params: object) -> int:
    value = conn.execute(query, params).fetchone()[0]
    conn.commit()
    return value


# --- Correctness ----------------------------------------------------------------


def test_first_run_copies_every_table_exactly(
    extract_config: ExtractorConfig, source: Connection, warehouse: psycopg.Connection
) -> None:
    run_all(extract_config, source, warehouse)
    for spec in extract_config.tables:
        columns = ", ".join(
            name
            for (name,) in warehouse.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'raw' AND table_name = %s AND column_name NOT LIKE '\\_%%' "
                "ORDER BY ordinal_position",
                (spec.name,),
            ).fetchall()
        )
        # Every value of every row: ids, decimals, microseconds, NULLs, booleans.
        assert raw_rows(warehouse, spec.name, columns) == source_rows(source, spec.name, columns), (
            spec.name
        )


def test_running_twice_changes_nothing(
    extract_config: ExtractorConfig, source: Connection, warehouse: psycopg.Connection
) -> None:
    first = run(extract_config, "tickets", source, warehouse)
    before = count(warehouse, "SELECT COUNT(*) FROM raw.tickets")

    second = run(extract_config, "tickets", source, warehouse)

    assert first.rows_inserted == before > 0
    # The overlap is re-read, but unchanged rows are not rewritten.
    assert second.rows_inserted == second.rows_updated == 0
    assert count(warehouse, "SELECT COUNT(*) FROM raw.tickets") == before
    assert count(warehouse, "SELECT COUNT(DISTINCT _extract_run_id) FROM raw.tickets") == 1


def test_source_changes_are_caught_up(
    extract_config: ExtractorConfig,
    source: Connection,
    warehouse: psycopg.Connection,
    app_source: Connection,
) -> None:
    run(extract_config, "tickets", source, warehouse)
    with app_source.cursor() as cur:
        cur.execute("SELECT MIN(ticket_id) FROM tickets WHERE ticket_status = 'ISSUED'")
        ticket_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE tickets SET ticket_status = 'CANCELLED', cancelled_at = UTC_TIMESTAMP(6) "
            "WHERE ticket_id = %s",
            (ticket_id,),
        )
    app_source.commit()

    result = run(extract_config, "tickets", source, warehouse)

    assert result.rows_updated == 1
    status = warehouse.execute(
        "SELECT ticket_status FROM raw.tickets WHERE ticket_id = %s", (ticket_id,)
    ).fetchone()[0]
    assert status == "CANCELLED"


# --- Robustness -------------------------------------------------------------------


def test_a_crash_mid_run_loses_nothing_and_duplicates_nothing(
    extract_config: ExtractorConfig,
    source: Connection,
    warehouse: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = dataclasses.replace(extract_config, batch_size=500)
    real_load = TableExtractor._load_batch
    calls = {"n": 0}

    def crash_on_third_batch(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ConnectionError("simulated network failure")
        return real_load(self, *args, **kwargs)

    monkeypatch.setattr(TableExtractor, "_load_batch", crash_on_third_batch)
    with pytest.raises(ConnectionError):
        run(cfg, "tickets", source, warehouse)

    # Exactly the two committed batches are there, and the watermark matches them.
    assert count(warehouse, "SELECT COUNT(*) FROM raw.tickets") == 1000
    watermark = warehouse.execute(
        "SELECT watermark_updated_at, watermark_id FROM meta.extract_state "
        "WHERE table_name = 'tickets'"
    ).fetchone()
    last_loaded = warehouse.execute(
        "SELECT updated_at, ticket_id FROM raw.tickets ORDER BY updated_at DESC, ticket_id DESC "
        "LIMIT 1"
    ).fetchone()
    assert watermark == last_loaded
    status = warehouse.execute(
        "SELECT status FROM meta.extract_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()[0]
    warehouse.commit()
    assert status == "failed"

    monkeypatch.undo()
    run(cfg, "tickets", source, warehouse)  # the next run resumes
    assert count(warehouse, "SELECT COUNT(*) FROM raw.tickets") == len(
        source_rows(source, "tickets", "ticket_id")
    )


def test_ctrl_c_between_load_and_watermark_saves_nothing_half_done(
    extract_config: ExtractorConfig,
    source: Connection,
    warehouse: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl+C raises KeyboardInterrupt, which is not an Exception. Interrupted
    after loading batch 2 but before saving its watermark, the run must roll
    batch 2 back entirely: rows and watermark stay consistent."""
    cfg = dataclasses.replace(extract_config, batch_size=500)
    real_save = TableExtractor._save_progress
    calls = {"n": 0}

    def interrupt_on_second_save(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(TableExtractor, "_save_progress", interrupt_on_second_save)
    with pytest.raises(KeyboardInterrupt):
        run(cfg, "tickets", source, warehouse)

    assert count(warehouse, "SELECT COUNT(*) FROM raw.tickets") == 500  # batch 1 only
    watermark = warehouse.execute(
        "SELECT watermark_updated_at, watermark_id FROM meta.extract_state"
    ).fetchone()
    last_loaded = warehouse.execute(
        "SELECT updated_at, ticket_id FROM raw.tickets ORDER BY updated_at DESC, ticket_id DESC "
        "LIMIT 1"
    ).fetchone()
    status = warehouse.execute(
        "SELECT status FROM meta.extract_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()[0]
    warehouse.commit()
    assert watermark == last_loaded
    assert status == "failed"  # not stuck in "running"


def test_a_second_concurrent_run_is_skipped(
    extract_config: ExtractorConfig,
    source: Connection,
    warehouse: psycopg.Connection,
    warehouse_db: str,
) -> None:
    from rail_ticketing.extract.connections import connect_warehouse

    with connect_warehouse(warehouse_db) as other_session:
        # Another session holds the lock of the tickets table.
        other_session.execute("SELECT pg_advisory_lock(hashtext('rail-extract:tickets'))")
        result = run(extract_config, "tickets", source, warehouse)

    assert result.status == "skipped"
    assert count(warehouse, "SELECT COUNT(*) FROM raw.tickets") == 0


# --- Protecting the source ---------------------------------------------------------


class UntouchableSource:
    """Fails the test if the extractor sends anything to the source."""

    def cursor(self):
        raise AssertionError("the source must not be queried outside the window")


def test_outside_the_time_window_the_source_is_not_queried(
    extract_config: ExtractorConfig, warehouse: psycopg.Connection
) -> None:
    daytime_only = dataclasses.replace(extract_config, window=Window.parse("08:00", "18:00"))

    result = run(daytime_only, "tickets", UntouchableSource(), warehouse, clock=lambda: NIGHT)

    assert result.status == "skipped"
    assert "window" in result.message


def test_a_closing_window_stops_the_run_between_two_batches(
    extract_config: ExtractorConfig, source: Connection, warehouse: psycopg.Connection
) -> None:
    cfg = dataclasses.replace(extract_config, batch_size=500, window=Window.parse("02:00", "04:00"))
    # Inside the window for the start check and batch 1, then closed.
    times = iter([NIGHT, NIGHT.replace(hour=5)])

    result = run(
        cfg, "tickets", source, warehouse, clock=lambda: next(times, NIGHT.replace(hour=5))
    )

    assert result.status == "success"
    assert result.batches == 1
    assert "window closed" in result.message
    assert count(warehouse, "SELECT COUNT(*) FROM raw.tickets") == 500


def test_the_source_query_is_an_index_range_scan(
    extract_config: ExtractorConfig, source: Connection, warehouse: psycopg.Connection
) -> None:
    extractor = TableExtractor(extract_config, extract_config.table("tickets"), source, warehouse)
    query = extractor._source_query(extractor._columns())
    with source.cursor() as cur:
        cur.execute(f"EXPLAIN {query}", (datetime(2026, 1, 1),) * 2 + (0, 10000))
        names = [d[0] for d in cur.description]
        plan = dict(zip(names, cur.fetchone(), strict=True))
    assert plan["key"] == "ix_tickets_updated_at"
    assert plan["type"] == "range"
    assert "filesort" not in (plan["Extra"] or "")  # rows come out in index order


# --- Known limits, written down as tests ----------------------------------------------------


def _backdate_customer(app_source: Connection, minutes_before_watermark: int, wm: datetime) -> int:
    """Change a customer with an updated_at in the past, as a transaction that
    started `minutes_before_watermark` before the watermark and committed now."""
    with app_source.cursor() as cur:
        cur.execute("SELECT MIN(customer_id) FROM customers")
        customer_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE customers SET city = 'Libreville', updated_at = %s WHERE customer_id = %s",
            (wm - timedelta(minutes=minutes_before_watermark), customer_id),
        )
    app_source.commit()
    return customer_id


def _watermark(warehouse: psycopg.Connection, table: str) -> datetime:
    value = warehouse.execute(
        "SELECT watermark_updated_at FROM meta.extract_state WHERE table_name = %s", (table,)
    ).fetchone()[0]
    warehouse.commit()
    return value


def test_a_late_commit_within_the_overlap_is_caught(
    extract_config: ExtractorConfig,
    source: Connection,
    warehouse: psycopg.Connection,
    app_source: Connection,
) -> None:
    run(extract_config, "customers", source, warehouse)
    customer_id = _backdate_customer(app_source, 5, _watermark(warehouse, "customers"))

    run(extract_config, "customers", source, warehouse)

    city = warehouse.execute(
        "SELECT city FROM raw.customers WHERE customer_id = %s", (customer_id,)
    ).fetchone()[0]
    assert city == "Libreville"


def test_known_limit_a_commit_later_than_the_overlap_is_missed(
    extract_config: ExtractorConfig,
    source: Connection,
    warehouse: psycopg.Connection,
    app_source: Connection,
) -> None:
    """Documents the limit of watermark extraction (ADR 0001): a change whose
    updated_at is older than watermark - overlap is never seen. CDC (reading
    the binlog) does not have this limit."""
    run(extract_config, "customers", source, warehouse)
    customer_id = _backdate_customer(
        app_source, extract_config.overlap_minutes + 20, _watermark(warehouse, "customers")
    )

    run(extract_config, "customers", source, warehouse)

    city = warehouse.execute(
        "SELECT city FROM raw.customers WHERE customer_id = %s", (customer_id,)
    ).fetchone()[0]
    assert city != "Libreville"


def test_a_column_missing_in_the_source_fails_loudly(
    extract_config: ExtractorConfig,
    source: Connection,
    warehouse: psycopg.Connection,
    warehouse_admin: psycopg.Connection,
) -> None:
    warehouse_admin.execute("ALTER TABLE raw.stations ADD COLUMN opening_year INTEGER")

    with pytest.raises(SchemaDriftError, match="opening_year"):
        run(extract_config, "stations", source, warehouse)

    status, message = warehouse.execute(
        "SELECT status, message FROM meta.extract_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    assert status == "failed"
    assert "opening_year" in message


def test_decimals_and_microseconds_survive_the_trip(
    extract_config: ExtractorConfig, source: Connection, warehouse: psycopg.Connection
) -> None:
    run(extract_config, "payments", source, warehouse)
    amount, attempted_at = warehouse.execute(
        "SELECT amount, attempted_at FROM raw.payments ORDER BY payment_id LIMIT 1"
    ).fetchone()
    with source.cursor() as cur:
        cur.execute("SELECT amount, attempted_at FROM payments ORDER BY payment_id LIMIT 1")
        assert (amount, attempted_at) == cur.fetchone()
    assert isinstance(amount, Decimal)  # never a float: money stays exact
