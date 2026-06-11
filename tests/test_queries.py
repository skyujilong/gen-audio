import json
import sqlite3
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
