"""Fixtures for the generator unit tests: no database, everything in memory."""

from datetime import datetime

import pytest

from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.history import MemoryWriter, generate_history


@pytest.fixture(scope="session")
def history(config: GeneratorConfig, now: datetime) -> MemoryWriter:
    """45 days of history, generated once and shared by the read-only tests."""
    writer = MemoryWriter()
    generate_history(config, writer, now=now, days=45)
    return writer
