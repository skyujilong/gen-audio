import sqlite3
from pathlib import Path

import pytest

from app.db.database import init_schema, get_connection, SCHEMA_SQL


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_init_schema_creates_cards_table(db_path: Path):
    init_schema(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cards'"
        )
        assert cur.fetchone() is not None


def test_init_schema_creates_synthesis_jobs_table(db_path: Path):
    init_schema(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='synthesis_jobs'"
        )
        assert cur.fetchone() is not None


def test_init_schema_creates_indexes(db_path: Path):
    init_schema(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('idx_jobs_status', 'idx_jobs_card_id')"
        )
        names = {row[0] for row in cur.fetchall()}
        assert {"idx_jobs_status", "idx_jobs_card_id"} <= names


def test_init_schema_idempotent(db_path: Path):
    """二次调用不应报错。"""
    init_schema(db_path)
    init_schema(db_path)
    # 不抛异常即通过


def test_get_connection_yields_sqlite3_connection(db_path: Path):
    init_schema(db_path)
    with get_connection(db_path) as conn:
        assert isinstance(conn, sqlite3.Connection)
        # 默认 row_factory=Row
        assert conn.row_factory is sqlite3.Row


def test_get_connection_foreign_keys_enabled(db_path: Path):
    init_schema(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1
