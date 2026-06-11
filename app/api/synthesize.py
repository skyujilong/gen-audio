"""/api/synthesize 路由：单条 + 批量合成（提交任务到队列）。"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter

from .. import config
from ..core.exceptions import CardNotFoundError
from ..core.params import (
    BatchSynthesizeRequest,
    Job,
    JobStatus,
    SynthesizeRequest,
)
from ..core.queue import submit_job
from ..db import queries


router = APIRouter(prefix="/api", tags=["synthesize"])


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


@router.post("/synthesize", response_model=Job)
def synthesize_one(req: SynthesizeRequest) -> Job:
    """提交单条合成任务到队列，立即返回 `Job`（pending 状态）。

    Raises:
        CardNotFoundError: card_id 不存在。
    """
    row = queries.get_card(config.DB_PATH, req.card_id)
    if row is None:
        raise CardNotFoundError(f"参数卡 {req.card_id} 不存在")

    job_id = submit_job(config.DB_PATH, card_id=req.card_id, params=req.params, text=req.text)
    job_row = queries.get_job(config.DB_PATH, job_id)
    return _row_to_job(job_row)


@router.post("/synthesize/batch", response_model=list[Job])
def synthesize_batch(req: BatchSynthesizeRequest) -> list[Job]:
    """批量提交合成任务到队列，返回 `list[Job]`（按提交顺序）。

    Raises:
        CardNotFoundError: 任一 item.card_id 不存在（**立即**失败，不提交已遍历的部分）。
    """
    jobs_out: list[Job] = []
    for item in req.items:
        row = queries.get_card(config.DB_PATH, item.card_id)
        if row is None:
            raise CardNotFoundError(f"参数卡 {item.card_id} 不存在")
        job_id = submit_job(config.DB_PATH, card_id=item.card_id, params=item.params, text=item.text)
        job_row = queries.get_job(config.DB_PATH, job_id)
        jobs_out.append(_row_to_job(job_row))
    return jobs_out
