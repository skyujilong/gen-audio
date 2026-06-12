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
from ..core.params import TtsParams, SpeakerBase, SpeakerOut, SpeakerListItem


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
        "speaker_id": row["speaker_id"] if "speaker_id" in row.keys() else None,
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
    speaker_id: int | None = None,
) -> int:
    """插入一张新卡，返回自增 id。

    Phase 1.6：新增可选 `speaker_id`，引用 speakers 表（FK 由应用层保证）。
    老调用方不传 → 仍为 NULL，向后兼容。
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO cards (name, params, demo_text, demo_audio_path, demo_subtitle_path, speaker_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, json.dumps(params.model_dump()), demo_text, demo_audio_path, demo_subtitle_path, speaker_id),
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


# === synthesis_jobs CRUD ===

def _row_to_job_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "card_id": row["card_id"],
        "params": row["params"],  # JSON 字符串
        "text": row["text"],
        "status": row["status"],
        "progress": row["progress"],
        "error": row["error"],
        "result_audio_path": row["result_audio_path"],
        "result_subtitle_path": row["result_subtitle_path"],
        "result_params_path": row["result_params_path"],
        "duration_sec": row["duration_sec"],
        "created_at": _isoformat(row["created_at"]),
        "started_at": _isoformat(row["started_at"]) if row["started_at"] else None,
        "finished_at": _isoformat(row["finished_at"]) if row["finished_at"] else None,
    }


def insert_job(
    db_path: Path,
    id: str,
    card_id: int,
    params: TtsParams,
    text: str,
) -> None:
    """插入一个 pending 任务。id 由调用方生成（UUID）。"""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO synthesis_jobs (id, card_id, params, text, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (id, card_id, json.dumps(params.model_dump()), text),
        )


def get_job(db_path: Path, job_id: str) -> dict[str, Any] | None:
    """按 id 取任务；找不到返回 None。"""
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM synthesis_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job_dict(row) if row else None


def list_jobs(
    db_path: Path,
    statuses: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """列任务，按 created_at 倒序。

    Args:
        statuses: None = 全部；非空 = 只列这些状态。
        limit: 最多返回多少条；None = 全部。
    """
    sql = "SELECT * FROM synthesis_jobs"
    params: list[Any] = []
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        sql += f" WHERE status IN ({placeholders})"
        params.extend(statuses)
    sql += " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_job_dict(r) for r in rows]


def update_job_status(
    db_path: Path,
    job_id: str,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    result_audio_path: str | None = None,
    result_subtitle_path: str | None = None,
    result_params_path: str | None = None,
    duration_sec: float | None = None,
    error: str | None = None,
) -> None:
    """更新任务状态及相关字段。

    注意：`started_at` 传 None 不更新；传 datetime 才会写入。
    实际调用方传 `started_at=now()` 表示"现在进入 running"。
    """
    sets: list[str] = ["status = ?"]
    values: list[Any] = [status]

    if started_at is not None:
        sets.append("started_at = ?")
        values.append(started_at.isoformat(sep=" "))
    if finished_at is not None:
        sets.append("finished_at = ?")
        values.append(finished_at.isoformat(sep=" "))
    if result_audio_path is not None:
        sets.append("result_audio_path = ?")
        values.append(result_audio_path)
    if result_subtitle_path is not None:
        sets.append("result_subtitle_path = ?")
        values.append(result_subtitle_path)
    if result_params_path is not None:
        sets.append("result_params_path = ?")
        values.append(result_params_path)
    if duration_sec is not None:
        sets.append("duration_sec = ?")
        values.append(duration_sec)
    if error is not None:
        sets.append("error = ?")
        values.append(error)

    values.append(job_id)

    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE synthesis_jobs SET {', '.join(sets)} WHERE id = ?",
            values,
        )


def update_job_progress(db_path: Path, job_id: str, progress: float) -> None:
    """更新任务进度（0.0–1.0）。worker 调用频率较高，独立函数。"""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE synthesis_jobs SET progress = ? WHERE id = ?",
            (progress, job_id),
        )


def delete_job(db_path: Path, job_id: str) -> None:
    """删除任务（一般不调；保留以备调试）。"""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM synthesis_jobs WHERE id = ?", (job_id,))


def cleanup_stale_running_jobs(db_path: Path) -> int:
    """启动时调用：把上次崩溃残留的 'running' 行标为 'failed'。返回受影响行数。"""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE synthesis_jobs
            SET status = 'failed',
                error = '进程崩溃，任务丢失',
                finished_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
            """
        )
        return cur.rowcount


