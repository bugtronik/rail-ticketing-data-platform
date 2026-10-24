"""Connections used by the extractor, configured from the environment (.env)."""

import os

import psycopg
import pymysql
from pymysql.connections import Connection as MariaDBConnection


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set: run through `make`, which loads .env")
    return value


def connect_source(database: str | None = None) -> MariaDBConnection:
    """Read-only connection to the source, as `etl_reader`.

    autocommit=True: each batch query is its own short transaction. A long
    transaction on the source would keep an old snapshot open, preventing
    InnoDB from purging old row versions (the "history list" grows and
    production slows down). Short reads are the polite way to read prod.
    """
    return pymysql.connect(
        host=os.environ.get("MARIADB_HOST", "127.0.0.1"),
        port=int(os.environ.get("MARIADB_PORT", "3306")),
        user="etl_reader",
        password=_require("ETL_DB_PASSWORD"),
        database=database or os.environ.get("MARIADB_DATABASE", "ticketing"),
        autocommit=True,
    )


def connect_warehouse(dbname: str | None = None) -> psycopg.Connection:
    """Connection to the warehouse as `extractor` (raw and meta schemas only)."""
    return psycopg.connect(
        host=os.environ.get("WAREHOUSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("WAREHOUSE_PORT", "5432")),
        dbname=dbname or os.environ.get("WAREHOUSE_DB", "warehouse"),
        user="extractor",
        password=_require("EXTRACTOR_DB_PASSWORD"),
        application_name="rail-extract",
    )
