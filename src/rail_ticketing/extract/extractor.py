"""Incremental extraction of one table: MariaDB batches -> PostgreSQL raw layer.

For each table:

  1. take a lock, so two runs never extract the same table at the same time;
  2. check the allowed time window: outside it, the source is not touched;
  3. compare the source columns with the raw table (schema drift);
  4. read the watermark (updated_at, id) of the last loaded row and step back
     by `overlap_minutes`, to catch rows whose transaction committed late;
  5. read the source in batches, in (updated_at, id) order, each batch
     starting right after the last row of the previous one (keyset
     pagination: no OFFSET, each query is an index range scan);
  6. for each batch, in ONE warehouse transaction: COPY the rows into a
     temporary table, upsert them into raw (INSERT ... ON CONFLICT), move the
     watermark forward, update the run log. Either everything is committed or
     nothing is: after a crash, the watermark is never ahead of the data.

Running twice is harmless: rows already loaded are matched on their primary
key and left untouched (idempotence).
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from pymysql.connections import Connection as MariaDBConnection

from rail_ticketing.extract.config import ExtractorConfig, TableSpec

log = logging.getLogger(__name__)

# Starting point when a table has never been extracted.
EPOCH = datetime(1970, 1, 1)


class SchemaDriftError(RuntimeError):
    """A column of the raw table no longer exists in the source."""


@dataclass
class RunResult:
    table: str
    status: str  # success | skipped | failed
    run_id: int | None = None
    batches: int = 0
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    source_seconds: float = 0.0
    seconds: float = 0.0
    watermark_from: datetime | None = None
    watermark_to: datetime | None = None
    message: str = ""


class TableExtractor:
    """Extracts one table. `clock` and `sleep` can be replaced in tests."""

    def __init__(
        self,
        cfg: ExtractorConfig,
        spec: TableSpec,
        source: MariaDBConnection,
        warehouse: psycopg.Connection,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg, self.spec = cfg, spec
        self.source, self.warehouse = source, warehouse
        self.clock = clock or (lambda: datetime.now(ZoneInfo(cfg.timezone)))
        self.sleep = sleep
        self.raw = sql.Identifier("raw", spec.name)
        self.stage = sql.Identifier(f"stage_{spec.name}")

    # --- Public entry point ------------------------------------------------------

    def run(self, full: bool = False) -> RunResult:
        result = RunResult(table=self.spec.name, status="running")
        started = time.perf_counter()
        if not self._try_lock():
            return self._skip(result, "another extraction of this table is running")
        try:
            if not self._in_window():
                return self._skip(result, "outside the allowed time window")
            self._extract(result, full)
        except BaseException as exc:
            # BaseException, not Exception: Ctrl+C (KeyboardInterrupt) and a
            # stop signal (SystemExit) must also roll the batch in progress
            # back. Otherwise the commit below would save half a batch.
            self.warehouse.rollback()
            result.status, result.message = "failed", f"{type(exc).__name__}: {exc}"[:1000]
            self._finish_run(result)
            raise
        finally:
            result.seconds = time.perf_counter() - started
            self._unlock()
        return result

    # --- Steps ---------------------------------------------------------------------

    def _extract(self, result: RunResult, full: bool) -> None:
        if full:  # forget the watermark: everything is read again
            self._execute("DELETE FROM meta.extract_state WHERE table_name = %s", self.spec.name)
        watermark = self._watermark()
        start = (max(watermark[0] - timedelta(minutes=self.cfg.overlap_minutes), EPOCH), 0)
        result.watermark_from = watermark[0]
        result.run_id = self._start_run(result.watermark_from)  # logged before any check

        columns = self._columns()
        key_index, updated_index = columns.index(self.spec.key), columns.index("updated_at")
        self._create_stage(columns)

        source_sql = self._source_query(columns)
        upsert = self._upsert_statement(columns, force=full)
        position = start
        while True:
            if result.batches and not self._in_window():
                result.message = "stopped between two batches: the time window closed"
                break
            began = time.perf_counter()
            with self.source.cursor() as cur:
                cur.execute(
                    source_sql, (position[0], position[0], position[1], self.cfg.batch_size)
                )
                rows = cur.fetchall()
            result.source_seconds += time.perf_counter() - began
            if not rows:
                break

            last = rows[-1]
            position = (last[updated_index], last[key_index])
            inserted, updated = self._load_batch(columns, rows, upsert, result.run_id)
            result.batches += 1
            result.rows_read += len(rows)
            result.rows_inserted += inserted
            result.rows_updated += updated
            result.watermark_to = max(position[0], result.watermark_to or EPOCH)
            self._save_progress(position, result)
            self.warehouse.commit()  # data + watermark + run log: all or nothing

            if len(rows) < self.cfg.batch_size:
                break
            self.sleep(self.cfg.pause_seconds)

        result.status = "success"
        self._finish_run(result)

    # --- Source side --------------------------------------------------------------------

    def _columns(self) -> list[str]:
        """Columns of the raw table (minus our _ metadata), checked against the source."""
        with self.warehouse.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'raw' AND table_name = %s ORDER BY ordinal_position",
                (self.spec.name,),
            )
            columns = [name for (name,) in cur.fetchall() if not name.startswith("_")]
        with self.source.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (self.spec.name,),
            )
            in_source = {name for (name,) in cur.fetchall()}
        missing = [c for c in columns if c not in in_source]
        if missing:
            raise SchemaDriftError(f"{self.spec.name}: missing in source: {missing}")
        new = sorted(in_source - set(columns))
        if new:  # the source evolved: not fatal, but raw should follow
            log.warning("%s: source columns not in raw (ignored): %s", self.spec.name, new)
        if self.spec.key not in columns or "updated_at" not in columns:
            raise SchemaDriftError(f"{self.spec.name}: key or updated_at missing")
        return columns

    def _source_query(self, columns: list[str]) -> str:
        """Keyset pagination on (updated_at, key).

        `updated_at >= ts AND (updated_at > ts OR key > id)` is the row
        comparison (updated_at, key) > (ts, id), written so that MariaDB uses
        a range scan on the (updated_at, key) index. FORCE INDEX makes that
        plan certain: on production, a planner mistake would mean a full
        scan of millions of rows, every hour.
        """
        t, key = self.spec.name, self.spec.key
        cols = ", ".join(f"`{c}`" for c in columns)
        # Identifiers are validated against IDENTIFIER in config.py.
        return (
            f"SELECT {cols} FROM `{t}` FORCE INDEX (`ix_{t}_updated_at`) "  # noqa: S608
            f"WHERE `updated_at` >= %s AND (`updated_at` > %s OR `{key}` > %s) "
            f"ORDER BY `updated_at`, `{key}` LIMIT %s"
        )

    # --- Warehouse side -------------------------------------------------------------------

    def _execute(self, query: str, *params: object) -> None:
        with self.warehouse.cursor() as cur:
            cur.execute(query, params)

    def _watermark(self) -> tuple[datetime, int]:
        with self.warehouse.cursor() as cur:
            cur.execute(
                "SELECT watermark_updated_at, watermark_id FROM meta.extract_state "
                "WHERE table_name = %s",
                (self.spec.name,),
            )
            row = cur.fetchone()
        return (row[0], row[1]) if row else (EPOCH, 0)

    def _create_stage(self, columns: list[str]) -> None:
        """Temporary table with the raw columns, emptied at every commit."""
        cols = sql.SQL(", ").join(map(sql.Identifier, columns))
        with self.warehouse.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(self.stage))
            cur.execute(
                sql.SQL(
                    "CREATE TEMP TABLE {} ON COMMIT DELETE ROWS AS SELECT {} FROM {} WITH NO DATA"
                ).format(self.stage, cols, self.raw)
            )
        self.warehouse.commit()

    def _upsert_statement(self, columns: list[str], force: bool) -> sql.Composed:
        """INSERT ... ON CONFLICT: new rows are inserted, known rows updated.

        A known row is only rewritten if the source version is newer: re-read
        rows (the overlap) cost no write. `force` (full refresh) rewrites all.
        RETURNING (xmax = 0) is true for inserted rows, false for updated ones.
        """
        cols = sql.SQL(", ").join(map(sql.Identifier, columns))
        updates = sql.SQL(", ").join(
            sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
            for c in columns
            if c != self.spec.key
        )
        newer = sql.SQL("TRUE" if force else "{raw}.updated_at < EXCLUDED.updated_at").format(
            raw=self.raw
        )
        return sql.SQL(
            "INSERT INTO {raw} ({cols}, _extract_run_id) "
            "SELECT {cols}, %s FROM {stage} "
            "ON CONFLICT ({key}) DO UPDATE SET {updates}, "
            "_extracted_at = NOW(), _extract_run_id = EXCLUDED._extract_run_id "
            "WHERE {newer} "
            "RETURNING (xmax = 0) AS inserted"
        ).format(
            raw=self.raw,
            cols=cols,
            stage=self.stage,
            key=sql.Identifier(self.spec.key),
            updates=updates,
            newer=newer,
        )

    def _load_batch(
        self, columns: list[str], rows: list[tuple], upsert: sql.Composed, run_id: int
    ) -> tuple[int, int]:
        """COPY the batch into the stage table, then upsert.

        Returns (rows inserted, rows updated). Re-read rows that did not
        change are neither: the upsert leaves them alone.
        """
        cols = sql.SQL(", ").join(map(sql.Identifier, columns))
        with self.warehouse.cursor() as cur:
            # COPY streams rows in bulk: much faster than one INSERT per row.
            with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(self.stage, cols)) as copy:
                for row in rows:
                    copy.write_row(row)
            cur.execute(upsert, (run_id,))
            flags = [inserted for (inserted,) in cur.fetchall()]
        inserted = sum(flags)
        return inserted, len(flags) - inserted

    def _save_progress(self, position: tuple[datetime, int], result: RunResult) -> None:
        """Move the watermark forward (never backward) and log the progress.

        Runs inside the batch transaction, committed together with the rows.
        """
        self._execute(
            """
            INSERT INTO meta.extract_state AS s (table_name, watermark_updated_at, watermark_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (table_name) DO UPDATE
            SET watermark_updated_at = EXCLUDED.watermark_updated_at,
                watermark_id = EXCLUDED.watermark_id,
                updated_at = NOW()
            WHERE (s.watermark_updated_at, s.watermark_id)
                < (EXCLUDED.watermark_updated_at, EXCLUDED.watermark_id)
            """,
            self.spec.name,
            position[0],
            position[1],
        )
        self._update_run(result)

    # --- Run log -------------------------------------------------------------------

    def _start_run(self, watermark_from: datetime) -> int:
        with self.warehouse.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.extract_runs (table_name, status, watermark_from) "
                "VALUES (%s, 'running', %s) RETURNING run_id",
                (self.spec.name, watermark_from),
            )
            run_id = cur.fetchone()[0]
        self.warehouse.commit()  # visible even if the run crashes later
        return run_id

    def _update_run(self, result: RunResult, finished: bool = False) -> None:
        self._execute(
            """
            UPDATE meta.extract_runs
            SET status = %s, batches = %s, rows_read = %s, rows_inserted = %s,
                rows_updated = %s, source_seconds = %s, watermark_to = %s, message = %s,
                finished_at = CASE WHEN %s THEN NOW() END
            WHERE run_id = %s
            """,
            result.status,
            result.batches,
            result.rows_read,
            result.rows_inserted,
            result.rows_updated,
            round(result.source_seconds, 3),
            result.watermark_to,
            result.message or None,
            finished,
            result.run_id,
        )

    def _finish_run(self, result: RunResult) -> None:
        if result.run_id is not None:
            self._update_run(result, finished=True)
            self.warehouse.commit()

    def _skip(self, result: RunResult, reason: str) -> RunResult:
        result.status, result.message = "skipped", reason
        with self.warehouse.cursor() as cur:
            cur.execute(
                "INSERT INTO meta.extract_runs (table_name, status, finished_at, message) "
                "VALUES (%s, 'skipped', NOW(), %s) RETURNING run_id",
                (self.spec.name, reason),
            )
            result.run_id = cur.fetchone()[0]
        self.warehouse.commit()
        log.info("%s: skipped (%s)", self.spec.name, reason)
        return result

    # --- Concurrency and time window -----------------------------------------------------

    def _try_lock(self) -> bool:
        """PostgreSQL advisory lock, held by this session until released.

        A second extraction of the same table (a manual run during a
        scheduled one, say) gets False instead of loading the same rows twice
        and racing on the watermark.
        """
        with self.warehouse.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (self._lock_name,))
            locked = cur.fetchone()[0]
        self.warehouse.commit()
        return locked

    def _unlock(self) -> None:
        """Release the lock. Every path of run() has already committed or
        rolled back, so this commit only covers the unlock itself."""
        try:
            with self.warehouse.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (self._lock_name,))
            self.warehouse.commit()
        except psycopg.Error:
            # Broken connection: PostgreSQL releases session locks by itself
            # when the session ends, so there is nothing more to do.
            log.warning("%s: could not release the lock explicitly", self.spec.name)

    @property
    def _lock_name(self) -> str:
        return f"rail-extract:{self.spec.name}"

    def _in_window(self) -> bool:
        return self.cfg.window.allows(self.clock().time())
