"""The configuration is validated at load time, with a clear error message."""

from pathlib import Path

import pytest

from rail_ticketing.generator.config import DEFAULT_CONFIG_PATH, ConfigError, load_config


def test_default_configuration_is_valid() -> None:
    cfg = load_config()
    assert cfg.currency == "XAF"
    assert len(cfg.stations) == 19


@pytest.mark.parametrize(
    ("original", "broken", "message"),
    [
        ("CASH = 0.60, MOBILE_MONEY = 0.30", "CASH = 0.70, MOBILE_MONEY = 0.30", "sum to 1"),
        ('stops = ["POCEA", "PESTU"', 'stops = ["POCEA", "XXXXX"', "unknown station"),
        ('departure = "07:00"', 'departure = "7h00"', "Invalid isoformat"),
    ],
    ids=["shares-not-summing-to-1", "unknown-station", "bad-time"],
)
def test_invalid_configuration_is_rejected(
    tmp_path: Path, original: str, broken: str, message: str
) -> None:
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    assert original in text, "the test must really change something"
    path = tmp_path / "generator.toml"
    path.write_text(text.replace(original, broken, 1), encoding="utf-8")
    with pytest.raises((ConfigError, ValueError), match=message):
        load_config(path)
