"""合成任务队列（asyncio）。

设计：
- 全局单例：`job_queue`（asyncio.Queue）+ `in_memory`（dict: job_id -> job row）。
- 启动时 `init_queue(db_path, concurrency)` 启动 N 个 worker 协程 + 清理脏数据。
- 提交：`submit_job(...)` 写 DB(pending) + push 到 queue + 加 in_memory。
- 取消：`cancel_job(job_id)` —— pending 从 queue 移除 + DB canceled；running 抛 `JobNotCancellableError`。
- 关闭：`shutdown()` 取消所有 worker。

**不吞错**：所有异常在 worker 里被捕获后**记录到 DB**（status=failed, error=...），但**不**静默丢；
调用方（API 层）依然能从 DB 看到失败详情。
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# torch 顶层 import 失败时降级为 None（仅影响 GPU 探测；CPU 模式仍可用）
try:
    import torch
    _TORCH_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore[assignment]
    _TORCH_AVAILABLE = False

from .exceptions import JobNotCancellableError, JobNotFoundError
from .params import JobStatus, TtsParams


log = logging.getLogger(__name__)


# === 模块单例状态 ===

job_queue: asyncio.Queue | None = None
in_memory: dict[str, dict[str, Any]] = {}
worker_tasks: list[asyncio.Task] = []
_db_path: Path | None = None


# === 显存探测 + 并发决策 ===

def determine_concurrency() -> int:
    """根据环境探测合适的并发数。

    规则：
    - 无 CUDA（CPU 模式） → 1
    - 自由显存 < 4 GB → 1
    - 自由显存 ≥ 4 GB → env `MAX_CONCURRENT_SYNTHESIS`（默认 2）
    """
    if torch is None or not torch.cuda.is_available():
        return 1

    try:
        free_bytes, _ = torch.cuda.mem_get_info()
        free_gb = free_bytes / 1024**3
    except Exception:
        return 1

    if free_gb < 4.0:
        log.warning("可用显存 %.1f GB < 4 GB，强制并发=1", free_gb)
        return 1

    env_val = os.getenv("MAX_CONCURRENT_SYNTHESIS", "2")
    try:
        n = int(env_val)
        if n < 1:
            n = 1
        return n
    except ValueError:
        return 2


# === 合成封装（worker 调这个） ===

async def synthesize_with_progress(
    card_id: int,
    text: str,
    params: TtsParams,
    on_progress,
    job_id: str,
):
    """调真实 ChatTTS 合成；返回 (audio_path, srt_path, params_path)。

    默认走 `chat_tts.synthesize_to_wav_bytes`，但函数定义在 queue 层是为了让测试能 patch。
    实际实现里就是 import 一下然后调。

    Args:
        job_id: 当前任务的 UUID，用于把产物写到 `audio/<card_id>/jobs/<job_id>/`。
    """
    from .chat_tts import synthesize_to_wav_bytes
    from ..core.subtitle import build_srt
    from ..storage.files import write_synthesis_files
    from ..db.queries import get_card

    # 真实场景下应：调 ChatTTS → 拿到音频字节 + 字幕段
    # 这里为简化：把音频字节和字幕直接落盘
    audio_bytes, segments = synthesize_to_wav_bytes(
        params=params, text=text, on_progress=on_progress,
    )
    srt = build_srt([(text, segments[0][0], segments[0][1])]) if segments else ""

    # 写文件（用 data_root = db_path 父目录的 data/）
    data_root = _db_path.parent if _db_path else Path("data")
    paths = write_synthesis_files(
        data_root=data_root,
        card_id=card_id,
        job_id=job_id,
        audio_bytes=audio_bytes,
        srt=srt,
        params=params,
    )
    return paths["audio_path"], paths["subtitle_path"], paths["params_path"]


# === Worker 协程 ===

async def _worker_loop() -> None:
    """单个 worker：从 queue 取 job → 跑合成 → 更新 DB。"""
    assert job_queue is not None
    from ..db.queries import update_job_status, update_job_progress

    while True:
        job_id = await job_queue.get()
        try:
            row = in_memory.get(job_id)
            if row is None:
                # 已经被取消
                continue

            started_at = datetime.now()
            update_job_status(
                _db_path, job_id, JobStatus.RUNNING.value,
                started_at=started_at,
            )

            def on_progress(p: float):
                update_job_progress(_db_path, job_id, p)

            try:
                audio_path, srt_path, params_path = await synthesize_with_progress(
                    card_id=row["card_id"],
                    text=row["text"],
                    params=TtsParams.model_validate(row["params"]),
                    on_progress=on_progress,
                    job_id=job_id,
                )
                finished_at = datetime.now()
                duration = (finished_at - started_at).total_seconds()
                update_job_status(
                    _db_path, job_id, JobStatus.DONE.value,
                    finished_at=finished_at,
                    result_audio_path=audio_path,
                    result_subtitle_path=srt_path,
                    result_params_path=params_path,
                    duration_sec=duration,
                )
            except Exception as e:
                log.exception("synthesis job failed", extra={"job_id": job_id})
                update_job_status(
                    _db_path, job_id, JobStatus.FAILED.value,
                    finished_at=datetime.now(),
                    error=str(e),
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("worker outer error", extra={"job_id": job_id})
        finally:
            in_memory.pop(job_id, None)
            if torch is not None and torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            job_queue.task_done()


# === 公共 API ===

def init_queue(db_path: Path, concurrency: int | None = None) -> None:
    """启动 worker 池 + 清理脏数据。必须在 asyncio event loop 跑起来后调。

    Args:
        db_path: SQLite 文件路径。
        concurrency: 强制并发数；None 用 `determine_concurrency()`。
    """
    global job_queue, _db_path
    if concurrency is None:
        concurrency = determine_concurrency()
    log.info("合成队列启动：并发=%d", concurrency)

    job_queue = asyncio.Queue()
    _db_path = db_path

    # 清理崩溃残留
    from ..db.queries import cleanup_stale_running_jobs
    n = cleanup_stale_running_jobs(db_path)
    if n:
        log.warning("清理了 %d 条残留 running 任务", n)

    # 启动 worker
    for _ in range(concurrency):
        worker_tasks.append(asyncio.create_task(_worker_loop()))


async def shutdown() -> None:
    """关掉所有 worker。测试 / 应用退出时调。"""
    for t in worker_tasks:
        t.cancel()
    for t in worker_tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    worker_tasks.clear()
    in_memory.clear()


def get_queue_size() -> int:
    """当前内存中 pending + running 数。"""
    if job_queue is None:
        return 0
    return job_queue.qsize() + len(in_memory)


def submit_job(
    db_path: Path,
    card_id: int,
    params: TtsParams,
    text: str,
) -> str:
    """提交一个合成任务。返回 job_id（UUID）。

    写 DB(pending) + 推 queue + 加 in_memory。
    """
    from ..db.queries import insert_job

    job_id = str(uuid.uuid4())
    insert_job(db_path, id=job_id, card_id=card_id, params=params, text=text)

    # 同步 in_memory（worker 会再读 DB，但 in_memory 提前存可让 list_jobs / get 走缓存）
    in_memory[job_id] = {
        "id": job_id,
        "card_id": card_id,
        "params": params.model_dump(),
        "text": text,
        "status": JobStatus.PENDING.value,
        "progress": 0.0,
    }

    assert job_queue is not None
    job_queue.put_nowait(job_id)

    return job_id


async def cancel_job(job_id: str) -> None:
    """取消任务。

    - pending：从 `in_memory` 移除（worker 不会取到），DB 标 canceled。
      **简化版**：`asyncio.Queue` 不支持任意移除，所以**等 worker 取到时**通过检查 `in_memory`
      是否还有这个 job 来决定要不要跳过。如果还在 in_memory，worker 会处理；如果不在，worker 跳过。
    - running：抛 `JobNotCancellableError`。
    """
    from ..db.queries import get_job, update_job_status

    row = get_job(_db_path, job_id)
    if row is None:
        raise JobNotFoundError(f"任务 {job_id} 不存在")

    if row["status"] == "pending":
        in_memory.pop(job_id, None)
        update_job_status(_db_path, job_id, JobStatus.CANCELED.value,
                          finished_at=datetime.now())
    elif row["status"] == "running":
        raise JobNotCancellableError(f"任务 {job_id} 正在运行，无法取消")
    else:
        raise JobNotCancellableError(
            f"任务 {job_id} 状态为 {row['status']}，无法取消"
        )


def get_job_cached(job_id: str) -> dict[str, Any] | None:
    """优先从 in_memory 取（速度），fallback DB。"""
    if job_id in in_memory:
        return in_memory[job_id]
    if _db_path is None:
        return None
    from ..db.queries import get_job
    return get_job(_db_path, job_id)
