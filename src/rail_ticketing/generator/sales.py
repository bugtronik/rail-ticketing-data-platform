"""Simulate the sales of one trip and everything that happens to them.

For each trip:
  1. draw how many seats are requested (Poisson around the expected demand);
  2. create the purchase requests (group, journey, class, fare, channel, time);
  3. replay them in chronological order against the seat capacity, segment by
     segment: a seat freed at Rivière-Noire can be sold again for the rest of
     the journey; requests that do not fit are lost (sold-out train);
  4. decide each ticket's fate: payment failure, voluntary cancellation, trip
     cancellation, travel (USED), delay compensation.

Events later than `now` have not happened yet: a ticket bought last week for
next month's train is ISSUED, its trip SCHEDULED. That is what makes the
history end in a realistic "present".

Rows are returned with ids set to None: ids are assigned by the caller
(sequentially in history mode, by AUTO_INCREMENT in live mode).
"""

import heapq
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np

from rail_ticketing.generator.config import Channel, GeneratorConfig
from rail_ticketing.generator.customers import CustomerPicker
from rail_ticketing.generator.demand import DemandModel
from rail_ticketing.generator.network import PlannedTrip, ReferenceData, to_utc
from rail_ticketing.generator.rows import (
    OrderRow,
    PaymentRow,
    RefundRow,
    TicketRow,
    TripRow,
)

ELECTRONIC_METHODS = {"CARD", "MOBILE_MONEY"}
REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


# Multiplying by a number coprime with 36**10 is a bijection modulo 36**10:
# distinct ids always give distinct references, which still look random.
_REFERENCE_SPACE = len(REFERENCE_ALPHABET) ** 10
_REFERENCE_MULTIPLIER = 2**61 - 1  # a prime number, hence coprime with 36**10


def reference_from_id(order_id: int) -> str:
    """Booking reference derived from the order id: unique, non-sequential."""
    n = (order_id * _REFERENCE_MULTIPLIER) % _REFERENCE_SPACE
    chars = []
    for _ in range(10):
        n, digit = divmod(n, len(REFERENCE_ALPHABET))
        chars.append(REFERENCE_ALPHABET[digit])
    return "".join(chars)


@dataclass
class OrderBundle:
    """An order with its tickets, payment attempts and refunds.

    Refunds point to Python objects (ticket, payment) rather than ids, because
    ids do not exist yet; the caller resolves them once ids are assigned.
    """

    order: OrderRow
    tickets: list[TicketRow] = field(default_factory=list)
    payments: list[PaymentRow] = field(default_factory=list)
    refunds: list[tuple[RefundRow, TicketRow, PaymentRow]] = field(default_factory=list)


@dataclass
class TripOutcome:
    trip: TripRow
    bundles: list[OrderBundle]
    seats_requested: int
    seats_lost: int  # demand that did not fit in the train


@dataclass
class _Request:
    """A purchase request, before we know whether a seat is available."""

    ordered_at: datetime  # naive UTC
    channel: Channel
    seats: int
    origin: int  # index in trip.stops
    destination: int
    fare_class: str
    fare_type: str
    lead_days: int


