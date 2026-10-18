"""Properties of the simulated sales, checked on 45 days generated in memory.

These tests check business rules (capacity, chronology, refunds) and that the
configured rates really show up in the data, within a statistical tolerance.
"""

import dataclasses
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.customers import CustomerPicker, build_customers
from rail_ticketing.generator.demand import DemandModel
from rail_ticketing.generator.history import MemoryWriter, generate_history
from rail_ticketing.generator.network import build_reference_data, plan_day
from rail_ticketing.generator.sales import REFERENCE_ALPHABET, SalesSimulator, reference_from_id

LOCAL_OFFSET = timedelta(hours=1)  # Africa/Libreville is UTC+1 all year


# --- Reproducibility ------------------------------------------------------------------


def _small_run(config: GeneratorConfig, now: datetime) -> MemoryWriter:
    writer = MemoryWriter()
    generate_history(config, writer, now=now, days=3)
    return writer


def test_same_seed_gives_identical_data(config: GeneratorConfig, now: datetime) -> None:
    first, second = _small_run(config, now), _small_run(config, now)
    for table in ("customers", "orders", "tickets", "payments", "refunds"):
        assert [dataclasses.astuple(r) for r in first.tables[table]] == [
            dataclasses.astuple(r) for r in second.tables[table]
        ], table


def test_another_seed_gives_different_data(config: GeneratorConfig, now: datetime) -> None:
    other = _small_run(dataclasses.replace(config, seed=config.seed + 1), now)
    reference = _small_run(config, now)
    assert [t.price for t in other.tables["tickets"]] != [
        t.price for t in reference.tables["tickets"]
    ]


# --- Business rules -----------------------------------------------------------------


def test_nothing_happens_after_now(history: MemoryWriter, now: datetime) -> None:
    for table, rows in history.tables.items():
        assert all(row.updated_at <= now for row in rows), table
        assert all(row.created_at <= row.updated_at for row in rows), table


def test_ids_and_foreign_keys_are_consistent(history: MemoryWriter) -> None:
    orders = {o.order_id for o in history.tables["orders"]}
    tickets = {t.ticket_id: t for t in history.tables["tickets"]}
    payments = {p.payment_id: p for p in history.tables["payments"]}
    assert all(t.order_id in orders for t in tickets.values())
    assert all(p.order_id in orders for p in payments.values())
    for refund in history.tables["refunds"]:
        # A refund goes back through a payment of the same order as the ticket.
        assert tickets[refund.ticket_id].order_id == payments[refund.payment_id].order_id


def test_trains_are_never_overbooked(history: MemoryWriter) -> None:
    """Segment by segment, the tickets still valid never exceed the seats."""
    stops_by_line = defaultdict(list)
    for stop in sorted(history.tables["line_stops"], key=lambda s: s.stop_sequence):
        stops_by_line[stop.line_id].append(stop.station_id)
    seats = {
        t.train_id: {"FIRST": t.seats_first, "SECOND": t.seats_second}
        for t in history.tables["trains"]
    }
    trips = {t.trip_id: t for t in history.tables["trips"]}

    occupancy: dict[tuple[int, str], np.ndarray] = {}
    for ticket in history.tables["tickets"]:
        if ticket.ticket_status == "CANCELLED":
            continue
        trip = trips[ticket.trip_id]
        stops = stops_by_line[trip.line_id]
        if trip.direction == "RETURN":
            stops = stops[::-1]
        o, d = stops.index(ticket.origin_station_id), stops.index(ticket.destination_station_id)
        assert o < d, "a journey goes in the direction of the train"
        key = (trip.trip_id, ticket.fare_class)
        occupancy.setdefault(key, np.zeros(len(stops) - 1, dtype=int))[o:d] += 1

    for (trip_id, fare_class), used in occupancy.items():
        assert used.max() <= seats[trips[trip_id].train_id][fare_class]


def test_tickets_are_bought_before_departure_and_used_after_arrival(
    history: MemoryWriter, now: datetime
) -> None:
    trips = {t.trip_id: t for t in history.tables["trips"]}
    for ticket in history.tables["tickets"]:
        trip = trips[ticket.trip_id]
        assert ticket.created_at <= trip.scheduled_departure_at - timedelta(minutes=30)
        assert (ticket.ticket_status == "CANCELLED") == (ticket.cancelled_at is not None)
        if ticket.ticket_status == "USED":
            assert trip.trip_status == "COMPLETED"
            assert trip.scheduled_arrival_at <= now


