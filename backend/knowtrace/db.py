"""SQLite connection management and initial schema creation."""
from __future__ import annotations

import sqlite3
import re
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
        # Existing SQLite CHECK constraints cannot be modified using ALTER.
        # Rebuild only question_versions inside a single rollback-safe transaction,
        # preserving all versions, child mapping records, indexes and triggers.
        # The migration runs BEFORE new exercises are enabled.
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='question_versions'"
        ).fetchone()["sql"]
        if "'short_answer'" not in table_sql:
            # executescript has already committed schema initialization.
            # Foreign key checks must be temporarily deferred for the table
            # replacement; checks are rerun before commit.
            connection.commit()
            connection.execute("PRAGMA foreign_keys=OFF")
            objects = [row["sql"] for row in connection.execute("""
                SELECT sql FROM sqlite_master
                WHERE tbl_name='question_versions' AND type IN ('trigger','index')
                AND sql IS NOT NULL
            """)]
            try:
                connection.execute("BEGIN IMMEDIATE")
                replacement = re.sub(
                    r"^(CREATE\s+TABLE\s+)(?:IF\s+NOT\s+EXISTS\s+)?[\"\x60]?question_versions[\"\x60]?",
                    r"\1question_versions_v2", table_sql, count=1, flags=re.I)
                if replacement == table_sql:
                    raise RuntimeError("Unable to safely migrate question_versions")
                replacement = replacement.replace(
                    "'single_choice','true_false'",
                    "'single_choice','multiple_choice','true_false','short_answer'")
                replacement = replacement.replace(
                    "'single_choice', 'true_false'",
                    "'single_choice','multiple_choice','true_false','short_answer'")
                connection.execute(replacement)
                connection.execute("INSERT INTO question_versions_v2 SELECT * FROM question_versions")
                connection.execute("DROP TABLE question_versions")
                connection.execute("ALTER TABLE question_versions_v2 RENAME TO question_versions")
                for statement in objects:
                    connection.execute(statement)
                broken = connection.execute("PRAGMA foreign_key_check").fetchone()
                if broken:
                    raise RuntimeError("Migration foreign-key consistency check failed")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.execute("PRAGMA foreign_keys=ON")
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
        # v1.5 adds chapter/similarity grouped relation jobs. Preserve existing
        # per-point snapshots and their completed progress without reset.
        relation_columns = {row["name"] for row in connection.execute(
            "PRAGMA table_info(relation_batch_runs)")}
        relation_extra = {
            "mode": "TEXT NOT NULL DEFAULT 'single'",
            "group_size": "INTEGER NOT NULL DEFAULT 1",
            "total_points": "INTEGER",
            "completed_points": "INTEGER NOT NULL DEFAULT 0",
            "bridge_strategy": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, sql_type in relation_extra.items():
            if column not in relation_columns:
                connection.execute(f"ALTER TABLE relation_batch_runs ADD COLUMN {column} {sql_type}")




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
