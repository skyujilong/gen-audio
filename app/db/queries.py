"""所有 SQL 集中这里。

约定：
- 接收 `db_path: Path` 作为第一个参数（不是连接本身），函数内部用 `get_connection()` 取连接。
- 返回 `sqlite3.Row` 或 `dict` 列表，方便路由层转 Pydantic。
- 业务异常（找不到/重复等）由调用方根据返回值判断；不要在这里 raise AppError
  （保持 db 层无业务依赖）。

CASCADE 行为：删 `cards` 行会自动删对应 `synthesis_jobs` 行（数据库层保证）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .database import get_connection
from ..core.params import TtsParams


# === 行 → dict 转换 ===

def _row_to_card_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "params": row["params"],  # JSON 字符串，不做预解析
        "demo_text": row["demo_text"],
        "demo_audio_path": row["demo_audio_path"],
        "demo_subtitle_path": row["demo_subtitle_path"],
        "is_favorited": bool(row["is_favorited"]),
        "created_at": _isoformat(row["created_at"]),
        "updated_at": _isoformat(row["updated_at"]),
    }


def _isoformat(ts: str | datetime) -> str:
    """SQLite 的 CURRENT_TIMESTAMP 返回 'YYYY-MM-DD HH:MM:SS'，统一转 ISO 8601。"""
    if isinstance(ts, datetime):
        return ts.isoformat()
    return ts.replace(" ", "T")


# === cards CRUD ===

def insert_card(
    db_path: Path,
    name: str | None,
    params: TtsParams,
    demo_text: str,
    demo_audio_path: str | None,
    demo_subtitle_path: str | None,
) -> int:
    """插入一张新卡，返回自增 id。"""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO cards (name, params, demo_text, demo_audio_path, demo_subtitle_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, json.dumps(params.model_dump()), demo_text, demo_audio_path, demo_subtitle_path),
        )
        return cur.lastrowid


def get_card(db_path: Path, card_id: int) -> dict[str, Any] | None:
    """按 id 取单卡；找不到返回 None。"""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return _row_to_card_dict(row) if row else None


def list_cards(db_path: Path, favorited: bool | None = None) -> list[dict[str, Any]]:
    """列卡，按 created_at 倒序。

    Args:
        favorited: True = 只列收藏；False/None = 全部。
    """
    with get_connection(db_path) as conn:
        if favorited is True:
            rows = conn.execute(
                "SELECT * FROM cards WHERE is_favorited = 1 ORDER BY created_at DESC, id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cards ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [_row_to_card_dict(r) for r in rows]


def update_card(
    db_path: Path,
    card_id: int,
    name: str | None = None,
    is_favorited: bool | None = None,
) -> None:
    """更新卡的 name 和/或 is_favorited。两者都可空。"""
    sets: list[str] = []
    values: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        values.append(name)
    if is_favorited is not None:
        sets.append("is_favorited = ?")
        values.append(1 if is_favorited else 0)

    if not sets:
        return  # 没东西可改

    sets.append("updated_at = CURRENT_TIMESTAMP")
    values.append(card_id)

    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE cards SET {', '.join(sets)} WHERE id = ?",
            values,
        )


def delete_card(db_path: Path, card_id: int) -> None:
    """删卡；DB 层 ON DELETE CASCADE 自动删对应 jobs。"""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))


# === synthesis_jobs 留到 Task 8 ===
