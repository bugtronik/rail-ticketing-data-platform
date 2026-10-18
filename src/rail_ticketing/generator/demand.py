"""How many people want to travel, when they buy, and through which channel.

Pure functions of the configuration and a random generator: no database here,
which is what makes this part unit-testable.
"""

import bisect
import math
import random
from datetime import date

from rail_ticketing.generator.config import Channel, GeneratorConfig
from rail_ticketing.generator.network import PlannedTrip


class Categorical:
    """Draw values according to fixed weights.

    `numpy.random.Generator.choice(p=...)` re-validates the weights at every
    call; with millions of draws, precomputing the cumulative distribution
    once and using a binary search (bisect) is several times faster.
    """

    def __init__(self, weights: dict) -> None:
        total = float(sum(weights.values()))
        self.values = list(weights)
        self.cumulative: list[float] = []
        running = 0.0
        for weight in weights.values():
            running += weight / total
            self.cumulative.append(running)
        self.cumulative[-1] = 1.0  # guard against floating-point rounding

    def sample(self, rng: random.Random):
        return self.values[bisect.bisect_right(self.cumulative, rng.random())]


class DemandModel:
    """Precomputed distributions, built once from the configuration."""

    def __init__(self, cfg: GeneratorConfig, history_start: date, history_end: date) -> None:
        self.cfg = cfg
        self.history_start = history_start
        self.history_days = max((history_end - history_start).days, 1)
        self.hours = {
            name: Categorical(dict(enumerate(profile)))
            for name, profile in cfg.hour_profiles.items()
        }
        self.group_sizes = Categorical(cfg.demand.group_sizes)
        self.passenger_types = Categorical(cfg.demand.passenger_types)
        self.fare_types = Categorical(cfg.pricing.fare_type_shares)
        self.payment_methods = {c.code: Categorical(c.payment_methods) for c in cfg.channels}
        self._channels = {c.code: c for c in cfg.channels}
        self._channels_by_day: dict[date, Categorical] = {}
        self._pairs: dict[tuple[str, ...], Categorical] = {}

    # --- How many seats are requested on a trip -------------------------------

    def seasonality(self, day: date) -> float:
        """Multiplier for a departure date: month x weekday x special day x growth."""
        d = self.cfg.demand
        years = (day - self.history_start).days / 365.25
        return (
            d.month_factors[day.month - 1]
            * d.weekday_factors[day.isoweekday() - 1]
            * d.special_days.get(day.strftime("%m-%d"), 1.0)
            * (1 + d.annual_growth) ** years
        )

    def expected_seat_requests(self, trip: PlannedTrip) -> float:
        return trip.line.base_demand * self.seasonality(trip.departure_local.date())

    # --- Who buys, where and when ---------------------------------------------

    def channel_shares(self, day: date) -> dict[str, float]:
        """Market share of each channel on a date: linear move from start to end."""
        progress = min(max((day - self.history_start).days / self.history_days, 0.0), 1.0)
        return {
            c.code: c.share_start + (c.share_end - c.share_start) * progress
            for c in self.cfg.channels
        }

    def sample_channel(self, rng: random.Random, day: date) -> Channel:
        if day not in self._channels_by_day:  # one distribution per day, built once
            self._channels_by_day[day] = Categorical(self.channel_shares(day))
        code = self._channels_by_day[day].sample(rng)
        return self._channels[code]

    def sample_lead_days(self, rng: random.Random) -> int:
        """Days between purchase and departure."""
        d = self.cfg.demand
        if rng.random() < d.last_minute_share:
            return rng.randrange(2)  # on the day, or the day before
        lead = 2 + math.floor(rng.expovariate(1 / (d.lead_time_mean_days - 2)))
        return min(lead, d.max_lead_days)

    def sample_hour(self, rng: random.Random, channel: Channel) -> int:
        return self.hours[channel.hour_profile].sample(rng)

    def sample_journey(self, rng: random.Random, trip: PlannedTrip) -> tuple[int, int]:
        """Origin and destination, as indexes in the trip's stop list (origin < destination).

        A pair is chosen with probability proportional to weight(origin) x
        weight(destination): big stations exchange the most passengers.
        """
        if trip.stops not in self._pairs:
            weights = [self.cfg.station(code).weight for code in trip.stops]
            n = len(weights)
            self._pairs[trip.stops] = Categorical(
                {(i, j): weights[i] * weights[j] for i in range(n) for j in range(i + 1, n)}
            )
        return self._pairs[trip.stops].sample(rng)
