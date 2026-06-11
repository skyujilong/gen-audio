"""测试 /api/jobs 路由：查询 / 取消 / 结果文件流。"""
import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.core import queue as queue_mod
from app.core.params import TtsParams
from app.db.database import init_schema
from app.db.queries import insert_card, insert_job
from app.main import app


@pytest.fixture
def client_and_card(tmp_path: Path):
    """建 DB + 1 张卡 + 启动 lifespan（含 init_queue）+ 同步 queue_mod._db_path。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)
    cid = insert_card(
        config.DB_PATH, name="c", params=TtsParams(seed=1, speaker="x"),
        demo_text="d", demo_audio_path=None, demo_subtitle_path=None,
    )
    with TestClient(app) as client:
        # queue_mod._db_path 是 init_queue 时设的全局，要和 config.DB_PATH 保持一致
        queue_mod._db_path = config.DB_PATH
        yield client, cid


def test_get_job_returns_job(client_and_card):
    """GET /api/jobs/{id} 返回 Job JSON。"""
    client, cid = client_and_card
    jid = "test-uuid-1"
    insert_job(config.DB_PATH, id=jid, card_id=cid,
               params=TtsParams(seed=1, speaker="x"), text="hi")
    res = client.get(f"/api/jobs/{jid}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == jid
    assert body["status"] == "pending"
    assert body["params"]["seed"] == 1


def test_get_job_404(client_and_card):
    """不存在的 job 返回 404 + JOB_NOT_FOUND。"""
    client, _ = client_and_card
    res = client.get("/api/jobs/no-such")
    assert res.status_code == 404
    assert res.json()["code"] == "JOB_NOT_FOUND"


def test_list_jobs_no_filter(client_and_card):
    """GET /api/jobs 返回所有任务。"""
    client, cid = client_and_card
    for _ in range(3):
        insert_job(config.DB_PATH, id=str(uuid.uuid4()), card_id=cid,
                   params=TtsParams(seed=1, speaker="x"), text="t")
    res = client.get("/api/jobs")
    assert res.status_code == 200
    arr = res.json()
    assert len(arr) == 3


def test_list_jobs_status_filter(client_and_card):
    """?status=pending 只列 pending。"""
    client, cid = client_and_card
    insert_job(config.DB_PATH, id="a", card_id=cid,
               params=TtsParams(seed=1, speaker="x"), text="t")
    insert_job(config.DB_PATH, id="b", card_id=cid,
               params=TtsParams(seed=1, speaker="x"), text="t")
    res = client.get("/api/jobs?status=pending")
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_list_jobs_multi_status_filter(client_and_card):
    """?status=pending,running 支持多状态。"""
    client, cid = client_and_card
    insert_job(config.DB_PATH, id="a", card_id=cid,
               params=TtsParams(seed=1, speaker="x"), text="t")
    insert_job(config.DB_PATH, id="b", card_id=cid,
               params=TtsParams(seed=1, speaker="x"), text="t")
    insert_job(config.DB_PATH, id="c", card_id=cid,
               params=TtsParams(seed=1, speaker="x"), text="t")
    res = client.get("/api/jobs?status=pending,running")
    assert res.status_code == 200
    assert len(res.json()) == 3


def test_list_jobs_limit(client_and_card):
    """?limit=2 限制返回条数。"""
    client, cid = client_and_card
    for _ in range(5):
        insert_job(config.DB_PATH, id=str(uuid.uuid4()), card_id=cid,
                   params=TtsParams(seed=1, speaker="x"), text="t")
    res = client.get("/api/jobs?limit=2")
    assert len(res.json()) == 2


def test_get_job_audio_not_ready(client_and_card):
    """pending 任务取 audio 返回 409 + JOB_NOT_READY。"""
    client, cid = client_and_card
    insert_job(config.DB_PATH, id="jid", card_id=cid,
               params=TtsParams(seed=1, speaker="x"), text="t")
    res = client.get("/api/jobs/jid/audio")
    assert res.status_code == 409
    assert res.json()["code"] == "JOB_NOT_READY"


def test_get_job_audio_404(client_and_card):
    """不存在的 job 取 audio 返回 404。"""
    client, _ = client_and_card
    res = client.get("/api/jobs/no-such/audio")
    assert res.status_code == 404
    assert res.json()["code"] == "JOB_NOT_FOUND"


def test_cancel_pending_job(client_and_card):
    """DELETE /api/jobs/{pending_id} 返回 204 + DB 状态变 canceled。"""
    client, cid = client_and_card
    # 让 worker 永远不完成（卡住）
    finish = asyncio.Event()

    async def fake_synth(card_id, text, params, on_progress, job_id):
        await finish.wait()
        return ("a", "b", "c")

    # patch 真实合成（在 queue module 内被调）
    queue_mod.synthesize_with_progress = fake_synth

    # 提交 2 个任务（j1 占住 worker，j2 排队）
    jid1 = queue_mod.submit_job(config.DB_PATH, card_id=cid,
                                params=TtsParams(seed=1, speaker="x"), text="first")
    jid2 = queue_mod.submit_job(config.DB_PATH, card_id=cid,
                                params=TtsParams(seed=1, speaker="x"), text="second")
    # 等待 j1 进入 running
    import time
    for _ in range(50):
        row = queue_mod.get_job_cached(jid1)
        if row and row["status"] == "running":
            break
        time.sleep(0.02)

    try:
        # 取消 pending 的 jid2
        res = client.delete(f"/api/jobs/{jid2}")
        assert res.status_code == 204
        from app.db.queries import get_job
        assert get_job(config.DB_PATH, jid2)["status"] == "canceled"
    finally:
        finish.set()
        # 让 j1 也跑完
        from app.db.queries import update_job_status
        from app.core.params import JobStatus
        update_job_status(
            config.DB_PATH, jid1, JobStatus.DONE.value,
            result_audio_path="audio/x/audio.wav",
            result_subtitle_path="audio/x/subtitle.srt",
            result_params_path="audio/x/params.json",
            duration_sec=0.0,
        )


def test_cancel_running_job_returns_409(client_and_card):
    """DELETE running 任务返回 409 + JOB_NOT_CANCELLABLE。"""
    client, cid = client_and_card
    finish = asyncio.Event()

    async def fake_synth(card_id, text, params, on_progress, job_id):
        await finish.wait()
        return ("a", "b", "c")

    queue_mod.synthesize_with_progress = fake_synth

    jid = queue_mod.submit_job(config.DB_PATH, card_id=cid,
                               params=TtsParams(seed=1, speaker="x"), text="x")
    import time
    for _ in range(50):
        row = queue_mod.get_job_cached(jid)
        if row and row["status"] == "running":
            break
        time.sleep(0.02)

    try:
        res = client.delete(f"/api/jobs/{jid}")
        assert res.status_code == 409
        assert res.json()["code"] == "JOB_NOT_CANCELLABLE"
    finally:
        finish.set()
