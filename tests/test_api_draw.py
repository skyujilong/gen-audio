"""测试 /api/draw 路由：生成参数 + 写文件 + 返回 DrawnCard。"""
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

    # patch draw_one_from_params 给个确定值，便于断言
    from app.core.params import TtsParams
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=42, temperature=0.3, top_p=0.7, top_k=20, speaker="x"),
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
    """refiner_text 字段会传给 draw_one_from_params。"""
    from app.core.params import TtsParams

    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    import app.api.draw as draw_mod

    captured: dict = {}

    def fake_draw_one_from_params(**kw):
        captured.update(kw)
        return TtsParams(seed=1, speaker="x", refiner_text=kw.get("refiner_text"))

    monkeypatch.setattr(draw_mod, "draw_one_from_params", fake_draw_one_from_params)
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"X", [(0.0, 1.0)]),
    )

    client = TestClient(app)
    res = client.post("/api/draw", json={"refiner_text": "温柔"})
    assert res.status_code == 200
    assert captured["refiner_text"] == "温柔"


def test_draw_with_custom_params(tmp_path, monkeypatch):
    """前端传入 temperature/top_p/top_k 等时，应传给 draw_one_from_params。"""
    from app.core.params import TtsParams

    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    import app.api.draw as draw_mod

    captured: dict = {}

    def fake_draw_one_from_params(**kw):
        captured.update(kw)
        return TtsParams(
            seed=kw.get("seed") or 1,
            temperature=kw["temperature"],
            top_p=kw["top_p"],
            top_k=kw["top_k"],
            speaker=kw.get("speaker") or "x",
            refiner_text=kw.get("refiner_text"),
            repetition_penalty=kw.get("repetition_penalty", 1.05),
            speed=kw.get("speed", 5),
            skip_refine_text=kw.get("skip_refine_text", False),
            max_new_token=kw.get("max_new_token", 2048),
            spk_smp=kw.get("spk_smp"),
            txt_smp=kw.get("txt_smp"),
        )

    monkeypatch.setattr(draw_mod, "draw_one_from_params", fake_draw_one_from_params)
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"X", [(0.0, 1.0)]),
    )

    client = TestClient(app)
    res = client.post("/api/draw", json={
        "temperature": 0.8,
        "top_p": 0.5,
        "top_k": 10,
        "seed": 42,
    })
    assert res.status_code == 200
    assert captured["temperature"] == 0.8
    assert captured["top_p"] == 0.5
    assert captured["top_k"] == 10
    assert captured["seed"] == 42
    body = res.json()
    assert body["params"]["temperature"] == 0.8
    assert body["params"]["seed"] == 42


def test_draw_with_new_params(tmp_path, monkeypatch):
    """新增字段（repetition_penalty/speed/skip_refine_text/max_new_token/spk_smp/txt_smp）传入时传给 draw_one_from_params。"""
    from app.core.params import TtsParams

    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    import app.api.draw as draw_mod

    captured: dict = {}

    def fake_draw_one_from_params(**kw):
        captured.update(kw)
        return TtsParams(
            seed=kw.get("seed") or 1,
            speaker=kw.get("speaker") or "x",
            repetition_penalty=kw["repetition_penalty"],
            speed=kw["speed"],
            skip_refine_text=kw["skip_refine_text"],
            max_new_token=kw["max_new_token"],
            spk_smp=kw.get("spk_smp"),
            txt_smp=kw.get("txt_smp"),
        )

    monkeypatch.setattr(draw_mod, "draw_one_from_params", fake_draw_one_from_params)
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"X", [(0.0, 1.0)]),
    )

    client = TestClient(app)
    res = client.post("/api/draw", json={
        "repetition_penalty": 1.5,
        "speed": 3,
        "skip_refine_text": True,
        "max_new_token": 1024,
        "spk_smp": "REF_AUDIO",
        "txt_smp": "参考文本",
    })
    assert res.status_code == 200
    assert captured["repetition_penalty"] == 1.5
    assert captured["speed"] == 3
    assert captured["skip_refine_text"] is True
    assert captured["max_new_token"] == 1024
    assert captured["spk_smp"] == "REF_AUDIO"
    assert captured["txt_smp"] == "参考文本"

    body = res.json()
    assert body["params"]["repetition_penalty"] == 1.5
    assert body["params"]["speed"] == 3
    assert body["params"]["skip_refine_text"] is True
    assert body["params"]["max_new_token"] == 1024


def test_draw_with_custom_demo_text(tmp_path, monkeypatch):
    """前端传入 demo_text 时，应使用该文本而非默认值。"""
    from app.core.params import TtsParams

    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    import app.api.draw as draw_mod

    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=1, speaker="x"),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"X", [(0.0, 1.0)]),
    )

    client = TestClient(app)
    res = client.post("/api/draw", json={"demo_text": "自定义试听文本"})
    assert res.status_code == 200
    assert res.json()["demo_text"] == "自定义试听文本"


def test_random_speaker_endpoint(tmp_path, monkeypatch):
    """GET /api/draw/random_speaker 返回 speaker 字段。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    chat_tts._MODEL = None
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "FAKE_SPEAKER_EMBEDDING")

    client = TestClient(app)
    res = client.get("/api/draw/random_speaker")
    assert res.status_code == 200
    assert res.json()["speaker"] == "FAKE_SPEAKER_EMBEDDING"
