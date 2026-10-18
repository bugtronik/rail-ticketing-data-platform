"""Live mode: behave like the production application, one tick at a time.

Each tick writes what happened since the previous one: new trips published,
trains that arrived, new sales, cancellations, refunds moving forward,
customers editing their profile. These are real INSERTs and UPDATEs, so
`updated_at` moves forward: exactly what the incremental extractor (J3) must
detect and copy.

Unlike history mode, `created_at` / `updated_at` are left to the database
(DEFAULT / ON UPDATE CURRENT_TIMESTAMP): the rows get the real write time.
"""

import logging
import random
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pymysql
from pymysql.connections import Connection

from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.customers import CustomerPicker
from rail_ticketing.generator.db import insert_one
from rail_ticketing.generator.demand import DemandModel
from rail_ticketing.generator.network import PlannedTrip, ReferenceData, plan_day
from rail_ticketing.generator.rows import CustomerRow, OrderRow
from rail_ticketing.generator.sales import SalesSimulator

log = logging.getLogger(__name__)

AUDIT = ("created_at", "updated_at")
DUPLICATE_KEY = 1062  # MariaDB error code


def _ids(conn: Connection, sql: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return dict(cur.fetchall())


def load_reference(conn: Connection) -> ReferenceData:
    """Code -> id lookups, read back from the database."""
    return ReferenceData(
        stations=[],
        lines=[],
        line_stops=[],
        trains=[],
        channels=[],
        station_ids=_ids(conn, "SELECT station_code, station_id FROM stations"),
        line_ids=_ids(conn, "SELECT line_code, line_id FROM rail_lines"),
        train_ids=_ids(conn, "SELECT train_code, train_id FROM trains"),
        channel_ids=_ids(conn, "SELECT channel_code, channel_id FROM sales_channels"),
    )


def load_customer_picker(conn: Connection) -> CustomerPicker:
    with conn.cursor() as cur:
        cur.execute("SELECT customer_id, created_at FROM customers ORDER BY created_at")
        stubs = [
            CustomerRow(cid, "", "", "", "", date.min, "", "", created, created)
            for cid, created in cur.fetchall()
        ]
    return CustomerPicker(stubs)


class LiveSimulator:
    def __init__(self, cfg: GeneratorConfig, conn: Connection, rng: random.Random) -> None:
        self.cfg, self.conn, self.rng = cfg, conn, rng
        self.tz = ZoneInfo(cfg.timezone)
        today = datetime.now(self.tz).date()
        self.demand = DemandModel(cfg, today - timedelta(days=cfg.history_days), today)
        self.ref = load_reference(conn)
        self.sales = SalesSimulator(cfg, rng, self.demand, self.ref, load_customer_picker(conn))
        self.published_until: date | None = None

    def _poisson(self, per_hour: float, elapsed: timedelta) -> int:
        return int(self.sales.np_rng.poisson(per_hour * elapsed.total_seconds() / 3600))

    # --- The tick -------------------------------------------------------------------

    def tick(self, now: datetime, elapsed: timedelta) -> Counter[str]:
        """Simulate the period (now - elapsed, now]. `now` is naive UTC.

        Does not commit: the caller decides (run_live commits every tick,
        tests roll back).
        """
        done: Counter[str] = Counter()
        with self.conn.cursor() as cur:
            done["trips_published"] = self._publish_trips(cur, now)
            done["trips_completed"], done["tickets_used"] = self._complete_trips(cur, now)
            done["payments_settled"], done["orders_abandoned"] = self._settle_payments(cur, now)
            done["orders"] = self._sell(cur, now, elapsed)
            done["tickets_cancelled"] = self._cancel_tickets(cur, now, elapsed)
            done["refunds_progressed"] = self._progress_refunds(cur, now)
            done["profiles_updated"] = self._update_profiles(cur, elapsed)
        return done

    def _local_today(self, now: datetime) -> date:
        return now.replace(tzinfo=UTC).astimezone(self.tz).date()

    def _publish_trips(self, cur, now: datetime) -> int:
        """Keep the timetable open for sale `booking_horizon_days` ahead."""
        today = self._local_today(now)
        horizon = today + timedelta(days=self.cfg.booking_horizon_days)
        if self.published_until == horizon:
            return 0
        rows = []
        day = today
        while day <= horizon:
            for trip in plan_day(self.cfg, day):
                rows.append(
                    (
                        trip.service_number,
                        self.ref.line_ids[trip.line.code],
                        self.ref.train_ids[trip.train_code],
                        trip.direction,
                        trip.departure_utc,
                        trip.arrival_utc,
                    )
                )
            day += timedelta(days=1)
        # The unique key (service_number, scheduled_departure_at) makes this
        # idempotent: INSERT IGNORE skips the trips that already exist.
        cur.executemany(
            "INSERT IGNORE INTO trips (service_number, line_id, train_id, direction, "
            "scheduled_departure_at, scheduled_arrival_at) VALUES (%s, %s, %s, %s, %s, %s)",
            rows,
        )
        self.published_until = horizon
        return cur.rowcount

    def _complete_trips(self, cur, now: datetime) -> tuple[int, int]:
        cur.execute(
            "UPDATE trips SET trip_status = 'COMPLETED' "
            "WHERE trip_status = 'SCHEDULED' AND scheduled_arrival_at <= %s",
            (now,),
        )
        trips = cur.rowcount
        cur.execute(
            """
            UPDATE tickets t
            JOIN trips p ON p.trip_id = t.trip_id
            JOIN orders o ON o.order_id = t.order_id
            SET t.ticket_status = 'USED'
            WHERE p.trip_status = 'COMPLETED'
              AND p.scheduled_arrival_at > %s - INTERVAL 2 DAY
              AND t.ticket_status = 'ISSUED'
              AND o.order_status = 'CONFIRMED'
            """,
            (now,),
        )
        return trips, cur.rowcount

    def _settle_payments(self, cur, now: datetime) -> tuple[int, int]:
        """Pending payments clear (cards and mobile money in minutes, bank
        transfers in an hour); orders still unpaid after 15 minutes expire."""
        cur.execute(
            """
            UPDATE payments SET payment_status = 'SUCCEEDED', settled_at = %s
            WHERE payment_status = 'PENDING'
              AND attempted_at <= %s - INTERVAL
                  IF(payment_method = 'BANK_TRANSFER', 60, 2) MINUTE
            """,
            (now, now),
        )
        settled = cur.rowcount
        cur.execute(
            """
            UPDATE orders o
            JOIN payments p ON p.order_id = o.order_id AND p.payment_status = 'SUCCEEDED'
            SET o.order_status = 'CONFIRMED'
            WHERE o.order_status = 'PENDING'
            """
        )
        # An order still unpaid 15 minutes after its last failed attempt is abandoned.
        cur.execute(
            """
            SELECT o.order_id FROM orders o
            WHERE o.order_status = 'PENDING' AND o.ordered_at <= %s - INTERVAL 15 MINUTE
              AND NOT EXISTS (
                  SELECT 1 FROM payments p
                  WHERE p.order_id = o.order_id AND p.payment_status <> 'FAILED'
              )
            """,
            (now,),
        )
        abandoned = [row[0] for row in cur.fetchall()]
        for order_id in abandoned:
            cur.execute(
                "UPDATE orders SET order_status = 'CANCELLED' WHERE order_id = %s", (order_id,)
            )
            cur.execute(
                "UPDATE tickets SET ticket_status = 'CANCELLED', cancelled_at = %s "
                "WHERE order_id = %s AND ticket_status = 'ISSUED'",
                (now, order_id),
            )
        return settled, len(abandoned)

    def _upcoming_trips(self, cur, now: datetime) -> list[tuple[int, PlannedTrip]]:
        """Trips on sale: scheduled, departing in 30 minutes or more."""
        cur.execute(
            "SELECT trip_id, service_number, scheduled_departure_at FROM trips "
            "WHERE trip_status = 'SCHEDULED' AND scheduled_departure_at > %s + INTERVAL 30 MINUTE",
            (now,),
        )
        rows = cur.fetchall()
        plans: dict[tuple[str, datetime], PlannedTrip] = {}
        for day in {self._local_today(departure) for _, _, departure in rows}:
            for planned in plan_day(self.cfg, day):
                plans[(planned.service_number, planned.departure_utc)] = planned
        return [
            (trip_id, plans[(number, departure)])
            for trip_id, number, departure in rows
            if (number, departure) in plans
        ]

    def _seats_left(self, cur, trip_id: int, trip: PlannedTrip, request) -> bool:
        """Same segment-by-segment capacity rule as history mode."""
        position = {self.ref.station_ids[code]: i for i, code in enumerate(trip.stops)}
        cur.execute(
            "SELECT origin_station_id, destination_station_id FROM tickets "
            "WHERE trip_id = %s AND fare_class = %s AND ticket_status <> 'CANCELLED'",
            (trip_id, request.fare_class),
        )
        occupied = np.zeros(len(trip.stops) - 1, dtype=np.int32)
        for origin, destination in cur.fetchall():
            occupied[position[origin] : position[destination]] += 1
        seats = trip.seats_first if request.fare_class == "FIRST" else trip.seats_second
        used = occupied[request.origin : request.destination]
        return used.max() + request.seats <= seats

    def _sell(self, cur, now: datetime, elapsed: timedelta) -> int:
        local_now = now.replace(tzinfo=UTC).astimezone(self.tz)
        # More orders at busy hours: weight of the current hour in the daily profile.
        hour_weight = np.mean(
            [np.array(p)[local_now.hour] / np.mean(p) for p in self.cfg.hour_profiles.values()]
        )
        n_orders = self._poisson(self.cfg.live.orders_per_hour * hour_weight, elapsed)
        if n_orders == 0:
            return 0
        upcoming = self._upcoming_trips(cur, now)
        if not upcoming:
            return 0

        sold = 0
        for _ in range(n_orders):
            lead_days = self.demand.sample_lead_days(self.rng)
            target = local_now.date() + timedelta(days=lead_days)
            candidates = [
                (tid, t) for tid, t in upcoming if t.departure_local.date() == target
            ] or upcoming
            trip_id, trip = self.rng.choice(candidates)
            # The order happened at some point during the elapsed period.
            ordered_at = now - elapsed * self.rng.random()
            channel = self.demand.sample_channel(self.rng, local_now.date())
            request = self.sales.request_at(trip, ordered_at, channel, lead_days)
            if not self._seats_left(cur, trip_id, trip, request):
                continue  # sold out

            bundle = self.sales.build_order(trip, trip_id, request, now)
            order_id = self.insert_order(bundle.order)
            for ticket in bundle.tickets:
                ticket.order_id = order_id
                insert_one(self.conn, "tickets", ticket, ("ticket_id", *AUDIT))
            for payment in bundle.payments:
                payment.order_id = order_id
                insert_one(self.conn, "payments", payment, ("payment_id", *AUDIT))
            sold += 1
        return sold

    def insert_order(self, order: OrderRow) -> int:
        """Insert an order; on the (rare) duplicate booking reference, draw a new
        one and retry, as a real application would."""
        for _ in range(5):
            try:
                return insert_one(self.conn, "orders", order, ("order_id", *AUDIT))
            except pymysql.IntegrityError as exc:
                duplicate_reference = exc.args[0] == DUPLICATE_KEY and "uq_orders_reference" in str(
                    exc
                )
                if not duplicate_reference:
                    raise
                order.order_reference = self.sales.order_reference()
        raise RuntimeError("could not find a free booking reference after 5 attempts")

    def _cancel_tickets(self, cur, now: datetime, elapsed: timedelta) -> int:
        """Customers cancel tickets for trips leaving in the coming week."""
        n = self._poisson(self.cfg.live.cancellations_per_hour, elapsed)
        if n == 0:
            return 0
        cur.execute(
            """
            SELECT t.ticket_id, t.fare_type, t.price, p.payment_id
            FROM tickets t
            JOIN trips tr ON tr.trip_id = t.trip_id
            JOIN payments p ON p.order_id = t.order_id AND p.payment_status = 'SUCCEEDED'
            WHERE tr.trip_status = 'SCHEDULED'
              AND tr.scheduled_departure_at BETWEEN %s + INTERVAL 1 HOUR AND %s + INTERVAL 7 DAY
              AND t.ticket_status = 'ISSUED'
            """,
            (now, now),
        )
        candidates = cur.fetchall()
        if not candidates:
            return 0
        picks = self.rng.sample(range(len(candidates)), k=min(n, len(candidates)))
        for index in picks:
            ticket_id, fare_type, price, payment_id = candidates[index]
            cur.execute(
                "UPDATE tickets SET ticket_status = 'CANCELLED', cancelled_at = %s "
                "WHERE ticket_id = %s",
                (now, ticket_id),
            )
            share = self.cfg.lifecycle.refund_shares[fare_type]
            if share > 0:
                cur.execute(
                    "INSERT INTO refunds (ticket_id, payment_id, amount, reason, requested_at) "
                    "VALUES (%s, %s, %s, 'CUSTOMER_REQUEST', %s)",
                    (ticket_id, payment_id, self.sales.money(float(price) * share), now),
                )
        return len(picks)

    def _progress_refunds(self, cur, now: datetime) -> int:
        """REQUESTED -> APPROVED (or REJECTED) -> PAID, after configured delays."""
        live = self.cfg.live
        cur.execute(
            "SELECT refund_id, reason FROM refunds WHERE refund_status = 'REQUESTED' "
            "AND requested_at <= %s - INTERVAL %s MINUTE",
            (now, live.refund_approval_minutes),
        )
        moved = 0
        for refund_id, reason in cur.fetchall():
            rejected = (
                reason != "TRIP_CANCELLED"
                and self.rng.random() < self.cfg.lifecycle.refund_rejection_rate
            )
            if rejected:
                cur.execute(
                    "UPDATE refunds SET refund_status = 'REJECTED', processed_at = %s "
                    "WHERE refund_id = %s",
                    (now, refund_id),
                )
            else:
                cur.execute(
                    "UPDATE refunds SET refund_status = 'APPROVED' WHERE refund_id = %s",
                    (refund_id,),
                )
            moved += 1
        cur.execute(
            "UPDATE refunds SET refund_status = 'PAID', processed_at = %s "
            "WHERE refund_status = 'APPROVED' AND updated_at <= %s - INTERVAL %s MINUTE",
            (now, now, live.refund_payment_minutes),
        )
        return moved + cur.rowcount

    def _update_profiles(self, cur, elapsed: timedelta) -> int:
        n = self._poisson(self.cfg.live.profile_updates_per_hour, elapsed)
        for _ in range(n):
            customer_id = self.sales.customers.random_id(self.rng)
            if self.rng.random() < 0.5:
                cur.execute(
                    "UPDATE customers SET loyalty_tier = %s WHERE customer_id = %s",
                    (self.rng.choice(["SILVER", "GOLD"]), customer_id),
                )
            else:
                cur.execute(
                    "UPDATE customers SET phone = %s WHERE customer_id = %s",
                    (f"+999 {self.rng.randrange(10**8):08d}", customer_id),
                )
        return n


def run_live(cfg: GeneratorConfig, conn: Connection, max_ticks: int | None = None) -> None:
    """Run ticks forever (or `max_ticks` times), every `live.tick_seconds`."""
    simulator = LiveSimulator(cfg, conn, random.Random())
    interval = timedelta(seconds=cfg.live.tick_seconds)
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        started = time.monotonic()
        now = datetime.now(UTC).replace(tzinfo=None)
        done = simulator.tick(now, interval)
        conn.commit()  # one transaction per tick: all or nothing
        log.info("tick: %s", ", ".join(f"{k}={v}" for k, v in done.items() if v))
        ticks += 1
        if max_ticks is None or ticks < max_ticks:
            time.sleep(max(0.0, cfg.live.tick_seconds - (time.monotonic() - started)))
