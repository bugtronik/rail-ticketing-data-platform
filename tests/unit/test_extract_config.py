"""Extractor configuration: validation and the time window."""

from datetime import time
from pathlib import Path

import pytest

from rail_ticketing.extract.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    Window,
    load_extractor_config,
)


def test_default_configuration_lists_the_eleven_source_tables() -> None:
    cfg = load_extractor_config()
    assert len(cfg.tables) == 11
    assert cfg.table("tickets").key == "ticket_id"


@pytest.mark.parametrize(
    ("original", "broken", "message"),
    [
        ('key = "ticket_id"', 'key = "ticket_id; DROP TABLE x"', "invalid identifier"),
        ('name = "refunds"', 'name = "tickets"', "duplicate"),
        ("batch_size = 10000", "batch_size = 0", "at least 1"),
    ],
    ids=["sql-in-identifier", "duplicate-table", "empty-batch"],
)
def test_invalid_configuration_is_rejected(
    tmp_path: Path, original: str, broken: str, message: str
) -> None:
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    assert original in text
    path = tmp_path / "extractor.toml"
    path.write_text(text.replace(original, broken, 1), encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_extractor_config(path)


@pytest.mark.parametrize(
    ("start", "end", "allowed", "refused"),
    [
        ("00:00", "00:00", ["00:00", "12:00", "23:59"], []),  # always
        ("08:00", "18:00", ["08:00", "17:59"], ["07:59", "18:00", "03:00"]),
        ("21:00", "06:00", ["21:00", "23:59", "00:00", "05:59"], ["06:00", "12:00", "20:59"]),
    ],
    ids=["always", "daytime", "night-across-midnight"],
)
def test_time_window(start: str, end: str, allowed: list[str], refused: list[str]) -> None:
    window = Window.parse(start, end)
    assert all(window.allows(time.fromisoformat(t)) for t in allowed)
    assert not any(window.allows(time.fromisoformat(t)) for t in refused)
