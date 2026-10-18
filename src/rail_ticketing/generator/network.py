"""Reference data (stations, lines, stops, trains, channels) and the timetable."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from rail_ticketing.generator.config import GeneratorConfig, Line
from rail_ticketing.generator.rows import (
    LineStopRow,
    RailLineRow,
    SalesChannelRow,
    StationRow,
    TrainRow,
)


def to_utc(local: datetime) -> datetime:
    """Convert an aware local datetime to the naive UTC the database stores."""
    return local.astimezone(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class ReferenceData:
    """Reference rows plus code -> id lookups used by the rest of the generator."""

    stations: list[StationRow]
    lines: list[RailLineRow]
    line_stops: list[LineStopRow]
    trains: list[TrainRow]
    channels: list[SalesChannelRow]
    station_ids: dict[str, int]
    line_ids: dict[str, int]
    train_ids: dict[str, int]
    channel_ids: dict[str, int]


def build_reference_data(cfg: GeneratorConfig, created_at: datetime) -> ReferenceData:
    """Reference rows, with ids assigned in configuration order (1, 2, 3...)."""
    stations = [
        StationRow(i, s.code, s.name, s.name, s.region, created_at, created_at)
        for i, s in enumerate(cfg.stations, start=1)
    ]
    station_ids = {row.station_code: row.station_id for row in stations}

    lines = [
        RailLineRow(i, line.code, line.name, created_at, created_at)
        for i, line in enumerate(cfg.lines, start=1)
    ]
    line_ids = {row.line_code: row.line_id for row in lines}

    line_stops: list[LineStopRow] = []
    for line in cfg.lines:
        origin_km = cfg.station(line.stops[0]).km
        for sequence, code in enumerate(line.stops, start=1):
            line_stops.append(
                LineStopRow(
                    line_stop_id=len(line_stops) + 1,
                    line_id=line_ids[line.code],
                    station_id=station_ids[code],
                    stop_sequence=sequence,
                    km_from_origin=Decimal(str(cfg.station(code).km - origin_km)),
                    created_at=created_at,
                    updated_at=created_at,
                )
            )

    trains = [
        TrainRow(
            i, t.code, t.model, t.seats_first, t.seats_second, created_at, created_at
        )
        for i, t in enumerate(cfg.trains, start=1)
    ]
    channels = [
        SalesChannelRow(i, c.code, c.name, c.is_online, created_at, created_at)
        for i, c in enumerate(cfg.channels, start=1)
    ]
    return ReferenceData(
        stations=stations,
        lines=lines,
        line_stops=line_stops,
        trains=trains,
        channels=channels,
        station_ids=station_ids,
        line_ids=line_ids,
        train_ids={row.train_code: row.train_id for row in trains},
        channel_ids={row.channel_code: row.channel_id for row in channels},
    )


@dataclass(frozen=True)
class PlannedTrip:
    """A trip of the timetable, with what the sales simulation needs to know."""

    service_number: str
    line: Line
    direction: str  # OUTBOUND (from the coast) or RETURN
    train_code: str
    departure_local: datetime  # aware, network time zone
    arrival_local: datetime
    stops: tuple[str, ...]  # station codes, in travel order
    stop_km: tuple[float, ...]  # distance travelled at each stop, from 0
    seats_first: int
    seats_second: int

    @property
    def departure_utc(self) -> datetime:
        return to_utc(self.departure_local)

    @property
    def arrival_utc(self) -> datetime:
        return to_utc(self.arrival_local)


def plan_day(cfg: GeneratorConfig, day: date) -> list[PlannedTrip]:
    """Every trip departing on `day` (local date), in timetable order."""
    tz = ZoneInfo(cfg.timezone)
    trains = {t.code: t for t in cfg.trains}
    trips: list[PlannedTrip] = []
    trips_per_line: dict[str, int] = {}

    for service in cfg.services:
        if day.isoweekday() not in service.weekdays:
            continue
        if service.months and day.month not in service.months:
            continue
        line = cfg.line(service.line)
        for direction, number in zip(
            ("OUTBOUND", "RETURN"), service.numbers, strict=True
        ):
            stops = (
                line.stops if direction == "OUTBOUND" else tuple(reversed(line.stops))
            )
            kms = [cfg.station(code).km for code in stops]
            # Rotate the fleet day after day, so every train unit is used.
            k = trips_per_line.get(line.code, 0)
            trips_per_line[line.code] = k + 1
            train = trains[line.fleet[(day.toordinal() + k) % len(line.fleet)]]
            departure = datetime.combine(day, service.departure, tzinfo=tz)
            trips.append(
                PlannedTrip(
                    service_number=number,
                    line=line,
                    direction=direction,
                    train_code=train.code,
                    departure_local=departure,
                    arrival_local=departure + timedelta(hours=line.travel_hours),
                    stops=stops,
                    stop_km=tuple(abs(km - kms[0]) for km in kms),
                    seats_first=train.seats_first,
                    seats_second=train.seats_second,
                )
            )
    return trips