def test_a_cancelled_trip_has_no_valid_ticket_and_full_refunds(history: MemoryWriter) -> None:
    cancelled = {t.trip_id for t in history.tables["trips"] if t.trip_status == "CANCELLED"}
    assert cancelled, "45 days at 1% should include a few cancelled trips"
    tickets = {t.ticket_id: t for t in history.tables["tickets"]}
    assert all(t.ticket_status == "CANCELLED" for t in tickets.values() if t.trip_id in cancelled)
    for refund in history.tables["refunds"]:
        if refund.reason == "TRIP_CANCELLED":
            assert refund.amount == tickets[refund.ticket_id].price
            assert refund.refund_status != "REJECTED"


def test_refunds_follow_the_fare_rules(history: MemoryWriter) -> None:
    tickets = {t.ticket_id: t for t in history.tables["tickets"]}
    for refund in history.tables["refunds"]:
        ticket = tickets[refund.ticket_id]
        assert 0 < refund.amount <= ticket.price
        if refund.reason == "CUSTOMER_REQUEST":
            assert ticket.fare_type != "SAVER", "SAVER fares are not refundable"
            assert ticket.ticket_status == "CANCELLED"


def test_counters_only_sell_during_opening_hours(
    history: MemoryWriter, config: GeneratorConfig
) -> None:
    channel_ids = {c.channel_code: c.channel_id for c in history.tables["sales_channels"]}
    counters = {channel_ids["STATION_COUNTER"], channel_ids["PARTNER_AGENCY"]}
    local_hours = Counter(
        (o.ordered_at + LOCAL_OFFSET).hour
        for o in history.tables["orders"]
        if o.channel_id in counters
    )
    assert set(local_hours) <= set(range(6, 19))
    # Morning rush (8h) busier than early afternoon (13h), as configured.
    assert local_hours[8] > 1.5 * local_hours[13]


# --- Configured rates show up in the data ---------------------------------------------------


def test_voluntary_cancellation_rates_match_the_configuration(
    history: MemoryWriter, config: GeneratorConfig
) -> None:
    departed = {t.trip_id for t in history.tables["trips"] if t.trip_status == "COMPLETED"}
    paid_orders = {o.order_id for o in history.tables["orders"] if o.order_status == "CONFIRMED"}
    sold, cancelled = Counter(), Counter()
    for t in history.tables["tickets"]:
        if t.trip_id in departed and t.order_id in paid_orders:
            sold[t.fare_type] += 1
            cancelled[t.fare_type] += t.ticket_status == "CANCELLED"
    for fare_type, rate in config.lifecycle.cancellation_rates.items():
        assert cancelled[fare_type] / sold[fare_type] == pytest.approx(rate, abs=0.015)


def test_payment_failure_rate_matches_the_configuration(
    history: MemoryWriter, config: GeneratorConfig
) -> None:
    first_attempts = {}
    for payment in sorted(history.tables["payments"], key=lambda p: p.payment_id):
        first_attempts.setdefault(payment.order_id, payment)
    electronic = [
        p for p in first_attempts.values() if p.payment_method in {"CARD", "MOBILE_MONEY"}
    ]
    failed = sum(p.payment_status == "FAILED" for p in electronic) / len(electronic)
    assert failed == pytest.approx(config.lifecycle.payment_failure_rate, abs=0.01)


def test_august_trains_carry_more_passengers_than_february(config: GeneratorConfig) -> None:
    rng = random.Random(7)
    created = datetime(2027, 1, 1)
    reference = build_reference_data(config, created)
    customers = CustomerPicker(build_customers(config, rng, created, created))
    simulator = SalesSimulator(
        config, rng, DemandModel(config, date(2027, 1, 1), date(2028, 1, 1)), reference, customers
    )
    everything_sold = datetime(2030, 1, 1)  # far in the future: all sales have happened

    def tickets_per_trip(month: int) -> float:
        counts = []
        for day in range(1, 15):
            for trip in plan_day(config, date(2027, month, day)):
                outcome = simulator.simulate_trip(trip, 1, everything_sold)
                counts.append(sum(len(b.tickets) for b in outcome.bundles))
        return float(np.mean(counts))

    assert tickets_per_trip(8) > 1.3 * tickets_per_trip(2)


def test_booking_references_from_ids_are_unique_and_well_formed() -> None:
    references = [reference_from_id(i) for i in range(1, 200_001)]
    assert len(set(references)) == len(references)
    assert all(len(r) == 10 and set(r) <= set(REFERENCE_ALPHABET) for r in references)
