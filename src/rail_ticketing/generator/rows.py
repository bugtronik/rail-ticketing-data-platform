"""One dataclass per source table: the rows the generator produces.

Field names and order match the MariaDB columns exactly, so the database
writer can build `INSERT INTO <table> (<fields>)` statements from them.
All datetimes are naive UTC, like the database (default-time-zone = +00:00).

`slots=True` makes each instance smaller in memory: a 2-year history creates
millions of them, one day at a time.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(slots=True)
class StationRow:
    station_id: int
    station_code: str
    station_name: str
    city: str
    region: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RailLineRow:
    line_id: int
    line_code: str
    line_name: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class LineStopRow:
    line_stop_id: int
    line_id: int
    station_id: int
    stop_sequence: int
    km_from_origin: Decimal
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class TrainRow:
    train_id: int
    train_code: str
    train_model: str
    seats_first: int
    seats_second: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class SalesChannelRow:
    channel_id: int
    channel_code: str
    channel_name: str
    is_online: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class CustomerRow:
    customer_id: int
    email: str
    first_name: str
    last_name: str
    phone: str
    birth_date: date
    city: str
    loyalty_tier: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class TripRow:
    trip_id: int
    service_number: str
    line_id: int
    train_id: int
    direction: str
    scheduled_departure_at: datetime
    scheduled_arrival_at: datetime
    trip_status: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class OrderRow:
    order_id: int | None
    order_reference: str
    customer_id: int | None
    channel_id: int
    ordered_at: datetime
    order_status: str
    total_amount: Decimal
    currency: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class TicketRow:
    ticket_id: int | None
    order_id: int | None
    trip_id: int
    origin_station_id: int
    destination_station_id: int
    fare_class: str
    fare_type: str
    passenger_type: str
    price: Decimal
    ticket_status: str
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class PaymentRow:
    payment_id: int | None
    order_id: int | None
    payment_method: str
    amount: Decimal
    payment_status: str
    provider_reference: str | None
    attempted_at: datetime
    settled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RefundRow:
    refund_id: int | None
    ticket_id: int | None
    payment_id: int | None
    amount: Decimal
    reason: str
    refund_status: str
    requested_at: datetime
    processed_at: datetime | None
    created_at: datetime
    updated_at: datetime
