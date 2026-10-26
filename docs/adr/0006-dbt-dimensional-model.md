# ADR 0006 — dbt layers and a star schema with two fact tables

- **Status:** Accepted
- **Date:** 2026-10-24

## Context

The `raw` layer is an exact copy of the source: normalised, in UTC, with
personal data, and with business rules hidden in status columns (a ticket
whose order was never paid is still `ISSUED`). The business questions are:
revenue by line and period, load factor, cancellation and refund rates,
sales by channel, peak hours. They must be answered the same way by every
dashboard, and a wrong number must be caught before anyone sees it.

## Decision

**Three layers, one job each.**

| Layer | Materialised as | Job |
|---|---|---|
| `staging` | views | one model per raw table: rename, convert UTC to local time (`to_local_time` macro), drop personal data. No joins, no rules. |
| `intermediate` | views | each business rule written once: paid / cancelled / seat-holding flags, journey distance, refunds per ticket. |
| `marts` | tables | the star schema that BI reads. |

A dedicated role, `transformer`, reads `raw` and owns the three schemas it
builds; it cannot write `raw`. Names, emails and phone numbers stop in
`raw`: no schema that BI can read contains them.

**Two fact tables, because there are two grains.**

- `fct_tickets`: one row per ticket. Revenue, cancellations, refunds,
  channels, buying hours.
- `fct_trip_occupancy`: one row per trip. Seat-km offered against
  passenger-km sold. A trip with no ticket must still count as supply, so
  this cannot be derived on the fly from the ticket table in the BI tool.

Shared dimensions: `dim_date`, `dim_line`, `dim_station` (used twice:
origin and destination), `dim_channel`, `dim_customer`, `dim_trip`.

**Business rules, decided once.**

- A ticket is a sale only if its order is `CONFIRMED`. Tickets of an order
  whose payment failed count nowhere (a dbt unit test proves it case by
  case).
- Revenue is reported by **date of sale** (`order_date`), the commercial
  team's main view. `departure_date` answers "when do people travel".
- A refund stays attached to the original sale (`net_sales_amount`): the net
  sales of a past month can still go down when its tickets are refunded.
- Load factor = passenger-km / seat-km, aggregated as a ratio of sums, never
  as an average of per-trip ratios; departed (`COMPLETED`) trips only, since
  a scheduled trip is still selling. A cancelled trip offers 0 seat-km.
- Anonymous purchases point to customer `-1` (tier `ANONYME`): no blank
  member in the dashboard, and an inner join never drops a sale.

**Tests at three levels.**

1. Generic tests in YAML: unique and not-null keys, accepted values of
   every status, relationships between facts and dimensions.
2. A unit test (dbt 1.8+) on the most error-prone rule, with hand-written
   input rows.
3. Singular tests on business invariants: sales reconcile **to the franc**
   with the confirmed orders read straight from `raw`; a refund never
   exceeds its ticket; every ticket travels between two stops of its line;
   no ticket is bought after departure; load factor between 0 and 1.

Each test was checked by breaking the code on purpose: dropping the
"confirmed order" condition fails the unit test (the model is not even
built), counting unpaid tickets fails the reconciliation, doubling the
passenger-km fails the load-factor test.

**No dbt packages.** `dbt_utils` would save a few lines (date spine,
expression tests). Plain SQL (`generate_series`, singular tests) does the
same here, with one dependency less to install and pin.

## Measurements (2 years of history, WSL2 laptop, 16 GB RAM)

| | |
|---|---|
| `dbt build` | 133 nodes (22 models, 1 unit test, 110 data tests) in 54 s; every run costs the same (full rebuild) |
| Largest model | `fct_tickets`: 2.5 M rows in 25 s |
| Reconciliation | mart sales = confirmed orders in `raw`: 30,358,035,500 XAF on both sides |
| End-to-end CI job (1 month of data, test machine) | generate 18 s, extract 10 s, `dbt build` 10 s |

## Consequences

- **+** One definition per metric: Power BI only sums columns.
- **+** A join that loses or duplicates tickets, or a rule written wrong,
  fails `dbt build`, in CI as well (end-to-end job on a month of data).
- **−** The marts are rebuilt in full on every run. Fine at this volume;
  incremental models are planned (J7).
- **−** `dim_customer` keeps only the current city and loyalty tier (SCD
  type 1): a customer who moves changes the history of past sales. A dbt
  snapshot (SCD type 2) is planned (J7).
- **−** The age band is computed on today's date, not at purchase time.
- **−** BI authors must follow the aggregation rule for the load factor;
  it is written in the model documentation (`dbt docs`).
