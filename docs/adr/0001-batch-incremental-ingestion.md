# ADR 0001 — Batch incremental ingestion first, CDC later

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The commercial team needs sales reporting (revenue per line and period, load
factor, cancellation/refund rates, channel mix, peak hours) without putting
load on the production ticketing database (MariaDB, OLTP).

Data must be copied from MariaDB to a dedicated PostgreSQL warehouse. Two
families of approaches were considered:

1. **Batch incremental extraction**: periodically read rows whose
   `updated_at` is greater than the last loaded value (the *watermark*).
2. **Change Data Capture (CDC)**: stream every change from the MariaDB binary
   log (e.g. Debezium + Kafka) into the warehouse.

Constraints: one developer, 4 weeks, a 16 GB laptop (Docker limited to ~8 GB),
and hourly freshness is enough for commercial reporting.

## Decision

Start with **batch incremental extraction**, written in Python:

- watermark per table stored in the warehouse (`meta` schema);
- keyset pagination on `(updated_at, id)`, small batches, a pause between
  batches and an allowed time window, to keep the source load low;
- a re-read overlap before the watermark, to catch late-committed rows;
- idempotent upsert into the `raw` layer, committed in the **same
  transaction** as the new watermark.

CDC through the binlog is documented as the next evolution, not built in v1.

## Consequences

- **+** Few moving parts: fits in RAM and in the timeline; every step of the
  incremental logic is visible and testable.
- **+** Hourly freshness meets the business need.
- **−** Physical `DELETE`s at the source are invisible to a watermark. The
  source schema therefore uses status changes (soft deletes) instead.
- **−** Correctness depends on the source maintaining `updated_at` on every
  write: enforced at database level (`ON UPDATE CURRENT_TIMESTAMP`), not by
  the application.
- **−** Each run re-reads the overlap window: a small, bounded extra cost.
