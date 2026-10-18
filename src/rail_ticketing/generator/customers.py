"""Synthetic customer accounts, and how orders are attributed to them.

Personal data is fake but must stay *safely* fake:
  * emails use example.com, a domain reserved for documentation (RFC 2606):
    no real mailbox can ever receive anything;
  * phone numbers use +999, a country code the ITU has never assigned.
"""

import bisect
import random
import unicodedata
from datetime import datetime, timedelta

from faker import Faker

from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.demand import Categorical
from rail_ticketing.generator.rows import CustomerRow


def _ascii_slug(text: str) -> str:
    """'Hélène Dupré' -> 'helene-dupre' (emails must be plain ASCII)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "-".join(ascii_text.lower().split())


def _random_moment(rng: random.Random, start: datetime, end: datetime) -> datetime:
    seconds = (end - start).total_seconds()
    return start + timedelta(seconds=rng.uniform(0, seconds))


def build_customers(
    cfg: GeneratorConfig, rng: random.Random, start: datetime, now: datetime
) -> list[CustomerRow]:
    """Customer accounts, with ids in creation order (as AUTO_INCREMENT would give)."""
    faker = Faker("fr_FR")
    faker.seed_instance(cfg.seed)  # names are reproducible too
    tiers = Categorical(cfg.customers.loyalty_tiers)
    cities = Categorical({s.name: s.weight for s in cfg.stations})
    oldest = start - timedelta(days=3 * 365)

    created = sorted(
        _random_moment(rng, oldest, start)
        if rng.random() < cfg.customers.existing_share
        else _random_moment(rng, start, now)
        for _ in range(cfg.customers.count)
    )

    customers: list[CustomerRow] = []
    for customer_id, created_at in enumerate(created, start=1):
        first, last = faker.first_name(), faker.last_name()
        updated_at = created_at
        if rng.random() < cfg.customers.profile_update_share:
            updated_at = _random_moment(rng, created_at, now)
        age_days = rng.randrange(18 * 365, 80 * 365)
        customers.append(
            CustomerRow(
                customer_id=customer_id,
                email=f"{_ascii_slug(first)}.{_ascii_slug(last)}.{customer_id}@example.com",
                first_name=first,
                last_name=last,
                phone=f"+999 {rng.randrange(10**8):08d}",
                birth_date=start.date() - timedelta(days=age_days),
                city=cities.sample(rng),
                loyalty_tier=tiers.sample(rng),
                created_at=created_at,
                updated_at=updated_at,
            )
        )
    return customers


class CustomerPicker:
    """Choose which existing customer places an order at a given time."""

    def __init__(self, customers: list[CustomerRow]) -> None:
        # Sorted by creation: customers[:k] are the accounts that exist at time t.
        self.created_at = [c.created_at for c in customers]
        self.ids = [c.customer_id for c in customers]

    def pick(self, rng: random.Random, at: datetime) -> int | None:
        existing = bisect.bisect_right(self.created_at, at)
        if existing == 0:
            return None
        # u**2 favours older accounts: loyal customers travel more often.
        return self.ids[int(existing * rng.random() ** 2)]

    def random_id(self, rng: random.Random) -> int:
        """Any existing customer, uniformly (live mode profile edits)."""
        return self.ids[rng.randrange(len(self.ids))]
