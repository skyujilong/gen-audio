"""测试 /api/draw 路由：抽卡创建 + 写文件 + 返回 DrawnCard。"""
from fastapi.testclient import TestClient

from app import config
from app.core import chat_tts
from app.db.database import init_schema
from app.main import app


def test_draw_creates_a_card(tmp_path, monkeypatch):
    """端到端：POST /api/draw 应返回 DrawnCard 并在 DB 留痕。"""
    # 隔离 config 指向 tmp 目录
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    import app.api.draw as draw_mod

    # patch 真实 ChatTTS 推理（用假的 wav 字节 + 一段时间段）
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"FAKEWAV", [(0.0, 1.0)]),
    )

    # patch draw_one 给个确定值，便于断言
    from app.core.params import TtsParams
    monkeypatch.setattr(
        draw_mod, "draw_one",
        lambda refiner_text=None: TtsParams(seed=42, temperature=0.3, top_p=0.7, top_k=20, speaker="x"),
    )

    client = TestClient(app)
    res = client.post("/api/draw", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["card_id"] >= 1
    assert body["params"]["seed"] == 42
    assert body["demo_text"]  # 非空
    assert body["demo_audio_url"].startswith("/api/cards/")
    assert body["demo_audio_url"].endswith("/audio")
    assert body["demo_subtitle_url"].startswith("/api/cards/")

    # 验证 DB
    from app.db.queries import get_card
    row = get_card(config.DB_PATH, body["card_id"])
    assert row is not None
    assert row["demo_audio_path"] is not None
    assert row["demo_audio_path"].startswith(f"audio/{body['card_id']}/")


def test_draw_with_refiner_text(tmp_path, monkeypatch):
    """refiner_text 字段会传给 draw_one。"""
    from app.core.params import TtsParams

    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    import app.api.draw as draw_mod

    captured: dict = {}

    def fake_draw_one(refiner_text=None):
        captured["refiner_text"] = refiner_text
        return TtsParams(seed=1, speaker="x")

    monkeypatch.setattr(draw_mod, "draw_one", fake_draw_one)
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"X", [(0.0, 1.0)]),
    )

    client = TestClient(app)
    res = client.post("/api/draw", json={"refiner_text": "温柔"})
    assert res.status_code == 200
    assert captured["refiner_text"] == "温柔"
