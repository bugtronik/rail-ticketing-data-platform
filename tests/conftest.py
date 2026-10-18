"""Shared pytest fixtures: generator configuration, connections to the MariaDB source.

Connection settings come from environment variables (documented in
.env.example). `make test` loads them from .env before running pytest.
"""

import dataclasses
import os
from collections.abc import Callable, Iterator
from datetime import datetime

import pymysql
import pytest
from pymysql.connections import Connection

from rail_ticketing.generator.config import GeneratorConfig, load_config

# Database accounts created by source/mariadb/init/02_users.sh:
# short name -> (user name, environment variable holding its password).
ACCOUNTS = {
    "app": ("ticketing_app", "APP_DB_PASSWORD"),
    "etl": ("etl_reader", "ETL_DB_PASSWORD"),
}


def require_env(name: str) -> str:
    """Return an environment variable, or stop the test run with a clear message."""
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set. Run the tests with `make test`, which loads .env.")
    return value


@pytest.fixture
def connect_as() -> Iterator[Callable[[str], Connection]]:
    """Factory opening connections to the source; closes them all after the test.

    Transactions are never committed: whatever a test writes is rolled back
    on close, so tests leave no data behind (the app account cannot DELETE).
    """
    opened: list[Connection] = []

    def _connect(account: str) -> Connection:
        user, password_variable = ACCOUNTS[account]
        conn = pymysql.connect(
            host=os.environ.get("MARIADB_HOST", "127.0.0.1"),
            port=int(os.environ.get("MARIADB_PORT", "3306")),
            user=user,
            password=require_env(password_variable),
            database=os.environ.get("MARIADB_DATABASE", "ticketing"),
            autocommit=False,
        )
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        conn.close()


@pytest.fixture
def app_conn(connect_as: Callable[[str], Connection]) -> Connection:
    """Connection as the application account (SELECT, INSERT, UPDATE)."""
    return connect_as("app")


@pytest.fixture
def etl_conn(connect_as: Callable[[str], Connection]) -> Connection:
    """Connection as the read-only extraction account."""
    return connect_as("etl")


# --- Generator ------------------------------------------------------------------


@pytest.fixture(scope="session")
def now() -> datetime:
    """A fixed "present": tests that use it are reproducible, whatever the real date."""
    return datetime(2026, 3, 15, 12, 0)


@pytest.fixture(scope="session")
def config() -> GeneratorConfig:
    """The real configuration, smaller where size does not matter, so tests run fast."""
    cfg = load_config()
    return dataclasses.replace(
        cfg,
        booking_horizon_days=14,
        customers=dataclasses.replace(cfg.customers, count=3000),
    )
