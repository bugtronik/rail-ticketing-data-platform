{% docs __overview__ %}
# Rail ticketing data platform — dbt documentation

Analytics models for the ticket sales of a **fictional** rail operator.
All data is synthetic, produced by the project's own generator.
Source code: [github.com/bugtronik/rail-ticketing-data-platform](https://github.com/bugtronik/rail-ticketing-data-platform).

## Layers

| Layer | Materialised as | Job |
|---|---|---|
| `staging` | views | one model per raw table: renaming, UTC → local time (Africa/Libreville), personal data dropped |
| `intermediate` | views | each business rule written once (paid, cancelled, journey distance, refunds) |
| `marts` | tables | the star schema read by Power BI |

Click **Lineage** (bottom right) to see how every table is built, from the
`raw` copy of the source to the marts.

## Rules behind the numbers

- A ticket is a sale only if its order was paid (`CONFIRMED`).
- Revenue is reported by **date of sale** (`fct_tickets.order_date`);
  refunds stay attached to the original sale (`net_sales_amount`).
- Load factor = Σ passenger-km / Σ seat-km, on departed trips: a ratio of
  sums, never an average of per-trip ratios (`fct_trip_occupancy`).
- Anonymous purchases point to customer `-1`.

## History and incremental loading

- `dim_customer` keeps every version of a customer's city and loyalty tier
  (SCD type 2, from the `snap_customers` snapshot): a sale is analysed with
  the segment the customer had **when they bought**.
- `fct_tickets` is incremental: each hourly run rebuilds only the tickets
  whose ticket, order, trip or refunds changed, and two reconciliation tests
  check that nothing was missed.

## Quality

Every model is tested on each run: unique and not-null keys, accepted
values, relationships between facts and dimensions, a unit test on the
"paid" rule, and business invariants (sales and refunds reconcile with `raw` to the
franc, refunds never exceed the ticket price, load factor between 0 and 1).
{% enddocs %}
