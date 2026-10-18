"""History mode: generate the past `history_days` days, plus sales already made
for trips of the coming `booking_horizon_days`.

The run goes day by day (by departure date) so that memory stays small: the
rows of one day are written, committed, then forgotten.
"""

import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.customers import CustomerPicker, build_customers
from rail_ticketing.generator.demand import DemandModel
from rail_ticketing.generator.network import build_reference_data, plan_day
from rail_ticketing.generator.sales import OrderBundle, SalesSimulator, reference_from_id

log = logging.getLogger(__name__)


class RowWriter(Protocol):
    """Where rows go. MariaDB in real runs, a list in unit tests.

    Depending on this small interface rather than on a database connection is
    what lets the whole simulation be tested without a database.
    """

    def write(self, table: str, rows: list) -> None: ...

    def commit(self) -> None: ...


@dataclass
class MemoryWriter:
    """RowWriter keeping everything in memory (unit tests, small runs)."""

    tables: dict[str, list] = field(default_factory=dict)

    def write(self, table: str, rows: list) -> None:
        self.tables.setdefault(table, []).extend(rows)

    def commit(self) -> None:
        pass


class IdSequence:
    """Hands out consecutive ids per table, like AUTO_INCREMENT on an empty table."""

    def __init__(self) -> None:
        self._last: Counter[str] = Counter()

    def next(self, table: str) -> int:
        self._last[table] += 1
        return self._last[table]


def assign_ids(bundles: list[OrderBundle], ids: IdSequence) -> None:
    """Give ids to the rows of each order, then fill in the foreign keys."""
    for bundle in bundles:
        bundle.order.order_id = ids.next("orders")
        bundle.order.order_reference = reference_from_id(bundle.order.order_id)
        for ticket in bundle.tickets:
            ticket.ticket_id = ids.next("tickets")
            ticket.order_id = bundle.order.order_id
        for payment in bundle.payments:
            payment.payment_id = ids.next("payments")
            payment.order_id = bundle.order.order_id
        for refund, ticket, payment in bundle.refunds:
            refund.refund_id = ids.next("refunds")
            refund.ticket_id, refund.payment_id = ticket.ticket_id, payment.payment_id


@dataclass
class HistoryStats:
    rows: Counter[str] = field(default_factory=Counter)
    seats_requested: int = 0
    seats_lost: int = 0
    seconds: float = 0.0


def generate_history(
    cfg: GeneratorConfig,
    writer: RowWriter,
    now: datetime | None = None,
    days: int | None = None,
) -> HistoryStats:
    """Generate the history ending at `now` (naive UTC; default: current time).

    `days` overrides cfg.history_days (tests use a few weeks).
    """
    started = time.perf_counter()
    now = now or datetime.now(UTC).replace(tzinfo=None)
    tz = ZoneInfo(cfg.timezone)
    today = now.replace(tzinfo=UTC).astimezone(tz).date()
    first_day = today - timedelta(days=days or cfg.history_days)
    last_day = today + timedelta(days=cfg.booking_horizon_days)
    history_start = datetime.combine(first_day, datetime.min.time())

    rng = random.Random(cfg.seed)
    stats = HistoryStats()

    def write(table: str, rows: list) -> None:
        writer.write(table, rows)
        stats.rows[table] += len(rows)

    # Reference data existed before the period we simulate.
    reference = build_reference_data(cfg, created_at=history_start - timedelta(days=365))
    write("stations", reference.stations)
    write("rail_lines", reference.lines)
    write("line_stops", reference.line_stops)
    write("trains", reference.trains)
    write("sales_channels", reference.channels)
    customers = build_customers(cfg, rng, history_start, now)
    write("customers", customers)
    writer.commit()
    log.info("reference data and %d customers written", len(customers))

    demand = DemandModel(cfg, first_day, today)
    simulator = SalesSimulator(cfg, rng, demand, reference, CustomerPicker(customers))
    ids = IdSequence()

    day: date = first_day
    while day <= last_day:
        trips, bundles = [], []
        for planned in plan_day(cfg, day):
            outcome = simulator.simulate_trip(planned, ids.next("trips"), now)
            trips.append(outcome.trip)
            bundles.extend(outcome.bundles)
            stats.seats_requested += outcome.seats_requested
            stats.seats_lost += outcome.seats_lost
        assign_ids(bundles, ids)
        # Parents before children, so foreign keys are always satisfied.
        write("trips", trips)
        write("orders", [b.order for b in bundles])
        write("tickets", [t for b in bundles for t in b.tickets])
        write("payments", [p for b in bundles for p in b.payments])
        write("refunds", [r for b in bundles for r, _, _ in b.refunds])
        writer.commit()  # one transaction per day of departures
        if day.day == 1:
            log.info("%s done, %d tickets so far", day.strftime("%Y-%m"), stats.rows["tickets"])
        day += timedelta(days=1)

    stats.seconds = time.perf_counter() - started
    return stats
