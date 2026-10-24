# ADR 0005 — Incremental extraction: keyset batches, transactional watermark

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

ADR 0001 chose batch extraction driven by a watermark on `updated_at`.
This ADR records how it is built. Requirements: copy 11 tables from the
MariaDB source to the PostgreSQL warehouse; never duplicate a row; survive a
crash at any point; keep the load on the production database low and
predictable; let a run be repeated safely.

## Decision

**Warehouse layout.** Schema `raw` holds one table per source table: same
columns and primary keys, types translated (no unsigned types in PostgreSQL;
ENUMs become TEXT; `DATETIME(6)` stays a UTC `TIMESTAMP` without time zone),
plus `_extracted_at` and `_extract_run_id`. No foreign keys and no business
constraints: raw accepts whatever the source holds, quality is tested later
by dbt. Schema `meta` holds the watermark of each table (`extract_state`) and
a log of every run (`extract_runs`).

**Reading the source (keyset pagination).** Each batch is
`WHERE (updated_at, id) > (last_ts, last_id) ORDER BY updated_at, id LIMIT n`,
written as `updated_at >= ts AND (updated_at > ts OR id > last_id)` so that
MariaDB runs an index range scan on `(updated_at, id)`. No `OFFSET`, which
would re-scan every skipped row and grow slower with each batch. The source
connection is in autocommit: every batch is a short transaction, so the
extractor never keeps an old snapshot open on production (that would block
InnoDB's purge of old row versions).

**Loading (idempotent upsert).** Rows are streamed with `COPY` into a
temporary table, then `INSERT ... ON CONFLICT (id) DO UPDATE ... WHERE
raw.updated_at < EXCLUDED.updated_at`. Re-running is harmless: known rows
match on their key, and unchanged rows are not even rewritten.

**Crash safety.** The rows of a batch, the new watermark and the run log are
committed in **one** PostgreSQL transaction. After a crash, the watermark can
never be ahead of the data; the next run resumes after the last committed
batch. The watermark only moves forward. This includes Ctrl+C: Python raises
`KeyboardInterrupt`, which is a `BaseException`, not an `Exception`; the run
catches `BaseException` and rolls the batch back. (Found by interrupting a
real load: with `except Exception`, the lock release committed half a batch.
A test now reproduces it.)

**Late commits (overlap).** A row's `updated_at` is set when its statement
runs, but the row becomes visible when its transaction commits. Each run
therefore starts `overlap_minutes` (10) before the watermark.

**Protecting the source.** Batches of 10,000 rows, a pause between batches,
a time window outside which the source is not queried at all (and a running
extraction stops between two batches), the `etl_reader` account limited to 2
connections and 60-second statements (ADR 0003). A PostgreSQL advisory lock
per table prevents two runs from extracting the same table at once.

**Plan stability.** The source query names its index (`FORCE INDEX`). On
the generated data the optimizer already picks it without the hint (measured
on 2.5 M tickets: same plan, 0.06 s for the first batch), so the hint is
insurance: if the contract index ever disappears, extraction fails loudly
instead of silently turning every hourly run into full table scans.

## Measurements (2 years of history, local machine)

| | |
|---|---|
| Initial load | 6.03 M rows in 244 s (~24,700 rows/s), 56 MB memory peak |
| Source time | 126 s of the 244 s; about 0.2 s per 10,000-row query |
| Incremental run after 2 min of live activity | 845 rows read in 0.3 s |
| Correctness | row counts, amount sums and latest `updated_at` identical on all 11 tables |

## Consequences

- **+** Any run can be repeated or interrupted: idempotent and crash-safe,
  proved by tests that crash a run on purpose.
- **+** The load on production is bounded and visible (`meta.extract_runs`
  records the source time of every run).
- **−** Physical deletes are invisible (mitigated by soft deletes, ADR 0003).
- **−** A transaction committing more than `overlap_minutes` after its
  `updated_at` is missed. A test documents this limit; reading the binlog
  (CDC) removes it.
- **−** Each run re-reads the overlap window (a few hundred rows): a small,
  bounded cost.
- **−** Raw table definitions are maintained by hand next to the source
  schema; a new source column is reported (warning) but not copied until
  raw is extended.
