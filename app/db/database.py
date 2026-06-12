"""SQLite 连接管理与 schema 初始化。

- 启动时调一次 `init_schema(db_path)` → 实际走 `migrations.migrate()` 跑 v0→v1→v2 幂等升级。
- 业务代码用 `get_connection(db_path)` 取连接（context manager，自动 commit/rollback）。
- 启用外键（`PRAGMA foreign_keys = ON`），让 `ON DELETE CASCADE` 真正生效。

> Phase 1.5：`init_schema` 改为 `migrate` 的薄包装，schema 升级路径统一在
> `app/core/migrations.py` 里维护。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

# 兼容老测试 / 老调用方：`SCHEMA_SQL` 仍导出，但实际不再被 init_schema 使用。
SCHEMA_SQL = """
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


def init_schema(db_path: Path) -> None:
    """初始化 / 升级数据库 schema 到当前最新版本（v2）。

    内部走 `app.core.migrations.migrate`，幂等：
    - 全新空库：建 v0 baseline → 升 v1（speakers）→ 升 v2（cards.speaker_id）。
    - 老库（v0）：同上从 v0 起步升级。
    - 已是 v2：no-op。
    """
    # 延迟导入避免循环依赖（migrations 依赖本文件的 get_connection）
    from ..core.migrations import migrate
    migrate(db_path)


@contextmanager
def get_connection(db_path: Path):
    """取一个 sqlite3 连接，启用外键，启用 Row 工厂。

    用法：
        with get_connection(db_path) as conn:
            conn.execute(...)

    异常时自动 rollback；正常退出时自动 commit。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
