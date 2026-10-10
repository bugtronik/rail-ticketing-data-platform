# ADR 0003 — Source schema designed for incremental extraction

- **Status:** Accepted
- **Date:** 2026-10-10

## Context

The MariaDB database simulates the production system of a fictional rail
operator: stations, lines, trains, trips, customers, orders, tickets,
payments, refunds. It is normalised (3NF) for transactional use.

The warehouse will copy it with a watermark-based extractor (ADR 0001). That
extractor is only correct if the source keeps a few promises. In a real
company these promises are negotiated with the application team; here we
design them into the schema and **test them in CI**.

## Decision

| Promise to the extractor | How it is enforced |
|---|---|
| Every change moves `updated_at` forward | `ON UPDATE CURRENT_TIMESTAMP(6)` on every table: set by the database, not the application |
| Changed rows can be read cheaply, in a stable order | Index `(updated_at, <primary key>)` on every table: range scan for keyset pagination |
| Every table has the same shape of key | Single-column surrogate primary key everywhere (`line_stops` included) |
| No change is invisible | No physical `DELETE`: statuses change instead. The application account has no `DELETE` privilege |
| Times are comparable | Server time zone `+00:00`: every `DATETIME` is UTC |
| Extraction cannot hurt production | Read-only `etl_reader` account: 2 connections max, statements killed after 60 s |

Other choices:

- **Microsecond timestamps** (`DATETIME(6)`): fewer rows share the same
  `updated_at`; ties are still handled by paginating on the primary key.
- **Business time vs technical time**: `orders.ordered_at` is when the sale
  happened; `created_at` is when the row was written. Reports use the first,
  the extractor the second.
- **Sales channels as a table, not an ENUM**: a new channel is data, and the
  table maps directly to a warehouse dimension.
- **Cancelling is not refunding**: `tickets.ticket_status` and the `refunds`
  table are separate, so cancellation rate and refund rate are two KPIs.
- **Rules that span tables** (refund amount ≤ ticket price, ticket stations
  belong to the trip's line) cannot be `CHECK` constraints. They are the
  application's job and will be verified by dbt tests in the warehouse.

## Consequences

- **+** The extractor can be generic: one query pattern for all 11 tables.
- **+** The promises are executable: 37 integration tests run on every push.
- **−** Soft deletes keep every row forever. Acceptable at this volume; a real
  system would add archiving.
- **−** Init scripts only run on an empty volume: changing the schema means
  `make reset` (data is synthetic and regenerated). A long-lived system would
  use a migration tool (Flyway, Liquibase).
- **−** Database passwords are visible in the container's environment
  (`docker inspect`). Fine locally; production would use Docker secrets
  (the image supports `*_FILE` variables) or a vault.
