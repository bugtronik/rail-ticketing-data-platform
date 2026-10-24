"""Fixtures for the extraction tests: throwaway source and warehouse databases.

The extraction tests must COMMIT their source changes (the extractor reads
through its own connection, it cannot see uncommitted rows). To leave the
real databases untouched, each test gets two fresh databases, created by the
administrator accounts and dropped afterwards:

    MariaDB      extract_test     same schema as the source (01_schema.sql)
    PostgreSQL   warehouse_test   same raw/meta layers (02_warehouse.sql)

So these tests run identically on an empty source (CI) and on a seeded one.
"""

import dataclasses
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pymysql
import pytest
from pymysql.connections import Connection
from pymysql.constants import CLIENT

from rail_ticketing.extract.config import ExtractorConfig, load_extractor_config
from rail_ticketing.extract.connections import connect_source, connect_warehouse
from rail_ticketing.generator.config import GeneratorConfig
from rail_ticketing.generator.db import MariaDBWriter
from rail_ticketing.generator.history import generate_history

SOURCE_TEST_DB = "extract_test"
WAREHOUSE_TEST_DB = "warehouse_test"
SOURCE_SCHEMA = Path("source/mariadb/init/01_schema.sql")
WAREHOUSE_SCHEMA = Path("warehouse/init/02_warehouse.sql")


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set. Run the tests with `make test`, which loads .env.")
    return value


def _mariadb_root(database: str | None = None) -> Connection:
    return pymysql.connect(
        host=os.environ.get("MARIADB_HOST", "127.0.0.1"),
        port=int(os.environ.get("MARIADB_PORT", "3306")),
        user="root",
        password=require_env("MARIADB_ROOT_PASSWORD"),
        database=database,
        autocommit=True,
        client_flag=CLIENT.MULTI_STATEMENTS,  # to run the whole schema file at once
    )


def _postgres_admin(dbname: str) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("WAREHOUSE_HOST", "127.0.0.1"),
        port=int(os.environ.get("WAREHOUSE_PORT", "5432")),
        dbname=dbname,
        user=os.environ.get("WAREHOUSE_ADMIN_USER", "warehouse_admin"),
        password=require_env("WAREHOUSE_ADMIN_PASSWORD"),
        autocommit=True,  # CREATE / DROP DATABASE cannot run inside a transaction
    )


@pytest.fixture
def source_db() -> Iterator[str]:
    """A fresh MariaDB database with the source schema and the usual grants."""
    with _mariadb_root() as root, root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {SOURCE_TEST_DB}")
        cur.execute(f"CREATE DATABASE {SOURCE_TEST_DB}")
        cur.execute(f"USE {SOURCE_TEST_DB}")
        cur.execute(SOURCE_SCHEMA.read_text(encoding="utf-8"))
        while cur.nextset():  # consume the result of every statement of the file
            pass
        cur.execute(f"GRANT SELECT ON {SOURCE_TEST_DB}.* TO 'etl_reader'@'%'")
        cur.execute(f"GRANT SELECT, INSERT, UPDATE ON {SOURCE_TEST_DB}.* TO 'ticketing_app'@'%'")
    yield SOURCE_TEST_DB
    with _mariadb_root() as root, root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {SOURCE_TEST_DB}")


@pytest.fixture
def warehouse_db() -> Iterator[str]:
    """A fresh PostgreSQL database with the raw and meta layers."""
    maintenance = os.environ.get("WAREHOUSE_DB", "warehouse")
    with _postgres_admin(maintenance) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {WAREHOUSE_TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {WAREHOUSE_TEST_DB}")
    with _postgres_admin(WAREHOUSE_TEST_DB) as admin:
        admin.execute(WAREHOUSE_SCHEMA.read_text(encoding="utf-8"))
    yield WAREHOUSE_TEST_DB
    with _postgres_admin(maintenance) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {WAREHOUSE_TEST_DB} WITH (FORCE)")


@pytest.fixture
def seeded_source(
    source_db: str, connect_as: Callable[[str], Connection], config: GeneratorConfig
) -> str:
    """The test source filled with 3 days of generated history (committed)."""
    small = dataclasses.replace(
        config,
        booking_horizon_days=3,
        customers=dataclasses.replace(config.customers, count=500),
    )
    app = connect_as("app")
    app.select_db(source_db)
    generate_history(small, MariaDBWriter(app), now=datetime.now(UTC).replace(tzinfo=None), days=3)
    return source_db


@pytest.fixture
def extract_config() -> ExtractorConfig:
    """The real extractor configuration, without pauses between batches."""
    return dataclasses.replace(load_extractor_config(), pause_seconds=0)


@pytest.fixture
def source(seeded_source: str) -> Iterator[Connection]:
    conn = connect_source(seeded_source)
    yield conn
    conn.close()


@pytest.fixture
def warehouse(warehouse_db: str) -> Iterator[psycopg.Connection]:
    conn = connect_warehouse(warehouse_db)
    yield conn
    conn.close()


@pytest.fixture
def app_source(seeded_source: str, connect_as: Callable[[str], Connection]) -> Connection:
    """Application connection to the test source, to change rows (and commit)."""
    conn = connect_as("app")
    conn.select_db(seeded_source)
    return conn


@pytest.fixture
def warehouse_admin(warehouse_db: str) -> Iterator[psycopg.Connection]:
    conn = _postgres_admin(warehouse_db)
    yield conn
    conn.close()
