# ADR 0009 — Power BI on the marts: read-only account, import mode, ratios in DAX

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

The marts (ADR 0006, 0008) answer the business questions in SQL. The sales
team needs them as a dashboard: revenue by line and period, load factor,
cancellations and refunds, sales channels, peak hours.

## Decision

**A dedicated read-only account.** `bi_reader` can read the `marts` schema
and nothing else: not `raw` (personal data), not `staging`, not the
snapshots, and it cannot write. Its `SELECT` rights are granted by dbt
(`grants` in `dbt_project.yml`), because dbt recreates a table at each run
and a right granted once by hand would disappear with the old table. The
role itself is created by `warehouse/init/04_bi_reader.sh`, written to be
re-run safely, which `make migrate-warehouse` also runs on existing
warehouses. Checked: `bi_reader` reads the 8 marts tables, and is refused
on `raw`, `staging`, `snapshots` and on any `CREATE`.

**Import mode, not DirectQuery.** 2.6 M rows fit easily in Power BI's
engine, the data changes once an hour, and every visual then answers from
memory instead of sending a query to PostgreSQL.

**Ratios in DAX, everything additive in dbt.** Amounts, counts,
passenger-km and seat-km are columns of the marts. Rates (cancellation,
refund, load factor, online share) are DAX measures computed from those
sums in each visual's filter context. A rate stored per row would be
averaged by Power BI, which is wrong as soon as rows have different weights.

**A model that cannot be ambiguous.** One-to-many, single-direction
relationships only. Role-playing dimensions (`dim_date`: sale or travel;
`dim_station`: origin or destination) have one active relationship and one
inactive one, used explicitly with `USERELATIONSHIP`. Sales are linked to
the customer's version at the time of the order (`customer_sk`).

**Saved as a Power BI project (PBIP)**, so the report and its model are
text files reviewed in Git like code. No credentials in the files; the
server and database are Power Query parameters.

**Checked against SQL.** `transform/analyses/dashboard_checks.sql` computes
each measure's expected value; with no filter, every card must match it.

## Consequences

- **+** The dashboard cannot see personal data, even by mistake.
- **+** Each number of the report has one definition, and a SQL query that
  proves it.
- **−** Import mode shows the data of the last refresh: up to one hour plus
  the refresh interval behind. Scheduled refresh would need a Power BI
  service and a gateway, out of scope for a local project.
- **−** Power BI Desktop runs on Windows only; the CI cannot open or test
  the report.
