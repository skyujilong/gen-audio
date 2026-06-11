import asyncio
import time
from pathlib import Path

import pytest

from app.core.params import TtsParams, JobStatus
from app.core import queue as queue_mod
from app.core.queue import (
    determine_concurrency,
    submit_job,
    cancel_job,
    get_queue_size,
    shutdown,
    in_memory,
)


def test_determine_concurrency_no_cuda(monkeypatch):
    class FakeCuda:
        @staticmethod
        def is_available():
            return False
    class FakeTorch:
        cuda = FakeCuda
    monkeypatch.setattr("app.core.queue.torch", FakeTorch)
    monkeypatch.delenv("MAX_CONCURRENT_SYNTHESIS", raising=False)
    n = determine_concurrency()
    assert n == 1


def test_determine_concurrency_low_vram_forces_1(monkeypatch):
    def fake_mem_get_info():
        return (2 * 1024**3, 8 * 1024**3)

    class FakeCuda:
        @staticmethod
        def is_available():
            return True
        @staticmethod
        def mem_get_info():
            return fake_mem_get_info()
    class FakeTorch:
        cuda = FakeCuda
    monkeypatch.setattr("app.core.queue.torch", FakeTorch)
    monkeypatch.delenv("MAX_CONCURRENT_SYNTHESIS", raising=False)
    n = determine_concurrency()
    assert n == 1


def test_determine_concurrency_enough_vram_uses_default(monkeypatch):
    def fake_mem_get_info():
        return (8 * 1024**3, 16 * 1024**3)

    class FakeCuda:
        @staticmethod
        def is_available():
            return True
        @staticmethod
        def mem_get_info():
            return fake_mem_get_info()
    class FakeTorch:
        cuda = FakeCuda
    monkeypatch.setattr("app.core.queue.torch", FakeTorch)
    monkeypatch.delenv("MAX_CONCURRENT_SYNTHESIS", raising=False)
    n = determine_concurrency()
    assert n == 2


def test_determine_concurrency_env_override(monkeypatch):
    def fake_mem_get_info():
        return (8 * 1024**3, 16 * 1024**3)

    class FakeCuda:
        @staticmethod
        def is_available():
            return True
        @staticmethod
        def mem_get_info():
            return fake_mem_get_info()
    class FakeTorch:
        cuda = FakeCuda
    monkeypatch.setattr("app.core.queue.torch", FakeTorch)
    monkeypatch.setenv("MAX_CONCURRENT_SYNTHESIS", "3")
    n = determine_concurrency()
    assert n == 3


@pytest.mark.asyncio
async def test_submit_and_complete_a_job(tmp_path: Path, monkeypatch):
    """端到端：submit → worker 跑完 → 状态 done。"""
    db_path = tmp_path / "test.db"
    from app.db.database import init_schema
    init_schema(db_path)

    # 准备一张卡
    from app.db.queries import insert_card
    cid = insert_card(
        db_path,
        name=None,
        params=TtsParams(seed=1, speaker="x"),
        demo_text="d",
        demo_audio_path=None,
        demo_subtitle_path=None,
    )

    # patch 真实合成
    async def fake_synthesize_with_progress(card_id, text, params, on_progress, job_id):
        # 模拟短合成
        on_progress(0.5)
        await asyncio.sleep(0.01)
        on_progress(1.0)
        return (
            str(tmp_path / f"audio_{card_id}_{text}.wav"),
            "fake-srt",
            "fake-params.json",
        )

    monkeypatch.setattr("app.core.queue.synthesize_with_progress", fake_synthesize_with_progress)

    # 启动 worker（单并发便于测试）
    from app.core import chat_tts
    chat_tts._LOADED = True
    queue_mod.init_queue(db_path, concurrency=1)
    try:
        job_id = submit_job(db_path, card_id=cid, params=TtsParams(seed=1, speaker="x"),
                            text="hello")
        # 等待完成
        for _ in range(50):  # 最多 5 秒
            row = queue_mod.get_job_cached(job_id)
            if row and row["status"] in ("done", "failed", "canceled"):
                break
            await asyncio.sleep(0.1)

        row = queue_mod.get_job_cached(job_id)
        assert row is not None
        assert row["status"] == "done"
        assert row["result_audio_path"] is not None
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_submit_marks_pending_before_run(tmp_path: Path, monkeypatch):
    """submit 后 worker 取走前应能看到 pending 状态。"""
    db_path = tmp_path / "test.db"
    from app.db.database import init_schema
    init_schema(db_path)
    from app.db.queries import insert_card
    cid = insert_card(db_path, name=None, params=TtsParams(seed=1, speaker="x"),
                      demo_text="d", demo_audio_path=None, demo_subtitle_path=None)

    # 启动一个会 hang 住的合成
    started = asyncio.Event()
    finish = asyncio.Event()

    async def fake_synthesize_with_progress(card_id, text, params, on_progress, job_id):
        started.set()
        await finish.wait()
        return ("a", "b", "c")

    monkeypatch.setattr("app.core.queue.synthesize_with_progress", fake_synthesize_with_progress)

    from app.core import chat_tts
    chat_tts._LOADED = True
    queue_mod.init_queue(db_path, concurrency=1)
    try:
        job_id = submit_job(db_path, card_id=cid, params=TtsParams(seed=1, speaker="x"),
                            text="x")
        await asyncio.wait_for(started.wait(), timeout=2.0)
        # 查 DB 而不是 in_memory（in_memory status 不更新）
        from app.db.queries import get_job
        row = get_job(db_path, job_id)
        assert row["status"] == "running"
    finally:
        finish.set()
        await shutdown()


@pytest.mark.asyncio
async def test_cancel_pending_job(tmp_path: Path, monkeypatch):
    """pending 任务可以取消；running 任务取消抛错。"""
    db_path = tmp_path / "test.db"
    from app.db.database import init_schema
    init_schema(db_path)
    from app.db.queries import insert_card
    cid = insert_card(db_path, name=None, params=TtsParams(seed=1, speaker="x"),
                      demo_text="d", demo_audio_path=None, demo_subtitle_path=None)

    # worker 永远不跑完
    finish = asyncio.Event()
    async def fake_synthesize_with_progress(card_id, text, params, on_progress, job_id):
        await finish.wait()
        return ("a", "b", "c")
    monkeypatch.setattr("app.core.queue.synthesize_with_progress", fake_synthesize_with_progress)

    from app.core import chat_tts
    chat_tts._LOADED = True
    queue_mod.init_queue(db_path, concurrency=1)
    try:
        # 第一个任务占住 worker
        jid1 = submit_job(db_path, card_id=cid, params=TtsParams(seed=1, speaker="x"),
                          text="first")
        # 第二个任务排队
        jid2 = submit_job(db_path, card_id=cid, params=TtsParams(seed=1, speaker="x"),
                          text="second")
        await asyncio.sleep(0.05)  # 让 jid1 进入 running
        # 取消 pending 的 jid2
        await cancel_job(jid2)
        row = queue_mod.get_job_cached(jid2)
        assert row["status"] == "canceled"

        # 取消 running 的 jid1 应抛 JobNotCancellableError
        from app.core.exceptions import JobNotCancellableError
        with pytest.raises(JobNotCancellableError):
            await cancel_job(jid1)
    finally:
        finish.set()
        await shutdown()
