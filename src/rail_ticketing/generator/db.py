"""Database access for the generator: connection and batched inserts.

The generator connects as `ticketing_app`, the application account
(SELECT, INSERT, UPDATE, no DELETE), like the real application would.
"""

import os
from dataclasses import fields
from operator import attrgetter

import pymysql
from pymysql.connections import Connection

APP_USER = "ticketing_app"
BATCH_SIZE = 5_000


def connect() -> Connection:
    """Connection settings come from the environment (see .env.example)."""
    password = os.environ.get("APP_DB_PASSWORD")
    if not password:
        raise RuntimeError("APP_DB_PASSWORD is not set: run through `make`, which loads .env")
    return pymysql.connect(
        host=os.environ.get("MARIADB_HOST", "127.0.0.1"),
        port=int(os.environ.get("MARIADB_PORT", "3306")),
        user=APP_USER,
        password=password,
        database=os.environ.get("MARIADB_DATABASE", "ticketing"),
        autocommit=False,
    )


def source_is_empty(conn: Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM stations)")
        return not cur.fetchone()[0]


class MariaDBWriter:
    """RowWriter (see history.py) that inserts rows in batches.

    PyMySQL turns `executemany` on an INSERT ... VALUES statement into
    multi-row INSERTs: one network round trip per batch instead of per row.
    That is the single biggest factor in loading speed.
    """

    def __init__(self, conn: Connection, batch_size: int = BATCH_SIZE) -> None:
        self.conn = conn
        self.batch_size = batch_size

    def write(self, table: str, rows: list) -> None:
        if not rows:
            return
        columns = [f.name for f in fields(rows[0])]
        # Table and column names come from our own dataclasses, never from input.
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "  # noqa: S608
            f"VALUES ({', '.join(['%s'] * len(columns))})"
        )
        values = attrgetter(*columns)  # fast tuple extraction from slotted dataclasses
        with self.conn.cursor() as cur:
            for start in range(0, len(rows), self.batch_size):
                cur.executemany(sql, [values(row) for row in rows[start : start + self.batch_size]])

    def commit(self) -> None:
        self.conn.commit()


def insert_one(conn: Connection, table: str, row: object, skip: tuple[str, ...]) -> int:
    """Insert one row, letting the database fill the `skip` columns (the
    AUTO_INCREMENT id, created_at, updated_at). Returns the new id."""
    columns = [f.name for f in fields(row) if f.name not in skip]  # type: ignore[arg-type]
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "  # noqa: S608
        f"VALUES ({', '.join(['%s'] * len(columns))})"
    )
    with conn.cursor() as cur:
        cur.execute(sql, [getattr(row, c) for c in columns])
        return cur.lastrowid
