"""测试 schema migration 路径：v0→v1→v2 幂等。"""
from pathlib import Path

import pytest
import sqlite3

from app.core.migrations import migrate, CURRENT_VERSION
from app.db.database import init_schema, get_connection


def test_migrate_fresh_db_reaches_v2(tmp_path: Path):
    """全新空库跑 migrate，应到 v2。"""
    db_path = tmp_path / "fresh.db"
    version = migrate(db_path)
    assert version == CURRENT_VERSION
    assert version == 2

    # 验证 v1：speakers 表 + 索引
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='speakers'"
        ).fetchall()
        assert rows, "speakers 表应已创建"

        idx_names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name IN ('idx_speakers_favorited', 'idx_speakers_created_at')"
            ).fetchall()
        }
        assert {"idx_speakers_favorited", "idx_speakers_created_at"} <= idx_names

    # 验证 v2：cards.speaker_id 列 + 索引
    with get_connection(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
        assert "speaker_id" in cols

        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cards_speaker_id'"
        ).fetchall()
        assert idx_rows


def test_migrate_from_legacy_v0_db(tmp_path: Path):
    """模拟老库（v0 schema，无 speakers 表）走 migration 到 v2。

    注意：现在 `init_schema()` 自身就会跑到 v2（它是 `migrate` 的 wrapper），
    所以这里要**绕过**它，直接 inline v0 baseline SQL 建老库。
    """
    db_path = tmp_path / "legacy.db"
    # 用 v0 baseline SQL 直接建老库（绕开 init_schema wrapper）
    from app.core.migrations import _V0_BASELINE_SQL
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(_V0_BASELINE_SQL)
    conn.commit()
    conn.close()

    # 验证确实是 v0
    with get_connection(db_path) as conn:
        cur = conn.execute("PRAGMA user_version").fetchone()
        assert cur[0] == 0

    # 跑 migration
    version = migrate(db_path)
    assert version == 2

    # 升级后老数据保留
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cards'"
        ).fetchall()
        assert rows
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='synthesis_jobs'"
        ).fetchall()
        assert rows
        # v2 新增列
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
        assert "speaker_id" in cols


def test_migrate_idempotent_v2(tmp_path: Path):
    """已是 v2 时再跑 migrate 不应报错。"""
    db_path = tmp_path / "v2.db"
    migrate(db_path)
    version = migrate(db_path)  # 二次
    assert version == 2
    version = migrate(db_path)  # 三次
    assert version == 2


def test_migrate_v1_to_v2_adds_column_only_once(tmp_path: Path):
    """v1→v2 二次跑时不应重复 ALTER TABLE（幂等性靠 PRAGMA table_info 检测）。"""
    db_path = tmp_path / "v1_then_v2.db"
    # 跑到 v1
    migrate(db_path, target=1)
    # 跑到 v2
    migrate(db_path, target=2)
    # 再跑一次到 v2，不应报错
    migrate(db_path, target=2)
    with get_connection(db_path) as conn:
        # 仍只有 1 个 speaker_id 列
        cols = [row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall() if row[1] == "speaker_id"]
        assert len(cols) == 1


def test_speaker_id_column_is_nullable(tmp_path: Path):
    """cards.speaker_id 应可空（无 NOT NULL 约束）。"""
    db_path = tmp_path / "nullable.db"
    migrate(db_path)
    with get_connection(db_path) as conn:
        # 没 speakers 行的情况下也能插入 card（speaker_id = NULL）
        conn.execute(
            "INSERT INTO cards (name, params, demo_text) VALUES (?, ?, ?)",
            ("c", "{}", "d"),
        )
        row = conn.execute("SELECT speaker_id FROM cards").fetchone()
        assert row[0] is None


def test_fk_on_delete_set_null_via_app_layer(tmp_path: Path):
    """删 speakers 行后，引用它的 card 应保持 speaker_id=NULL（FK 由应用层保证）。"""
    db_path = tmp_path / "setnull.db"
    migrate(db_path)
    with get_connection(db_path) as conn:
        # 插一个 speaker
        conn.execute(
            "INSERT INTO speakers (name, tensor_base64) VALUES (?, ?)",
            ("s1", "abc"),
        )
        sid = conn.execute("SELECT id FROM speakers").fetchone()[0]
        # 插一个 card 引用
        conn.execute(
            "INSERT INTO cards (name, params, demo_text, speaker_id) VALUES (?, ?, ?, ?)",
            ("c", "{}", "d", sid),
        )
        # 应用层模拟 ON DELETE SET NULL
        conn.execute("UPDATE cards SET speaker_id = NULL WHERE speaker_id = ?", (sid,))
        conn.execute("DELETE FROM speakers WHERE id = ?", (sid,))
        # card 仍在，speaker_id 为 NULL
        row = conn.execute("SELECT speaker_id FROM cards").fetchone()
        assert row[0] is None
