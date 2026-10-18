"""Load and validate the generator configuration (config/generator.toml).

The TOML file is parsed with `tomllib` (Python standard library) into frozen
dataclasses. Validation happens once, at load time: a typo in the config
fails immediately with a clear message, not halfway through a 2-year run.
"""

import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config/generator.toml")


class ConfigError(ValueError):
    """The configuration file is invalid."""


@dataclass(frozen=True)
class Station:
    code: str
    name: str
    region: str
    km: float
    weight: float


@dataclass(frozen=True)
class Line:
    code: str
    name: str
    stops: tuple[str, ...]
    travel_hours: float
    base_demand: float
    first_class_share: float
    fleet: tuple[str, ...]


@dataclass(frozen=True)
class Service:
    line: str
    numbers: tuple[str, str]  # (outbound, return)
    departure: time
    weekdays: frozenset[int]  # 1 = Monday ... 7 = Sunday
    months: frozenset[int]  # empty = every month


@dataclass(frozen=True)
class Train:
    code: str
    model: str
    seats_first: int
    seats_second: int


@dataclass(frozen=True)
class Channel:
    code: str
    name: str
    is_online: bool
    share_start: float
    share_end: float
    identified_share: float
    hour_profile: str
    payment_methods: dict[str, float]


@dataclass(frozen=True)
class Demand:
    annual_growth: float
    month_factors: tuple[float, ...]
    weekday_factors: tuple[float, ...]
    special_days: dict[str, float]
    last_minute_share: float
    lead_time_mean_days: float
    max_lead_days: int
    group_sizes: dict[int, float]
    passenger_types: dict[str, float]


@dataclass(frozen=True)
class Pricing:
    second_class_per_km: float
    minimum_fare: int
    rounding: int
    first_class_multiplier: float
    fare_type_shares: dict[str, float]
    fare_type_multipliers: dict[str, float]
    saver_min_lead_days: int
    passenger_discounts: dict[str, float]


@dataclass(frozen=True)
class Lifecycle:
    cancellation_rates: dict[str, float]
    refund_shares: dict[str, float]
    trip_cancellation_rate: float
    disruption_rate: float
    disruption_claim_rate: float
    disruption_refund_share: float
    refund_rejection_rate: float
    payment_failure_rate: float
    payment_retry_success_rate: float


@dataclass(frozen=True)
class Customers:
    count: int
    existing_share: float
    profile_update_share: float
    loyalty_tiers: dict[str, float]


@dataclass(frozen=True)
class Live:
    tick_seconds: int
    orders_per_hour: float
    cancellations_per_hour: float
    profile_updates_per_hour: float
    refund_approval_minutes: int
    refund_payment_minutes: int


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int
    currency: str
    timezone: str
    history_days: int
    booking_horizon_days: int
    stations: tuple[Station, ...]
    lines: tuple[Line, ...]
    services: tuple[Service, ...]
    trains: tuple[Train, ...]
    channels: tuple[Channel, ...]
    hour_profiles: dict[str, tuple[float, ...]]
    demand: Demand
    pricing: Pricing
    lifecycle: Lifecycle
    customers: Customers
    live: Live

    def station(self, code: str) -> Station:
        return next(s for s in self.stations if s.code == code)

    def line(self, code: str) -> Line:
        return next(line for line in self.lines if line.code == code)


# --- Loading ------------------------------------------------------------------


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> GeneratorConfig:
    """Read, convert and validate the configuration file."""
    with path.open("rb") as f:
        raw = tomllib.load(f)
    try:
        config = _build(raw)
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"{path}: missing or malformed entry: {exc}") from exc
    _validate(config)
    return config


def _build(raw: dict[str, Any]) -> GeneratorConfig:
    return GeneratorConfig(
        seed=raw["seed"],
        currency=raw["currency"],
        timezone=raw["timezone"],
        history_days=raw["history_days"],
        booking_horizon_days=raw["booking_horizon_days"],
        stations=tuple(Station(**s) for s in raw["stations"]),
        lines=tuple(
            Line(
                **(
                    line
                    | {"stops": tuple(line["stops"]), "fleet": tuple(line["fleet"])}
                )
            )
            for line in raw["lines"]
        ),
        services=tuple(
            Service(
                line=s["line"],
                numbers=(s["numbers"][0], s["numbers"][1]),
                departure=time.fromisoformat(s["departure"]),
                weekdays=frozenset(s["weekdays"]),
                months=frozenset(s["months"]),
            )
            for s in raw["services"]
        ),
        trains=tuple(Train(**t) for t in raw["trains"]),
        channels=tuple(Channel(**c) for c in raw["channels"]),
        hour_profiles={k: tuple(v) for k, v in raw["hour_profiles"].items()},
        demand=Demand(
            **(
                raw["demand"]
                | {
                    "month_factors": tuple(raw["demand"]["month_factors"]),
                    "weekday_factors": tuple(raw["demand"]["weekday_factors"]),
                    "group_sizes": {
                        int(k): v for k, v in raw["demand"]["group_sizes"].items()
                    },
                }
            )
        ),
        pricing=Pricing(**raw["pricing"]),
        lifecycle=Lifecycle(**raw["lifecycle"]),
        customers=Customers(**raw["customers"]),
        live=Live(**raw["live"]),
    )


# --- Validation ---------------------------------------------------------------


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _check_distribution(name: str, weights: dict[Any, float]) -> None:
    total = sum(weights.values())
    _check(abs(total - 1.0) < 1e-6, f"{name}: shares must sum to 1 (got {total:.4f})")


def _validate(cfg: GeneratorConfig) -> None:
    station_codes = {s.code for s in cfg.stations}
    train_codes = {t.code for t in cfg.trains}
    line_codes = {line.code for line in cfg.lines}

    _check(len(station_codes) == len(cfg.stations), "stations: duplicate code")
    _check(
        all(len(code) == 5 for code in station_codes), "stations: codes must be 5 chars"
    )
    for line in cfg.lines:
        _check(set(line.stops) <= station_codes, f"line {line.code}: unknown station")
        kms = [cfg.station(code).km for code in line.stops]
        _check(kms == sorted(kms), f"line {line.code}: stops must be ordered by km")
        _check(
            set(line.fleet) <= train_codes, f"line {line.code}: unknown train in fleet"
        )
    for service in cfg.services:
        _check(service.line in line_codes, f"service {service.numbers}: unknown line")

    _check_distribution(
        "channels share_start", {c.code: c.share_start for c in cfg.channels}
    )
    _check_distribution(
        "channels share_end", {c.code: c.share_end for c in cfg.channels}
    )
    for channel in cfg.channels:
        _check(
            channel.hour_profile in cfg.hour_profiles,
            f"{channel.code}: unknown profile",
        )
        _check_distribution(f"{channel.code} payment_methods", channel.payment_methods)
    for name, profile in cfg.hour_profiles.items():
        _check(len(profile) == 24, f"hour profile {name}: needs 24 values")

    _check(len(cfg.demand.month_factors) == 12, "demand.month_factors: needs 12 values")
    _check(
        len(cfg.demand.weekday_factors) == 7, "demand.weekday_factors: needs 7 values"
    )
    _check_distribution("demand.group_sizes", cfg.demand.group_sizes)
    _check_distribution("demand.passenger_types", cfg.demand.passenger_types)
    _check_distribution("pricing.fare_type_shares", cfg.pricing.fare_type_shares)
    _check_distribution("customers.loyalty_tiers", cfg.customers.loyalty_tiers)
