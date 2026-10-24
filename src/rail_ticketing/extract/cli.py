"""Command line: `rail-extract run` and `rail-extract status`."""

import argparse
import logging
import sys
from pathlib import Path

from rail_ticketing.extract.config import DEFAULT_CONFIG_PATH, load_extractor_config
from rail_ticketing.extract.connections import connect_source, connect_warehouse
from rail_ticketing.extract.extractor import RunResult, TableExtractor

log = logging.getLogger("rail_ticketing.extract")


def _run(args: argparse.Namespace) -> int:
    cfg = load_extractor_config(args.config)
    specs = [cfg.table(name) for name in args.tables] if args.tables else list(cfg.tables)
    source, warehouse = connect_source(), connect_warehouse()
    results: list[RunResult] = []
    try:
        for spec in specs:
            try:
                result = TableExtractor(cfg, spec, source, warehouse).run(full=args.full)
            except Exception:
                # Logged in meta.extract_runs too; the other tables still run.
                log.exception("%s: extraction failed", spec.name)
                results.append(RunResult(spec.name, "failed"))
                continue
            results.append(result)
            log.info(
                "%-15s %-8s read=%-9d inserted=%-9d updated=%-7d batches=%-4d "
                "source=%.1fs total=%.1fs",
                result.table,
                result.status,
                result.rows_read,
                result.rows_inserted,
                result.rows_updated,
                result.batches,
                result.source_seconds,
                result.seconds,
            )
    finally:
        source.close()
        warehouse.close()

    read = sum(r.rows_read for r in results)
    seconds = sum(r.seconds for r in results)
    log.info("%d rows read in %.1f s (%.0f rows/s)", read, seconds, read / max(seconds, 1e-9))
    return 1 if any(r.status == "failed" for r in results) else 0


def _status(args: argparse.Namespace) -> int:
    """Per table: the last change seen, and how fresh the raw copy is.

    Two different things: a table that rarely changes (stations) has an old
    watermark but is perfectly up to date. Freshness is the time since the
    last successful run: raw is complete up to that moment.
    """
    with connect_warehouse() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.table_name, s.watermark_updated_at, ok.finished_at,
                   date_trunc('second', NOW() - ok.finished_at)
            FROM meta.extract_state s
            LEFT JOIN LATERAL (
                SELECT finished_at FROM meta.extract_runs
                WHERE table_name = s.table_name AND status = 'success'
                ORDER BY run_id DESC LIMIT 1
            ) ok ON TRUE
            ORDER BY s.table_name
            """
        )
        rows = cur.fetchall()
    print(f"{'table':15} {'last change seen (UTC)':28} {'last successful run':21} {'age':>9}")
    for table, watermark, finished, age in rows:
        when = finished.strftime("%Y-%m-%d %H:%M:%S") if finished else "never"
        print(f"{table:15} {watermark!s:28} {when:21} {age!s:>9}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rail-extract",
        description="Incremental extraction from the MariaDB source to the warehouse raw layer.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="extract the changes since the last run")
    run.add_argument("--tables", nargs="+", metavar="TABLE", help="default: all tables")
    run.add_argument(
        "--full", action="store_true", help="ignore the watermark and re-read everything"
    )
    run.set_defaults(handler=_run)

    status = commands.add_parser("status", help="show the watermark and lag of each table")
    status.set_defaults(handler=_status)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
