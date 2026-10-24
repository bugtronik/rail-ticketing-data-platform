"""Load and validate the extractor configuration (config/extractor.toml)."""

import re
import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config/extractor.toml")

# Table and column names end up inside SQL statements (they cannot be bound
# parameters), so they are restricted to plain identifiers.
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class ConfigError(ValueError):
    """The configuration file is invalid."""


@dataclass(frozen=True)
class TableSpec:
    name: str
    key: str  # single-column primary key (a contract of the source, ADR 0003)


@dataclass(frozen=True)
class Window:
    """Allowed extraction hours, in minutes since local midnight."""

    start: int
    end: int

    @classmethod
    def parse(cls, start: str, end: str) -> "Window":
        def minutes(text: str) -> int:
            t = time.fromisoformat(text)
            return t.hour * 60 + t.minute

        return cls(minutes(start), minutes(end))

    def allows(self, moment: time) -> bool:
        m = moment.hour * 60 + moment.minute
        if self.start == self.end:  # no restriction
            return True
        if self.start < self.end:  # e.g. 08:00 -> 18:00
            return self.start <= m < self.end
        return m >= self.start or m < self.end  # crosses midnight, e.g. 21:00 -> 06:00


@dataclass(frozen=True)
class ExtractorConfig:
    batch_size: int
    pause_seconds: float
    overlap_minutes: int
    timezone: str
    window: Window
    tables: tuple[TableSpec, ...]

    def table(self, name: str) -> TableSpec:
        for spec in self.tables:
            if spec.name == name:
                return spec
        raise ConfigError(f"unknown table: {name}")


def load_extractor_config(path: Path = DEFAULT_CONFIG_PATH) -> ExtractorConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    try:
        cfg = ExtractorConfig(
            batch_size=raw["batch_size"],
            pause_seconds=raw["pause_seconds"],
            overlap_minutes=raw["overlap_minutes"],
            timezone=raw["timezone"],
            window=Window.parse(raw["window"]["start"], raw["window"]["end"]),
            tables=tuple(TableSpec(**t) for t in raw["tables"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"{path}: missing or malformed entry: {exc}") from exc

    if cfg.batch_size < 1:
        raise ConfigError("batch_size must be at least 1")
    if cfg.overlap_minutes < 0 or cfg.pause_seconds < 0:
        raise ConfigError("overlap_minutes and pause_seconds cannot be negative")
    names = [t.name for t in cfg.tables]
    if len(set(names)) != len(names):
        raise ConfigError("tables: duplicate table name")
    for spec in cfg.tables:
        for identifier in (spec.name, spec.key):
            if not IDENTIFIER.match(identifier):
                raise ConfigError(f"tables: invalid identifier {identifier!r}")
    return cfg