class SalesSimulator:
    """Turns planned trips into rows. One instance for a whole run."""

    def __init__(
        self,
        cfg: GeneratorConfig,
        rng: random.Random,
        demand: DemandModel,
        reference: ReferenceData,
        customers: CustomerPicker,
    ) -> None:
        self.cfg = cfg
        # Scalar draws (millions of them) use Python's `random`: ~50x faster per
        # call than numpy, whose strength is drawing whole arrays at once.
        # numpy is only used for the Poisson law, which `random` does not offer.
        # Its seed comes from `rng`, so a single seed still drives everything.
        self.rng = rng
        self.np_rng = np.random.default_rng(rng.getrandbits(64))
        self.demand = demand
        self.ref = reference
        self.customers = customers

    # --- Helpers --------------------------------------------------------------

    def _seconds(self, low: float, high: float) -> timedelta:
        return timedelta(seconds=self.rng.uniform(low, high))

    def money(self, amount: float) -> Decimal:
        """An amount rounded to the currency step (100 XAF), never below one step."""
        step = self.cfg.pricing.rounding
        return Decimal(max(step, round(amount / step) * step))

    def order_reference(self) -> str:
        """Random 10-character booking reference, e.g. 'K7Q2M9XA1C'.

        Live mode only: uniqueness is enforced by the database (retry on
        duplicate). History mode uses reference_from_id(), unique by design.
        """
        return "".join(self.rng.choices(REFERENCE_ALPHABET, k=10))

    def price(
        self, trip: PlannedTrip, request: _Request, passenger_type: str
    ) -> Decimal:
        p = self.cfg.pricing
        km = trip.stop_km[request.destination] - trip.stop_km[request.origin]
        fare = max(p.minimum_fare, km * p.second_class_per_km)
        if request.fare_class == "FIRST":
            fare *= p.first_class_multiplier
        fare *= p.fare_type_multipliers[request.fare_type]
        fare *= 1 - p.passenger_discounts[passenger_type]
        return self.money(fare)

    # --- Step 2: purchase requests ----------------------------------------------

    def _make_request(self, trip: PlannedTrip) -> _Request:
        """History mode: draw when the purchase happened."""
        rng, demand = self.rng, self.demand
        departure = trip.departure_local

        # Purchase date and hour. A purchase must happen at least 30 minutes
        # before departure: if the drawn hour is too late on that day, the
        # customer bought one day earlier.
        lead_days = demand.sample_lead_days(rng)
        channel = demand.sample_channel(
            rng, (departure - timedelta(days=lead_days)).date()
        )
        while True:
            day = (departure - timedelta(days=lead_days)).date()
            hour = demand.sample_hour(rng, channel)
            ordered = datetime.combine(
                day, datetime.min.time(), tzinfo=departure.tzinfo
            )
            ordered += timedelta(hours=hour) + self._seconds(0, 3600)
            if ordered <= departure - timedelta(minutes=30):
                break
            lead_days += 1
        return self.request_at(trip, to_utc(ordered), channel, lead_days)

    def request_at(
        self, trip: PlannedTrip, ordered_at: datetime, channel: Channel, lead_days: int
    ) -> _Request:
        """A purchase request at a known time (live mode calls this directly)."""
        rng, demand = self.rng, self.demand
        seats = demand.group_sizes.sample(rng)
        origin, destination = demand.sample_journey(rng, trip)
        fare_class = "FIRST" if rng.random() < trip.line.first_class_share else "SECOND"
        fare_type = demand.fare_types.sample(rng)
        if fare_type == "SAVER" and lead_days < self.cfg.pricing.saver_min_lead_days:
            fare_type = "STANDARD"  # SAVER fares are only sold in advance
        return _Request(
            ordered_at,
            channel,
            seats,
            origin,
            destination,
            fare_class,
            fare_type,
            lead_days,
        )

    # --- Payment ------------------------------------------------------------------

    def _payments(
        self, channel: Channel, ordered_at: datetime, amount: Decimal, now: datetime
    ) -> tuple[list[PaymentRow], datetime | None, datetime | None]:
        """Payment attempts. Returns (payments, confirmed_at, failed_at)."""
        rng, life = self.rng, self.cfg.lifecycle
        methods = self.demand.payment_methods[channel.code]

        def attempt(method: str, at: datetime, succeeds: bool) -> PaymentRow:
            """One payment attempt, in the state it has reached at `now`."""
            if not succeeds:
                outcome_at = at + self._seconds(2, 30)  # the provider answers "refused"
            elif method == "CASH":
                outcome_at = at
            elif method == "BANK_TRANSFER":
                outcome_at = at + self._seconds(3600, 24 * 3600)
            else:
                outcome_at = at + self._seconds(5, 120)
            reference = None
            if method != "CASH":
                reference = f"{method[:3]}-{rng.getrandbits(48):012X}"
            if outcome_at > now:  # the answer has not arrived yet
                status, settled_at, updated = "PENDING", None, at
            elif succeeds:
                status, settled_at, updated = "SUCCEEDED", outcome_at, outcome_at
            else:
                status, settled_at, updated = "FAILED", None, outcome_at
            return PaymentRow(
                None,
                None,
                method,
                amount,
                status,
                reference,
                at,
                settled_at,
                at,
                updated,
            )

        # Every random draw is made up front, whatever `now` is: the random
        # sequence, hence the generated data, does not depend on the run date.
        method = methods.sample(rng)
        at = ordered_at + self._seconds(10, 120)
        fails = (
            method in ELECTRONIC_METHODS and rng.random() < life.payment_failure_rate
        )
        retry_succeeds = rng.random() < life.payment_retry_success_rate
        retry_method, retry_at = methods.sample(rng), at + self._seconds(60, 600)
        abandoned_at = at + self._seconds(120, 900)

        # Attempts that have not started yet at `now` do not exist yet.
        if at > now:
            return [], None, None
        if not fails:
            payment = attempt(method, at, succeeds=True)
            return [payment], payment.settled_at, None
        payments = [attempt(method, at, succeeds=False)]
        if not retry_succeeds:
            return payments, None, abandoned_at
        if retry_at > now:
            return payments, None, None
        retry = attempt(retry_method, retry_at, succeeds=True)
        return [*payments, retry], retry.settled_at, None

    # --- Refunds --------------------------------------------------------------------

    def _refund(
        self, amount: Decimal, reason: str, requested_at: datetime, now: datetime
    ) -> RefundRow:
        """A refund in the state it has reached at `now`.

        A refund for a cancelled trip is owed by the operator: never rejected.
        """
        approved_at = requested_at + self._seconds(2 * 3600, 24 * 3600)
        paid_at = approved_at + self._seconds(12 * 3600, 6 * 24 * 3600)
        rejected = (
            reason != "TRIP_CANCELLED"
            and self.rng.random() < self.cfg.lifecycle.refund_rejection_rate
        )
        if approved_at > now:
            status, processed, updated = "REQUESTED", None, requested_at
        elif rejected:
            status, processed, updated = "REJECTED", approved_at, approved_at
        elif paid_at > now:
            status, processed, updated = "APPROVED", None, approved_at
        else:
            status, processed, updated = "PAID", paid_at, paid_at
        return RefundRow(
            None,
            None,
            None,
            amount,
            reason,
            status,
            requested_at,
            processed,
            requested_at,
            updated,
        )

    # --- Steps 3 and 4: one trip -------------------------------------------------------

    def simulate_trip(
        self, trip: PlannedTrip, trip_id: int, now: datetime
    ) -> TripOutcome:
        rng, life = self.rng, self.cfg.lifecycle
        departure, arrival = trip.departure_utc, trip.arrival_utc

        # Is the trip cancelled, and has the decision been taken yet?
        cancelled_at: datetime | None = None
        if rng.random() < life.trip_cancellation_rate:
            decision = departure - self._seconds(2 * 3600, 48 * 3600)
            cancelled_at = decision if decision <= now else None
        disrupted = rng.random() < life.disruption_rate

        # Step 2: requests, then step 3: replay them in time order.
        seats_requested = int(
            self.np_rng.poisson(self.demand.expected_seat_requests(trip))
        )
        requests: list[_Request] = []
        seats_in_requests = 0  # running total: re-summing the list each time is O(n^2)
        while seats_in_requests < seats_requested:
            requests.append(self._make_request(trip))
            seats_in_requests += requests[-1].seats
        requests.sort(key=lambda r: r.ordered_at)

        segments = len(trip.stops) - 1
        capacity = {"FIRST": trip.seats_first, "SECOND": trip.seats_second}
        occupied = {cls: np.zeros(segments, dtype=np.int32) for cls in capacity}
        releases: list[tuple[datetime, int, str, int, int]] = []  # seats freed later
        bundles: list[OrderBundle] = []
        seats_lost = 0

        for request in requests:
            if request.ordered_at > now:
                continue  # not bought yet
            if cancelled_at and request.ordered_at >= cancelled_at:
                continue  # nobody can buy a cancelled train
            # Free the seats of tickets cancelled before this purchase.
            while releases and releases[0][0] <= request.ordered_at:
                _, _, cls, o, d = heapq.heappop(releases)
                occupied[cls][o:d] -= 1
            used = occupied[request.fare_class][request.origin : request.destination]
            if used.max() + request.seats > capacity[request.fare_class]:
                seats_lost += request.seats
                continue
            used += request.seats

            bundle, freed_at = self._sell(
                trip, trip_id, request, cancelled_at, disrupted, now
            )
            bundles.append(bundle)
            for ticket, at in zip(bundle.tickets, freed_at, strict=True):
                if at is not None:
                    # id(ticket) breaks ties between equal times, so tuples always compare.
                    cls, o, d = request.fare_class, request.origin, request.destination
                    heapq.heappush(releases, (at, id(ticket), cls, o, d))

        # The trip row, in the state it has reached at `now`.
        if cancelled_at:
            status, updated = "CANCELLED", cancelled_at
        elif arrival <= now:
            status, updated = "COMPLETED", arrival
        else:
            status, updated = "SCHEDULED", departure - timedelta(days=90)
        trip_row = TripRow(
            trip_id=trip_id,
            service_number=trip.service_number,
            line_id=self.ref.line_ids[trip.line.code],
            train_id=self.ref.train_ids[trip.train_code],
            direction=trip.direction,
            scheduled_departure_at=departure,
            scheduled_arrival_at=arrival,
            trip_status=status,
            created_at=departure
            - timedelta(days=90),  # timetable published 90 days ahead
            updated_at=updated,
        )
        return TripOutcome(trip_row, bundles, seats_requested, seats_lost)

    def build_order(
        self, trip: PlannedTrip, trip_id: int, request: _Request, now: datetime
    ) -> OrderBundle:
        """The order, its tickets and its payment attempts, as they are at `now`.

        Shared by history and live modes, so both sell in exactly the same way.
        Tickets are ISSUED, or CANCELLED if the payment failed for good.
        """
        rng, demand = self.rng, self.demand
        ordered_at = request.ordered_at

        customer_id = None
        if rng.random() < request.channel.identified_share:
            customer_id = self.customers.pick(rng, ordered_at)

        # Passengers: in a group, the first one is an adult.
        passenger_types = [
            "ADULT"
            if (i == 0 and request.seats > 1)
            else demand.passenger_types.sample(rng)
            for i in range(request.seats)
        ]
        prices = [self.price(trip, request, p) for p in passenger_types]
        total = sum(prices, Decimal(0))
        payments, confirmed_at, failed_at = self._payments(
            request.channel, ordered_at, total, now
        )
        if failed_at and failed_at <= now:
            order_status, order_updated = "CANCELLED", failed_at
        elif confirmed_at:
            order_status, order_updated = "CONFIRMED", confirmed_at
        else:
            order_status, order_updated = "PENDING", ordered_at

        order = OrderRow(
            order_id=None,
            order_reference=self.order_reference(),
            customer_id=customer_id,
            channel_id=self.ref.channel_ids[request.channel.code],
            ordered_at=ordered_at,
            order_status=order_status,
            total_amount=total,
            currency=self.cfg.currency,
            created_at=ordered_at,
            updated_at=order_updated,
        )
        bundle = OrderBundle(order=order, payments=payments)
        for passenger_type, price in zip(passenger_types, prices, strict=True):
            ticket = TicketRow(
                ticket_id=None,
                order_id=None,
                trip_id=trip_id,
                origin_station_id=self.ref.station_ids[trip.stops[request.origin]],
                destination_station_id=self.ref.station_ids[
                    trip.stops[request.destination]
                ],
                fare_class=request.fare_class,
                fare_type=request.fare_type,
                passenger_type=passenger_type,
                price=price,
                ticket_status="ISSUED",
                cancelled_at=None,
                created_at=ordered_at,
                updated_at=ordered_at,
            )
            if order_status == "CANCELLED":
                self._cancel(ticket, failed_at)  # unpaid: cancelled, nothing to refund
            bundle.tickets.append(ticket)
        return bundle

    def _sell(
        self,
        trip: PlannedTrip,
        trip_id: int,
        request: _Request,
        trip_cancelled_at: datetime | None,
        disrupted: bool,
        now: datetime,
    ) -> tuple[OrderBundle, list[datetime | None]]:
        """History mode: build the order, then decide what happened to each ticket
        up to `now`. Also returns, for each ticket, when its seat was given back
        (or None), so the caller can sell it again."""
        rng, life = self.rng, self.cfg.lifecycle
        departure, arrival = trip.departure_utc, trip.arrival_utc
        bundle = self.build_order(trip, trip_id, request, now)
        order = bundle.order
        paid_with = next(
            (p for p in bundle.payments if p.payment_status == "SUCCEEDED"), None
        )
        freed_at: list[datetime | None] = []

        for ticket in bundle.tickets:
            if ticket.ticket_status == "CANCELLED":  # payment failed
                freed_at.append(ticket.cancelled_at)
                continue

            # When would the customer cancel (if ever)? Between the purchase
            # and one hour before departure.
            voluntary_at = None
            start = request.ordered_at + timedelta(minutes=10)
            end = departure - timedelta(hours=1)
            if (
                rng.random() < life.cancellation_rates[request.fare_type]
                and start < end
            ):
                voluntary_at = start + self._seconds(0, (end - start).total_seconds())

            # The first event that happens decides the ticket's fate.
            if (
                voluntary_at
                and voluntary_at <= now
                and (trip_cancelled_at is None or voluntary_at < trip_cancelled_at)
            ):
                self._cancel(ticket, voluntary_at)
                freed_at.append(voluntary_at)
                share = life.refund_shares[request.fare_type]
                if share > 0 and paid_with:
                    amount = self.money(float(ticket.price) * share)
                    refund = self._refund(amount, "CUSTOMER_REQUEST", voluntary_at, now)
                    bundle.refunds.append((refund, ticket, paid_with))
            elif trip_cancelled_at:
                self._cancel(ticket, trip_cancelled_at)
                freed_at.append(None)  # the train does not run: no resale
                if paid_with:
                    refund = self._refund(
                        ticket.price, "TRIP_CANCELLED", trip_cancelled_at, now
                    )
                    bundle.refunds.append((refund, ticket, paid_with))
            elif arrival <= now and order.order_status == "CONFIRMED":
                ticket.ticket_status, ticket.updated_at = "USED", arrival
                freed_at.append(None)
                if (
                    disrupted
                    and paid_with
                    and rng.random() < life.disruption_claim_rate
                ):
                    claimed_at = arrival + self._seconds(3600, 7 * 24 * 3600)
                    if claimed_at <= now:
                        amount = self.money(
                            float(ticket.price) * life.disruption_refund_share
                        )
                        refund = self._refund(
                            amount, "SERVICE_DISRUPTION", claimed_at, now
                        )
                        bundle.refunds.append((refund, ticket, paid_with))
            else:
                freed_at.append(None)
        return bundle, freed_at

    @staticmethod
    def _cancel(ticket: TicketRow, at: datetime | None) -> None:
        ticket.ticket_status, ticket.cancelled_at, ticket.updated_at = (
            "CANCELLED",
            at,
            at,
        )
