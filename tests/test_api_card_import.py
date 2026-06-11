"""测试 /api/cards/import 路由：批量导入参数卡。"""
from fastapi.testclient import TestClient

from app import config
from app.db.database import init_schema
from app.main import app


def test_import_cards_creates_rows(tmp_path, monkeypatch):
    """批量导入：每张卡插一行，路径占位文件 + 默认 demo_text 填补。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    # patch 写文件（避免真实磁盘 IO）
    monkeypatch.setattr(
        "app.api.card_import.write_demo_files",
        lambda **kw: {
            "demo_audio_path": f"audio/{kw['card_id']}/demo.wav",
            "demo_subtitle_path": f"audio/{kw['card_id']}/demo.srt",
            "params_path": f"audio/{kw['card_id']}/params.json",
        },
    )

    client = TestClient(app)
    payload = {
        "cards": [
            {
                "name": "card-a",
                "params": {"seed": 1, "temperature": 0.3, "top_p": 0.7,
                           "top_k": 20, "speaker": "x"},
                "is_favorited": True,
            },
            {
                "name": "card-b",
                "params": {"seed": 2, "temperature": 0.3, "top_p": 0.7,
                           "top_k": 20, "speaker": "y"},
                "demo_text": "custom demo",
            },
        ]
    }
    res = client.post("/api/cards/import", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {"imported": 2}

    # 验证 DB
    from app.db.queries import list_cards
    rows = list_cards(config.DB_PATH)
    assert len(rows) == 2
    by_name = {r["name"]: r for r in rows}
    assert by_name["card-a"]["is_favorited"] is True
    assert by_name["card-b"]["demo_text"] == "custom demo"
    # 路径都填了
    for r in rows:
        assert r["demo_audio_path"] is not None
        assert r["demo_subtitle_path"] is not None


def test_import_empty_cards(tmp_path):
    """空数组返回 0。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    client = TestClient(app)
    res = client.post("/api/cards/import", json={"cards": []})
    assert res.status_code == 200
    assert res.json() == {"imported": 0}


def test_import_uses_default_demo_text_when_missing(tmp_path, monkeypatch):
    """不传 demo_text 时填默认。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    monkeypatch.setattr(
        "app.api.card_import.write_demo_files",
        lambda **kw: {
            "demo_audio_path": f"audio/{kw['card_id']}/demo.wav",
            "demo_subtitle_path": f"audio/{kw['card_id']}/demo.srt",
            "params_path": f"audio/{kw['card_id']}/params.json",
        },
    )

    client = TestClient(app)
    res = client.post("/api/cards/import", json={"cards": [
        {"name": "no-demo", "params": {"seed": 1, "speaker": "x"}},
    ]})
    assert res.status_code == 200
    from app.db.queries import list_cards
    rows = list_cards(config.DB_PATH)
    assert len(rows) == 1
    assert rows[0]["demo_text"]  # 非空（默认值）
