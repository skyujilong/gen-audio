"""/api/jobs 路由：任务查询 / 取消 / 结果文件流。"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response

from .. import config
from ..core.exceptions import (
    AudioFileNotFoundError,
    JobNotFoundError,
    JobNotReadyError,
)
from ..core.params import Job, JobStatus
from ..core.queue import cancel_job as queue_cancel_job
from ..db import queries


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _row_to_job(row: dict) -> Job:
    """DB row dict → Job Pydantic 模型。

    `row["params"]` 是 JSON 字符串（queries 层不预解析），需要先 `json.loads`。
    """
    params_raw = row["params"]
    if isinstance(params_raw, str):
        params_raw = json.loads(params_raw)
    return Job(
        id=row["id"],
        card_id=row["card_id"],
        params=params_raw,
        text=row["text"],
        status=JobStatus(row["status"]),
        progress=row["progress"],
        error=row["error"],
        duration_sec=row["duration_sec"],
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
    )


@router.get("", response_model=list[Job])
def list_jobs(
    status: str | None = Query(default=None, description="英文逗号分隔的状态列表；不传=全部"),
    limit: int | None = Query(default=None, ge=1, le=500),
) -> list[Job]:
    """列任务，按 created_at 倒序。

    Args:
        status: 英文逗号分隔的状态列表（如 `pending,running`）；不传=全部。
        limit: 最多返回多少条；不传=全部。
    """
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    rows = queries.list_jobs(config.DB_PATH, statuses=statuses, limit=limit)
    return [_row_to_job(r) for r in rows]


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    """取单任务详情。"""
    row = queries.get_job(config.DB_PATH, job_id)
    if row is None:
        raise JobNotFoundError(f"任务 {job_id} 不存在")
    return _row_to_job(row)


@router.delete("/{job_id}", status_code=204)
async def cancel_job_endpoint(job_id: str) -> Response:
    """取消任务（仅 pending 状态）。running/done/failed/canceled 抛 409。"""
    await queue_cancel_job(job_id)
    return Response(status_code=204)


def _check_done(row: dict, kind: str) -> None:
    """检查任务是否完成且有结果文件路径；否则抛对应异常。

    Args:
        kind: "audio" / "subtitle" / "params.json" —— 用于错误信息。
    """
    if row is None:
        raise JobNotFoundError(f"任务 {row['id'] if row else '?'} 不存在")
    if row["status"] != JobStatus.DONE.value:
        raise JobNotReadyError(f"任务 {row['id']} 尚未完成（status={row['status']}）")
    path_key = f"result_{kind}_path" if kind != "params.json" else "result_params_path"
    if not row.get(path_key):
        raise JobNotReadyError(f"任务 {row['id']} 缺少 {kind} 路径")


@router.get("/{job_id}/audio")
def get_job_audio(job_id: str) -> FileResponse:
    """合成结果音频流。"""
    row = queries.get_job(config.DB_PATH, job_id)
    if row is None:
        raise JobNotFoundError(f"任务 {job_id} 不存在")
    _check_done(row, "audio")
    full = config.DATA_ROOT / row["result_audio_path"]
    if not full.exists():
        raise AudioFileNotFoundError(f"音频文件不存在：{full}")
    return FileResponse(full, media_type="audio/wav")


@router.get("/{job_id}/subtitle")
def get_job_subtitle(job_id: str) -> str:
    """合成结果字幕（SRT 文本）。"""
    row = queries.get_job(config.DB_PATH, job_id)
    if row is None:
        raise JobNotFoundError(f"任务 {job_id} 不存在")
    _check_done(row, "subtitle")
    full = config.DATA_ROOT / row["result_subtitle_path"]
    if not full.exists():
        raise AudioFileNotFoundError(f"字幕文件不存在：{full}")
    return full.read_text(encoding="utf-8")


@router.get("/{job_id}/params.json")
def get_job_params(job_id: str) -> dict:
    """合成结果参数快照（JSON 字典）。"""
    row = queries.get_job(config.DB_PATH, job_id)
    if row is None:
        raise JobNotFoundError(f"任务 {job_id} 不存在")
    _check_done(row, "params.json")
    full = config.DATA_ROOT / row["result_params_path"]
    if not full.exists():
        raise AudioFileNotFoundError(f"参数快照文件不存在：{full}")
    return json.loads(full.read_text(encoding="utf-8"))
