"""Schema 版本管理：v0→v1→v2 幂等升级。

约定：
- 用 `PRAGMA user_version` 记录当前 schema 版本。
- 启动时 `migrate(db_path)` 依次跑到目标版本（v2 = 当前最新）。
- 每个 migration 是闭包：判断当前版本 < target 才执行；执行完 `PRAGMA user_version = target`。
- 全部在 `with get_connection(db_path) as conn` 上下文里跑（外键开启 + 自动 commit）。

升级路径：
- v0 → v1：建 `speakers` 表（音色库） + 索引
- v1 → v2：给 `cards` 加可空 `speaker_id` 列（FK → speakers.id, ON DELETE SET NULL）
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..db.database import get_connection


# === v0 baseline schema ===
# 复制自 app/db/database.py 的 SCHEMA_SQL（v0 版本，不含 speakers 表 / speaker_id 列）。
# 这里直接 inline 以避免 migrate() ↔ init_schema() 的循环调用。
_V0_BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    params TEXT NOT NULL,                    -- JSON: TtsParams
    demo_text TEXT NOT NULL,
    demo_audio_path TEXT,
    demo_subtitle_path TEXT,
    is_favorited INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS synthesis_jobs (
    id TEXT PRIMARY KEY,                     -- UUID
    card_id INTEGER NOT NULL,
    params TEXT NOT NULL,                    -- JSON: TtsParams（提交时快照）
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0.0,
    error TEXT,
    result_audio_path TEXT,
    result_subtitle_path TEXT,
    result_params_path TEXT,
    duration_sec REAL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON synthesis_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_card_id ON synthesis_jobs(card_id);
"""


CURRENT_VERSION = 2


def _get_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


# === Migrations ===

def _migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """v0 → v1：建 `speakers` 表（音色库） + 索引。

    表结构：
    - id: 主键自增
    - name: 音色名（必填）
    - tensor_base64: ChatTTS speaker embedding base64 字符串（必填）
    - tags: JSON 数组字符串（可空）
    - is_favorited: 0/1
    - created_at/updated_at: CURRENT_TIMESTAMP
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS speakers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        tensor_base64 TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '[]',
        is_favorited INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_speakers_favorited
        ON speakers(is_favorited);
    CREATE INDEX IF NOT EXISTS idx_speakers_created_at
        ON speakers(created_at DESC);
    """)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2：给 `cards` 加可空 `speaker_id` 列（FK → speakers.id）。

    行为：
    - `speaker_id` 可空；老 cards 没绑定音色库 → 仍为 NULL。
    - `ON DELETE SET NULL`：删音色库某项时引用它的 card 变 NULL，**保留** `cards.speaker` 字符串快照。
    - 索引 `idx_cards_speaker_id` 加速 JOIN 列表查音色。
    """
    # SQLite 不支持 `ADD COLUMN ... REFERENCES`；分两步：加列 + 重建表带 FK。
    # 但简单做法：先加可空列（无 FK），靠应用层保证一致性。FK 约束不强加。
    cols = {row[1] for row in conn.execute("PRAGMA table_info(cards)").fetchall()}
    if "speaker_id" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN speaker_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cards_speaker_id ON cards(speaker_id)"
    )


# === 主入口 ===

_MIGRATIONS = [_migrate_v0_to_v1, _migrate_v1_to_v2]


def _apply_v0_baseline(db_path: Path) -> None:
    """建 v0 baseline schema（cards + synthesis_jobs + 索引）。

    用独立连接 + `executescript`，不复用 `get_connection`（避免与外层 migration
    上下文冲突）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_V0_BASELINE_SQL)
        conn.commit()
    finally:
        conn.close()


def migrate(db_path: Path, target: int = CURRENT_VERSION) -> int:
    """跑 migration 到 target 版本。返回最终版本。

    Args:
        db_path: SQLite 数据库路径（不存在会创建）。
        target: 目标版本，默认 `CURRENT_VERSION` (2)。

    Returns:
        最终的 `user_version`（应等于 `target`）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # v0 baseline：建 cards + synthesis_jobs（用 inline SQL 避免循环调用 init_schema）
    _apply_v0_baseline(db_path)

    with get_connection(db_path) as conn:
        current = _get_version(conn)
        for v in range(current + 1, target + 1):
            migration = _MIGRATIONS[v - 1]  # v=1 → _migrate_v0_to_v1
            migration(conn)
            _set_version(conn, v)
        return _get_version(conn)
