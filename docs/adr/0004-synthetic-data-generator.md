# ADR 0004 — Synthetic data generator: history and live modes

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

The pipeline needs a source that behaves like a production ticketing
database: two years of history for the analyses, then a continuous flow of
inserts and updates so the incremental extractor (J3) has something to catch.
No real data may be used.

## Decision

A Python package, `rail_ticketing.generator`, driven by one configuration
file (`config/generator.toml`) that holds every business assumption.

**Network.** A fictional corridor (19 stations, ~650 km, coast to plateau)
with three service patterns: express, stopping train, night train. About six
long trains a day, as on a single-track long-distance line. Prices in XAF,
local time Africa/Libreville, stored in UTC.

**Demand model.** Seats requested per trip follow a Poisson law around
`base_demand × month × weekday × special day × yearly growth`. Each request
gets a group size, a journey (weighted by station importance), a class, a
fare type, a channel (shares drift online over two years) and a purchase
time (lead time + the channel's hourly profile).

**Capacity, segment by segment.** Requests are replayed in purchase order
against the seats of each segment of the line. A seat freed by a passenger
leaving at an intermediate station, or by a cancellation, can be sold again.
Requests that do not fit are lost: trains sell out at peak times.

**Life cycle.** Payment failures and retries, voluntary cancellations (rate
and refund share by fare type), cancelled trips (full refund), delay
compensation, refund processing. In history mode each row is written in the
state it has reached at `now`: nothing is dated in the future.

**Two modes, one sales logic.** History mode computes final states in Python
and bulk-inserts them, day of departure by day. Live mode runs a tick every
30 seconds with real `INSERT`/`UPDATE` statements; `created_at`/`updated_at`
are then set by the database. Both modes build orders with the same method
(`SalesSimulator.build_order`).

**Reproducibility.** One seed drives all random draws: same config and same
seed give byte-identical data (tested).

## Measured choices

| Choice | Measurement |
|---|---|
| Python `random` for scalar draws, numpy only for Poisson | numpy scalar calls cost ~6 µs each; profiling showed them as the top hotspot. Switching cut the unit tests from 33 s to 15 s |
| Running total instead of re-summing the request list | removed 24 million useless operations (O(n²) loop) |
| Day-by-day streaming with batched `executemany` | 6 million rows written in ~8 min with an 86 MB memory peak |
| Indexes on the status "work queues" (`PENDING` orders/payments, refunds) | a live tick went from 14 s (full scans of 1.6 M rows) to 0.05 s |

## Consequences

- **+** Realistic, explainable data: seasonality, rush hours, online shift,
  sold-out trains, cancellation ≠ refund. Every rate is in one file.
- **+** 24 unit tests run without a database; integration tests run in a
  rolled-back transaction.
- **−** History mode needs an empty source (it assigns ids itself): reset
  the volume to regenerate.
- **−** In history mode, ids follow departure dates rather than purchase
  times; real AUTO_INCREMENT ids would follow purchase times.
- **−** Live mode is a simplified model (uniform trip choice for a given
  lead time, no train swaps, no live trip cancellations).
- **−** Booking references: unique by construction in history mode; in live
  mode, random with a retry on the (rare) duplicate.
