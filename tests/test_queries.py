import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from app.db.database import init_schema
from app.db.queries import (
    insert_card,
    get_card,
    list_cards,
    update_card,
    delete_card,
    insert_job,
    get_job,
    list_jobs,
    update_job_status,
    update_job_progress,
    delete_job,
    cleanup_stale_running_jobs,
)
from app.core.params import TtsParams


@pytest.fixture
def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    init_schema(db_path)
    yield db_path


def _sample_params() -> TtsParams:
    return TtsParams(seed=42, temperature=0.3, top_p=0.7, top_k=20, speaker="abc")


def test_insert_card_returns_id(db: Path):
    card_id = insert_card(
        db,
        name=None,
        params=_sample_params(),
        demo_text="hello",
        demo_audio_path="audio/1/demo.wav",
        demo_subtitle_path="audio/1/demo.srt",
    )
    assert isinstance(card_id, int)
    assert card_id >= 1


def test_get_card_returns_full_row(db: Path):
    card_id = insert_card(
        db,
        name="my-card",
        params=_sample_params(),
        demo_text="hello",
        demo_audio_path="audio/1/demo.wav",
        demo_subtitle_path="audio/1/demo.srt",
    )
    row = get_card(db, card_id)
    assert row is not None
    assert row["id"] == card_id
    assert row["name"] == "my-card"
    assert row["demo_text"] == "hello"
    assert row["is_favorited"] == 0
    assert json.loads(row["params"])["seed"] == 42


def test_get_card_returns_none_for_missing(db: Path):
    assert get_card(db, 999) is None


def test_list_cards_returns_all(db: Path):
    for i in range(3):
        insert_card(db, name=f"c{i}", params=_sample_params(),
                    demo_text=f"t{i}", demo_audio_path=None, demo_subtitle_path=None)
    rows = list_cards(db)
    assert len(rows) == 3
    # 默认按 created_at 倒序（最新在前）
    assert rows[0]["name"] == "c2"


