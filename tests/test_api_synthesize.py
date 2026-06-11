"""测试 /api/synthesize 和 /api/synthesize/batch 路由。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.core import queue as queue_mod
from app.core.params import JobStatus, TtsParams
from app.db.database import init_schema
from app.db.queries import insert_card
from app.main import app
from app.storage.files import write_demo_files


@pytest.fixture
def client_and_card(tmp_path: Path):
    """初始化 DB + 1 张卡 + 进入 lifespan（让 init_queue 跑起来）+ 返回 client/card_id。

    yield 后退出 lifespan，自动 `queue_shutdown`。
    """
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    paths = write_demo_files(
        data_root=config.DATA_ROOT,
        card_id=0,
        demo_text="d",
        demo_wav_bytes=b"x",
        demo_srt="s",
        params=TtsParams(seed=1, speaker="x"),
    )
    cid = insert_card(
        config.DB_PATH, name="c", params=TtsParams(seed=1, speaker="x"),
        demo_text="d", demo_audio_path=paths["demo_audio_path"],
        demo_subtitle_path=paths["demo_subtitle_path"],
    )
    # 用 context manager 触发 lifespan（启动 init_queue）
    with TestClient(app) as client:
        yield client, cid


def test_synthesize_single_creates_job(client_and_card):
    """单条合成：返回 Job，状态在 pending/running/done 之一（fast submit）。"""
    client, cid = client_and_card
    res = client.post("/api/synthesize", json={
        "card_id": cid,
        "params": {"seed": 1, "temperature": 0.3, "top_p": 0.7, "top_k": 20, "speaker": "x"},
        "text": "hello",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["card_id"] == cid
    assert body["text"] == "hello"
    assert body["status"] in (
        JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.DONE.value
    )
    assert body["params"]["seed"] == 1
    assert body["id"]  # UUID 非空


def test_synthesize_batch_creates_multiple_jobs(client_and_card):
    """批量合成：返回 N 个 Job，按提交顺序。"""
    client, cid = client_and_card
    res = client.post("/api/synthesize/batch", json={
        "items": [
            {"card_id": cid, "params": {"seed": 1, "temperature": 0.3, "top_p": 0.7,
                                        "top_k": 20, "speaker": "x"}, "text": "a"},
            {"card_id": cid, "params": {"seed": 2, "temperature": 0.3, "top_p": 0.7,
                                        "top_k": 20, "speaker": "x"}, "text": "b"},
        ]
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 2
    texts = [j["text"] for j in body]
    assert texts == ["a", "b"]


def test_synthesize_missing_card_404(client_and_card):
    """card_id 不存在时返回 404 + CARD_NOT_FOUND。"""
    client, _ = client_and_card
    res = client.post("/api/synthesize", json={
        "card_id": 9999,
        "params": {"seed": 1, "speaker": "x"},
        "text": "hi",
    })
    assert res.status_code == 404
    assert res.json()["code"] == "CARD_NOT_FOUND"


def test_synthesize_batch_missing_card_404(client_and_card):
    """批量合成中任一 card_id 不存在立即返回 404（已 submit 的不回滚）。"""
    client, cid = client_and_card
    res = client.post("/api/synthesize/batch", json={
        "items": [
            {"card_id": cid, "params": {"seed": 1, "speaker": "x"}, "text": "ok"},
            {"card_id": 9999, "params": {"seed": 1, "speaker": "x"}, "text": "bad"},
        ]
    })
    assert res.status_code == 404
    assert res.json()["code"] == "CARD_NOT_FOUND"
