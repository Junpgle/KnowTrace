"""SQLite connection management and initial schema creation."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "schema.sql"


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=20000")
    return connection


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Additive migration for existing v1.2 databases. CREATE TABLE IF NOT
        # EXISTS does not add columns to a pre-existing table.
        present = {row["name"] for row in connection.execute(
            "PRAGMA table_info(batch_extractions)")}
        extra = {
            "range_start": "INTEGER NOT NULL DEFAULT 1",
            "range_end": "INTEGER",
            "work_stage": "TEXT NOT NULL DEFAULT 'queued'",
            "in_flight_start": "INTEGER",
            "in_flight_end": "INTEGER",
            "in_flight_started_at": "TEXT",
        }
        for column, sql_type in extra.items():
            if column not in present:
                connection.execute(f"ALTER TABLE batch_extractions ADD COLUMN {column} {sql_type}")



@contextmanager
def transaction(path: Path) -> Iterator[sqlite3.Connection]:
    db = connect(path)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