def test_list_cards_filter_favorited(db: Path):
    insert_card(db, name="a", params=_sample_params(),
                demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    cid = insert_card(db, name="b", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    update_card(db, cid, is_favorited=True)

    fav = list_cards(db, favorited=True)
    assert len(fav) == 1
    assert fav[0]["name"] == "b"

    all_ = list_cards(db, favorited=False)
    assert len(all_) == 2


def test_update_card_rename(db: Path):
    cid = insert_card(db, name="old", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    update_card(db, cid, name="new")
    row = get_card(db, cid)
    assert row["name"] == "new"


def test_update_card_favorite(db: Path):
    cid = insert_card(db, name="x", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    update_card(db, cid, is_favorited=True)
    assert get_card(db, cid)["is_favorited"] == 1
    update_card(db, cid, is_favorited=False)
    assert get_card(db, cid)["is_favorited"] == 0


def test_delete_card_removes_row(db: Path):
    cid = insert_card(db, name="x", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    delete_card(db, cid)
    assert get_card(db, cid) is None


def test_insert_job_with_pending_status(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    job_id = str(uuid.uuid4())
    insert_job(
        db,
        id=job_id,
        card_id=cid,
        params=_sample_params(),
        text="hello",
    )
    row = get_job(db, job_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["progress"] == 0.0
    assert row["text"] == "hello"


def test_insert_job_requires_existing_card(db: Path):
    """FK 约束：card_id 不存在时插入失败。"""
    with pytest.raises(sqlite3.IntegrityError):
        insert_job(
            db,
            id=str(uuid.uuid4()),
            card_id=999,
            params=_sample_params(),
            text="x",
        )


def test_get_job_returns_none_for_missing(db: Path):
    assert get_job(db, "no-such-id") is None


def test_update_job_status_to_running(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    jid = str(uuid.uuid4())
    insert_job(db, id=jid, card_id=cid, params=_sample_params(), text="x")
    update_job_status(db, jid, "running", started_at=datetime.now())
    assert get_job(db, jid)["status"] == "running"
    assert get_job(db, jid)["started_at"] is not None


def test_update_job_status_to_done_with_paths(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    jid = str(uuid.uuid4())
    insert_job(db, id=jid, card_id=cid, params=_sample_params(), text="x")
    update_job_status(
        db, jid, "done",
        finished_at=datetime.now(),
        result_audio_path="audio/x/jobs/y/audio.wav",
        result_subtitle_path="audio/x/jobs/y/subtitle.srt",
        result_params_path="audio/x/jobs/y/params.json",
        duration_sec=2.5,
    )
    row = get_job(db, jid)
    assert row["status"] == "done"
    assert row["result_audio_path"] == "audio/x/jobs/y/audio.wav"
    assert row["duration_sec"] == 2.5
    assert row["finished_at"] is not None


def test_update_job_status_to_failed_with_error(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    jid = str(uuid.uuid4())
    insert_job(db, id=jid, card_id=cid, params=_sample_params(), text="x")
    update_job_status(db, jid, "failed", finished_at=datetime.now(),
                      error="synthesis crashed")
    row = get_job(db, jid)
    assert row["status"] == "failed"
    assert row["error"] == "synthesis crashed"


def test_update_job_progress(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    jid = str(uuid.uuid4())
    insert_job(db, id=jid, card_id=cid, params=_sample_params(), text="x")
    update_job_progress(db, jid, 0.5)
    assert get_job(db, jid)["progress"] == 0.5
    update_job_progress(db, jid, 0.9)
    assert get_job(db, jid)["progress"] == 0.9


def test_list_jobs_filter_by_status(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for jid in ids:
        insert_job(db, id=jid, card_id=cid, params=_sample_params(), text="x")
    update_job_status(db, ids[0], "done", result_audio_path="a", result_subtitle_path="b",
                      result_params_path="c", duration_sec=1.0)
    update_job_status(db, ids[1], "failed", error="e")

    done = list_jobs(db, statuses=["done"])
    assert len(done) == 1
    assert done[0]["id"] == ids[0]

    failed = list_jobs(db, statuses=["failed"])
    assert len(failed) == 1
    assert failed[0]["id"] == ids[1]

    pending = list_jobs(db, statuses=["pending"])
    assert len(pending) == 1
    assert pending[0]["id"] == ids[2]


def test_list_jobs_no_status_filter_returns_all(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    for _ in range(3):
        insert_job(db, id=str(uuid.uuid4()), card_id=cid,
                   params=_sample_params(), text="x")
    assert len(list_jobs(db)) == 3


def test_list_jobs_limit(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    for _ in range(5):
        insert_job(db, id=str(uuid.uuid4()), card_id=cid,
                   params=_sample_params(), text="x")
    assert len(list_jobs(db, limit=3)) == 3


def test_delete_job(db: Path):
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    jid = insert_job(db, id=str(uuid.uuid4()), card_id=cid,
                     params=_sample_params(), text="x")
    delete_job(db, jid)
    assert get_job(db, jid) is None


def test_cleanup_stale_running_jobs(db: Path):
    """上次崩溃留下的 'running' 状态应被清理（标记为 failed）。"""
    cid = insert_card(db, name="c", params=_sample_params(),
                      demo_text="t", demo_audio_path=None, demo_subtitle_path=None)
    jid = str(uuid.uuid4())
    insert_job(db, id=jid, card_id=cid, params=_sample_params(), text="x")
    update_job_status(db, jid, "running", started_at=datetime.now())

    n = cleanup_stale_running_jobs(db)
    assert n == 1
    row = get_job(db, jid)
    assert row["status"] == "failed"
    # error 字段是中文（"进程崩溃，任务丢失"），用中文匹配
    assert "崩溃" in row["error"] or "丢失" in row["error"]
