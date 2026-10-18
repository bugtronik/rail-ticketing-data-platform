"""The demand model: seasonality, channels, lead times, journeys."""

import random
from datetime import date, timedelta

import numpy as np
import pytest

from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.demand import DemandModel
from rail_ticketing.generator.network import plan_day

START, END = date(2025, 1, 1), date(2027, 1, 1)


@pytest.fixture
def demand(config: GeneratorConfig) -> DemandModel:
    return DemandModel(config, START, END)


def test_holiday_months_have_more_demand_than_february(demand: DemandModel) -> None:
    # Same weekday (Tuesday), same year: only the month differs.
    assert demand.seasonality(date(2025, 8, 5)) > 1.5 * demand.seasonality(
        date(2025, 2, 4)
    )
    assert demand.seasonality(date(2025, 12, 2)) > demand.seasonality(date(2025, 2, 4))


def test_fridays_and_sundays_are_busier_than_midweek(demand: DemandModel) -> None:
    tuesday, friday, sunday = date(2025, 3, 4), date(2025, 3, 7), date(2025, 3, 9)
    assert demand.seasonality(friday) > demand.seasonality(tuesday)
    assert demand.seasonality(sunday) > demand.seasonality(tuesday)


def test_demand_grows_year_on_year(demand: DemandModel) -> None:
    assert demand.seasonality(date(2026, 3, 4)) > demand.seasonality(date(2025, 3, 5))


def test_sales_move_online_over_time(demand: DemandModel) -> None:
    def online_share(day: date) -> float:
        shares = demand.channel_shares(day)
        return shares["WEB"] + shares["MOBILE_APP"]

    assert online_share(START) == pytest.approx(0.25)
    assert online_share(END) == pytest.approx(0.45)
    assert sum(
        demand.channel_shares(START + timedelta(days=200)).values()
    ) == pytest.approx(1)


def test_lead_times_follow_the_configuration(
    config: GeneratorConfig, demand: DemandModel
) -> None:
    rng = random.Random(1)
    leads = np.array([demand.sample_lead_days(rng) for _ in range(20_000)])
    assert leads.min() >= 0
    assert leads.max() <= config.demand.max_lead_days
    assert (leads <= 1).mean() == pytest.approx(
        config.demand.last_minute_share, abs=0.02
    )


def test_journeys_go_forward_along_the_line(demand: DemandModel) -> None:
    rng = random.Random(2)
    for trip in plan_day(demand.cfg, date(2025, 3, 4)):
        for _ in range(500):
            origin, destination = demand.sample_journey(rng, trip)
            assert 0 <= origin < destination < len(trip.stops)


def test_big_stations_exchange_more_passengers(demand: DemandModel) -> None:
    rng = random.Random(3)
    omnibus = next(
        t for t in plan_day(demand.cfg, date(2025, 3, 4)) if t.line.code == "OMN"
    )
    ends = [demand.sample_journey(rng, omnibus) for _ in range(20_000)]
    terminus_to_terminus = sum(
        1 for o, d in ends if o == 0 and d == len(omnibus.stops) - 1
    )
    small_pair = sum(1 for o, d in ends if (o, d) == (4, 6))  # two weight-1 stations
    assert terminus_to_terminus > 10 * small_pair
