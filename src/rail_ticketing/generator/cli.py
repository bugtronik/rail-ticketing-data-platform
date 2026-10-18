"""Command line: `rail-generator history` and `rail-generator live`.

argparse (standard library) is enough for two sub-commands: no extra
dependency such as click or typer.
"""

import argparse
import logging
import sys
from pathlib import Path

from rail_ticketing.generator import db
from rail_ticketing.generator.config import DEFAULT_CONFIG_PATH, load_config
from rail_ticketing.generator.history import generate_history
from rail_ticketing.generator.live import run_live

log = logging.getLogger("rail_ticketing.generator")


def _history(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = db.connect()
    if not db.source_is_empty(conn):
        # History mode assigns ids itself (1, 2, 3...): it needs an empty source.
        # The app account cannot DELETE, so cleaning is done by recreating the volume.
        log.error("The source database already contains data. Run `make reset && make up`.")
        return 1
    stats = generate_history(cfg, db.MariaDBWriter(conn), days=args.days)
    total = sum(stats.rows.values())
    for table, count in stats.rows.items():
        log.info("%-15s %10d rows", table, count)
    log.info(
        "%d rows in %.0f s (%.0f rows/s). Seats requested: %d, lost (sold out): %d (%.1f %%)",
        total,
        stats.seconds,
        total / stats.seconds,
        stats.seats_requested,
        stats.seats_lost,
        100 * stats.seats_lost / max(stats.seats_requested, 1),
    )
    return 0


def _live(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    conn = db.connect()
    if db.source_is_empty(conn):
        log.error("The source database is empty. Run `make seed` first.")
        return 1
    try:
        run_live(cfg, conn, max_ticks=args.ticks)
    except KeyboardInterrupt:
        log.info("Stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rail-generator",
        description="Synthetic data generator for the rail ticketing source database.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    commands = parser.add_subparsers(dest="command", required=True)

    history = commands.add_parser("history", help="fill an empty source with the history")
    history.add_argument("--days", type=int, help="override history_days (shorter runs)")
    history.set_defaults(run=_history)

    live = commands.add_parser("live", help="simulate the application in real time")
    live.add_argument("--ticks", type=int, help="stop after N ticks (default: run forever)")
    live.set_defaults(run=_live)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