# === speakers CRUD（Phase 1.6） ===

def _row_to_speaker_dict(row: sqlite3.Row) -> dict[str, Any]:
    """speakers row → dict（tags 是 JSON 字符串，转 list）。"""
    tags_raw = row["tags"]
    if isinstance(tags_raw, str):
        tags = json.loads(tags_raw) if tags_raw else []
    else:
        tags = tags_raw or []
    return {
        "id": row["id"],
        "name": row["name"],
        "tensor_base64": row["tensor_base64"],
        "tags": tags,
        "is_favorited": bool(row["is_favorited"]),
        "created_at": _isoformat(row["created_at"]),
        "updated_at": _isoformat(row["updated_at"]),
    }


def insert_speaker(
    db_path: Path,
    name: str,
    tensor_base64: str,
    tags: list[str] | None = None,
    is_favorited: bool = False,
) -> int:
    """插入一个音色，返回自增 id。"""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO speakers (name, tensor_base64, tags, is_favorited)
            VALUES (?, ?, ?, ?)
            """,
            (name, tensor_base64, json.dumps(tags or []), 1 if is_favorited else 0),
        )
        return cur.lastrowid


def get_speaker(db_path: Path, speaker_id: int) -> dict[str, Any] | None:
    """按 id 取单个音色；找不到返回 None。"""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM speakers WHERE id = ?", (speaker_id,)
        ).fetchone()
        return _row_to_speaker_dict(row) if row else None


def list_speakers(
    db_path: Path,
    favorited: bool | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """列音色库，按 created_at 倒序。

    Args:
        favorited: True = 只列收藏；False/None = 全部。
        search: 可选 name 模糊匹配（LIKE '%search%'）。
    """
    sql = "SELECT * FROM speakers"
    params: list[Any] = []
    wheres: list[str] = []
    if favorited is True:
        wheres.append("is_favorited = 1")
    if search:
        wheres.append("name LIKE ?")
        params.append(f"%{search}%")
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += " ORDER BY created_at DESC, id DESC"

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_speaker_dict(r) for r in rows]


def update_speaker(
    db_path: Path,
    speaker_id: int,
    name: str | None = None,
    tags: list[str] | None = None,
    is_favorited: bool | None = None,
) -> None:
    """更新音色 name / tags / is_favorited。任一可空。"""
    sets: list[str] = []
    values: list[Any] = []
    if name is not None:
        sets.append("name = ?")
        values.append(name)
    if tags is not None:
        sets.append("tags = ?")
        values.append(json.dumps(tags))
    if is_favorited is not None:
        sets.append("is_favorited = ?")
        values.append(1 if is_favorited else 0)

    if not sets:
        return  # no-op

    sets.append("updated_at = CURRENT_TIMESTAMP")
    values.append(speaker_id)

    with get_connection(db_path) as conn:
        conn.execute(
            f"UPDATE speakers SET {', '.join(sets)} WHERE id = ?",
            values,
        )


def delete_speaker(db_path: Path, speaker_id: int) -> int:
    """删音色；同步把引用它的 cards.speaker_id 置 NULL（应用层模拟 ON DELETE SET NULL）。

    Returns:
        影响的行数（1 = 真删了 0 行也算成功）。
    """
    with get_connection(db_path) as conn:
        # 先把引用置 NULL
        conn.execute(
            "UPDATE cards SET speaker_id = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE speaker_id = ?",
            (speaker_id,),
        )
        cur = conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
        return cur.rowcount


def toggle_speaker_favorite(db_path: Path, speaker_id: int) -> bool | None:
    """切收藏状态。返回新的 is_favorited；找不到返回 None。"""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT is_favorited FROM speakers WHERE id = ?", (speaker_id,)
        ).fetchone()
        if not row:
            return None
        new_val = 0 if row["is_favorited"] else 1
        conn.execute(
            "UPDATE speakers SET is_favorited = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_val, speaker_id),
        )
        return bool(new_val)
