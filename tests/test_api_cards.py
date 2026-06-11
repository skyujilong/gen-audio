"""测试 /api/cards 路由：CRUD + 收藏 + 试听文件流。"""
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.core.params import TtsParams
from app.db.database import init_schema
from app.db.queries import insert_card
from app.main import app
from app.storage.files import write_demo_files


def _setup(tmp_path: Path) -> tuple[TestClient, int]:
    """公共 setup：初始化 DB + 插 1 张带真实磁盘文件的卡 + 返回 client/card_id。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    # 先用 0 占位写文件，再插卡拿 id，最后搬文件到正确位置（避开 plan 中"先用 0 占位"的脏数据）
    paths = write_demo_files(
        data_root=config.DATA_ROOT,
        card_id=0,
        demo_text="hi",
        demo_wav_bytes=b"FAKE",
        demo_srt="srt",
        params=TtsParams(seed=1, speaker="x"),
    )
    cid = insert_card(
        config.DB_PATH,
        name="my",
        params=TtsParams(seed=1, speaker="x"),
        demo_text="hi",
        demo_audio_path=paths["demo_audio_path"],
        demo_subtitle_path=paths["demo_subtitle_path"],
    )
    # 搬文件从 audio/0/ 到 audio/{cid}/
    src_dir = config.DATA_ROOT / "audio" / "0"
    dst_dir = config.DATA_ROOT / "audio" / str(cid)
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        (dst_dir / f.name).write_bytes(f.read_bytes())
    # 更新 DB 路径
    from app.db.database import get_connection
    with get_connection(config.DB_PATH) as conn:
        conn.execute(
            "UPDATE cards SET demo_audio_path=?, demo_subtitle_path=? WHERE id=?",
            (f"audio/{cid}/demo.wav", f"audio/{cid}/demo.srt", cid),
        )
    return TestClient(app), cid


def test_list_cards(tmp_path):
    client, cid = _setup(tmp_path)
    res = client.get("/api/cards")
    assert res.status_code == 200
    arr = res.json()
    assert any(c["id"] == cid for c in arr)


def test_list_cards_filter_favorited(tmp_path):
    client, cid = _setup(tmp_path)
    # 收藏
    client.patch(f"/api/cards/{cid}", json={"is_favorited": True})
    res = client.get("/api/cards?favorited=true")
    arr = res.json()
    assert all(c["is_favorited"] for c in arr)


def test_get_card(tmp_path):
    client, cid = _setup(tmp_path)
    res = client.get(f"/api/cards/{cid}")
    assert res.status_code == 200
    assert res.json()["id"] == cid


def test_get_card_404(tmp_path):
    client, _ = _setup(tmp_path)
    res = client.get("/api/cards/9999")
    assert res.status_code == 404
    assert res.json()["code"] == "CARD_NOT_FOUND"


def test_update_card_rename(tmp_path):
    client, cid = _setup(tmp_path)
    res = client.patch(f"/api/cards/{cid}", json={"name": "新名字"})
    assert res.status_code == 200
    assert res.json()["name"] == "新名字"


def test_update_card_favorite(tmp_path):
    client, cid = _setup(tmp_path)
    res = client.patch(f"/api/cards/{cid}", json={"is_favorited": True})
    assert res.status_code == 200
    assert res.json()["is_favorited"] is True
    res = client.patch(f"/api/cards/{cid}", json={"is_favorited": False})
    assert res.json()["is_favorited"] is False


def test_delete_card(tmp_path):
    client, cid = _setup(tmp_path)
    res = client.delete(f"/api/cards/{cid}")
    assert res.status_code == 204
    assert client.get(f"/api/cards/{cid}").status_code == 404


def test_card_audio_stream(tmp_path):
    client, cid = _setup(tmp_path)
    res = client.get(f"/api/cards/{cid}/audio")
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert res.content == b"FAKE"


def test_card_subtitle_stream(tmp_path):
    client, cid = _setup(tmp_path)
    res = client.get(f"/api/cards/{cid}/subtitle")
    assert res.status_code == 200
    assert "srt" in res.text
