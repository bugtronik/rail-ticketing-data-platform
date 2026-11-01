# ADR 0008 — Incremental fact table and customer history (SCD type 2)

- **Status:** Accepted
- **Date:** 2026-10-28

## Context

Two limits of ADR 0006, now that the pipeline runs every hour:

1. **Every run rebuilt `fct_tickets` in full**: 2.5 M rows rewritten to
   take in a few hundred changes.
2. **`dim_customer` only knew the current segment.** The live application
   changes customers' loyalty tier; when a SILVER customer becomes GOLD,
   their past purchases moved to GOLD as well, and history was rewritten.

## Decision

**Customer history with a dbt snapshot (SCD type 2).** `snap_customers`
(in its own `snapshots` schema) compares each customer's `city` and
`loyalty_tier` with its last version at every run, and opens a new version
when one of them changed.

- Built on `stg_customers`, not on `raw`: names, e-mail and phone never
  enter the history.
- `check` strategy on the two tracked columns: a new phone number (an
  update too) does not create a version. Versions are dated with the
  source's `updated_at`, not with the time the snapshot ran.
- `dim_customer` has one row per version, keyed by `customer_sk`;
  `fct_tickets.customer_sk` points to the version valid when the order was
  placed. The first version of each customer starts in 1900: the history
  begins when the snapshot started, and before that the current value is
  the best information available.
- The `snapshots` schema cannot be rebuilt from `raw`, which only holds the
  latest version of a row. A full refresh never touches snapshots.

**`fct_tickets` incremental.** A ticket's row depends on the ticket, its
order, its trip and its refunds. Each run re-processes the tickets for
which any of these arrived in the warehouse since the previous run
(`_extracted_at`, set by the extractor), plus the last day of orders (a
customer's new segment can reach the snapshot one run after an order
placed just after the change). Rows are replaced by key (`delete+insert`
on `ticket_id`), so re-processing is harmless, and the window starts one
hour before the last load, for loads that committed late (the same idea as
the extractor's overlap).

The bounds are read **before** the query is written (`run_query`), so they
reach PostgreSQL as constants. The first version used a subquery: the
planner could not know the values, expected millions of rows, and the
incremental run took 110 s, more than a full rebuild (20 s). With
constants, its estimate is right (21,854 rows estimated, 23,973 actual).

**Planner statistics after each load.** Measuring this ADR revealed
that PostgreSQL never analyzed the small raw tables: autovacuum waits for
50 rows + 10 % of a table to change, and `line_stops` has 36 rows. Without
statistics, the planner guessed their size and chose, for the models that
join them, a plan 50 times slower (`fct_tickets` on 100,000 tickets: 35 s,
then 0.7 s after `ANALYZE`). The same trap exists one level up: a mart
table rebuilt in the same run has no statistics yet when the next model
joins it (`fct_tickets` full rebuild: 109 s, then 16 s). Two fixes: the
extractor runs `ANALYZE` on every raw table it has written to (`MAINTAIN`
privilege, PostgreSQL 17+, rather than ownership), and every mart and
snapshot is analyzed right after it is built (dbt `post-hook`). Existing warehouses get the privilege, the `snapshots` schema
and a first `ANALYZE` from `warehouse/migrations/` (`make migrate-warehouse`):
`init/` scripts only run on an empty volume.

**Proof of correctness.** After real changes (live activity, three tier
changes, a refund on a ticket sold months earlier), the incremental table
and a full rebuild were compared row by row: 0 difference on 2.52 M rows.
Two tests check it on every run: sales **and refunds** reconcile with
`raw`, and every ticket of `raw` is in the table. Removing the refunds from
the change detection makes the refunds reconciliation fail.

## Measurements (2 years, test machine with 2 CPUs, statistics up to date)

| | Before (J6) | After (J7) |
|---|---|---|
| `fct_tickets`, hourly run | 12.4 s, 2.52 M rows rewritten | 9-10 s, ~21 K rows re-processed (0.8 %) |
| Whole `dbt build`, hourly run | 35-37 s | 33-40 s |
| Full rebuild (`--full-refresh`) | | 43 s (`fct_tickets`: 16 s) |
| Tests alone (`dbt test`) | | 18 s |

## Consequences

- **+** Sales keep the customer's segment of the time; the history of
  segments is queryable (`valid_from` / `valid_to`).
- **+** The hourly run rewrites 0.8 % of the fact table instead of all of
  it: far less write I/O, and the table is never dropped and swapped while
  Power BI reads it.
- **−** At this volume, incremental loading saves almost no **time**: the
  run stays at ~35 s. Finding the changed tickets still reads `raw`, and
  the tests (18 s, about half of a run) re-check all 2.5 M rows every hour.
  Testing only the new rows hourly and everything daily is the next lever.
  The large gains of this milestone came from the planner statistics
  (35 s to 0.7 s, 109 s to 16 s), not from incremental loading: measuring
  before optimising found the real bottleneck.
- **−** More complexity: a watermark, a look-back window, a dependency hint
  for dbt, and a rule to remember: after changing an incremental model or
  fixing a bug in it, rebuild it in full (`make transform-full`, or
  `make run-full` through Airflow). A run with a bug has already moved the
  watermark, and later runs would not re-process what it missed.
- **−** Changing a tracked column of the snapshot, or losing the
  `snapshots` schema (`make reset-warehouse`), loses the history.
