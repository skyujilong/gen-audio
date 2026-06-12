"""Phase 4.4：端到端抽卡测试。

串联：建 speaker → 抽卡（带 speaker_id）→ 音频/字幕流 → 提交合成 → 进度轮询。
全程 mock 真实 ChatTTS 推理，跑得很快，只验"业务路由层 + DB + 队列"的贯通性。
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient

from app import config
from app.core import queue as queue_mod
from app.core.params import TtsParams
from app.db.database import init_schema
from app.main import app


# === 公共 setup ===

def _setup(tmp_path: Path) -> tuple[TestClient, Path]:
    """建空 DB + 指向 tmp 的 DATA_ROOT + SPEAKERS_DIR + TestClient。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    config.SPEAKERS_DIR = config.DATA_ROOT / "speakers"
    init_schema(config.DB_PATH)
    return TestClient(app), config.SPEAKERS_DIR


def _sample_base64() -> str:
    """生成一段真实 tensor 的 base64。"""
    import base64
    buf = io.BytesIO()
    torch.save(torch.tensor([1.0, 2.0, 3.0]), buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# === 建 speaker + 抽卡（带 speaker_id）→ DB 验证 ===

def test_e2e_create_speaker_then_draw_with_speaker_id(tmp_path, monkeypatch):
    """建一个 speaker → 抽卡传 `speaker_id` → 返回 DrawnCard + DB.speaker_id 正确。"""
    client, _ = _setup(tmp_path)

    # 1) 建一个 speaker
    spk = client.post("/api/speakers", json={
        "name": "my-voice",
        "tensor_base64": _sample_base64(),
    })
    assert spk.status_code == 200, spk.text
    sid = spk.json()["id"]

    # 2) mock 推理层（避免真实 ChatTTS 跑模型）
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(
            seed=99,
            speaker=kw.get("speaker") or "x",  # 透传 draw.py 解析后的 speaker_id → tensor
            oral=3, laugh=1, break_=2,
        ),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"FAKEWAV", [(0.0, 1.5)]),
    )

    # 3) 抽卡：传 speaker_id
    res = client.post("/api/draw", json={"speaker_id": sid, "demo_text": "端到端测试"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["card_id"] >= 1
    assert body["params"]["seed"] == 99
    # params.speaker 应当是库里存的字符串快照
    assert body["params"]["speaker"] == _sample_base64()
    # 7 数字字段透传
    assert body["params"]["oral"] == 3
    assert body["params"]["laugh"] == 1
    assert body["params"]["break_"] == 2

    # 4) DB 验证：cards.speaker_id 已写入
    from app.db.queries import get_card
    row = get_card(config.DB_PATH, body["card_id"])
    assert row is not None
    assert row["speaker_id"] == sid

    # 5) CardListItem 列表里也能看到 speaker_id
    listing = client.get("/api/cards").json()
    this_card = next(c for c in listing if c["id"] == body["card_id"])
    assert this_card["speaker_id"] == sid


def test_e2e_draw_speaker_id_404(tmp_path, monkeypatch):
    """speaker_id 不存在时返回 404 + SPEAKER_NOT_FOUND。"""
    client, _ = _setup(tmp_path)
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"x", [(0.0, 1.0)]),
    )

    res = client.post("/api/draw", json={"speaker_id": 9999})
    assert res.status_code == 404
    assert res.json()["code"] == "SPEAKER_NOT_FOUND"


def test_e2e_draw_without_speaker_id_keeps_string_fallback(tmp_path, monkeypatch):
    """不传 speaker_id + 传显式 speaker 字符串 → 走老路径，DB.speaker_id 为 null。"""
    client, _ = _setup(tmp_path)
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"x", [(0.0, 1.0)]),
    )
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=1, speaker=kw.get("speaker") or "x"),
    )

    res = client.post("/api/draw", json={"speaker": "EMB"})
    assert res.status_code == 200
    cid = res.json()["card_id"]
    from app.db.queries import get_card
    row = get_card(config.DB_PATH, cid)
    assert row["speaker_id"] is None  # 没传 speaker_id → NULL


def test_e2e_draw_speaker_id_overrides_explicit_speaker_string(tmp_path, monkeypatch):
    """speaker_id 优先级 > 显式 speaker 字符串。"""
    client, _ = _setup(tmp_path)
    sid = client.post("/api/speakers", json={
        "name": "lib",
        "tensor_base64": _sample_base64(),
    }).json()["id"]

    import app.api.draw as draw_mod
    captured: dict = {}

    def fake_draw_one_from_params(**kw):
        captured["speaker"] = kw.get("speaker")
        return TtsParams(seed=1, speaker=kw.get("speaker") or "x")

    monkeypatch.setattr(draw_mod, "draw_one_from_params", fake_draw_one_from_params)
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"x", [(0.0, 1.0)]),
    )

    res = client.post("/api/draw", json={
        "speaker": "EXPLICIT_EMB",  # 应当被 speaker_id 覆盖
        "speaker_id": sid,
    })
    assert res.status_code == 200
    # 实际喂给 ChatTTS 的是库里的字符串（不是 EXPLICIT_EMB）
    assert captured["speaker"] == _sample_base64()


# === 整链路：抽卡 → 试听流 → 合成 → 进度轮询 ===

def test_e2e_full_pipeline_draw_then_synthesize_poll(tmp_path, monkeypatch):
    """整链路：建 speaker → 抽卡 → 试听流可读 → 提交合成 → 轮询拿到 done。

    关键技术点：lifespan 用 `from .config import DB_PATH` 在 import 时绑定路径，
    测试改 `config.DB_PATH` 不影响 lifespan。必须同时 patch `app.main.DB_PATH`。
    """
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "DB_PATH", tmp_path / "data" / "gen-audio.db")
    monkeypatch.setattr(main_mod, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main_mod, "SPEAKERS_DIR", tmp_path / "data" / "speakers")
    client, speakers_dir = _setup(tmp_path)

    # 1) 建 speaker
    sid = client.post("/api/speakers", json={
        "name": "e2e",
        "tensor_base64": _sample_base64(),
    }).json()["id"]

    # 2) mock 抽卡推理
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=1, speaker=_sample_base64()),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"DRAW_WAV", [(0.0, 1.0)]),
    )

    # 3) 抽卡
    draw_res = client.post("/api/draw", json={"speaker_id": sid})
    assert draw_res.status_code == 200
    cid = draw_res.json()["card_id"]
    audio_url = draw_res.json()["demo_audio_url"]
    srt_url = draw_res.json()["demo_subtitle_url"]

    # 4) 试听流可读
    audio_res = client.get(audio_url)
    assert audio_res.status_code == 200
    assert audio_res.content == b"DRAW_WAV"
    srt_res = client.get(srt_url)
    assert srt_res.status_code == 200

    # 5) mock 合成推理（worker 内的 synthesize_with_progress）
    from app.core import queue as queue_mod_inner
    async def fake_synthesize_with_progress(
        card_id, text, params, on_progress=None, job_id=None,
    ):
        """mock 合成：直接写 wav 到 job 目录，仿照真实产物的形状。"""
        from app.storage.files import write_synthesis_files
        if on_progress:
            on_progress(0.5)
        paths = write_synthesis_files(
            data_root=config.DATA_ROOT,
            card_id=card_id,
            job_id=job_id,
            audio_bytes=b"JOB_WAV",
            srt="00:00:00,000 --> 00:00:01,000\n" + text,
            params=params,
        )
        if on_progress:
            on_progress(1.0)
        return paths["audio_path"], paths["subtitle_path"], paths["params_path"]
    monkeypatch.setattr(queue_mod_inner, "synthesize_with_progress", fake_synthesize_with_progress)

    # 6) 提交合成任务（需进入 lifespan 启动 worker；用 with 块以触发 lifespan）
    with TestClient(app) as poll_client:
        synth_res = poll_client.post("/api/synthesize", json={
            "card_id": cid,
            "params": {
                "seed": 1,
                "speaker": _sample_base64(),
                "speaker_id": sid,  # Phase 4.2: 走 speaker_id 解析
            },
            "text": "合成文本",
        })
        assert synth_res.status_code == 200, synth_res.text
        job_id = synth_res.json()["id"]

        # 7) 轮询直到 done
        deadline = time.time() + 10.0
        final = None
        while time.time() < deadline:
            r = poll_client.get(f"/api/jobs/{job_id}")
            assert r.status_code == 200
            data = r.json()
            if data["status"] == "done":
                final = data
                break
            if data["status"] == "failed":
                pytest.fail(f"job failed: {data.get('error')}")
            time.sleep(0.05)
        assert final is not None, "synthesize job did not reach 'done' within 10s"
        assert final["progress"] == 1.0
        # params.speaker_id 在 job 落库后被 resolve 成字符串
        assert final["params"]["speaker_id"] == sid
        assert final["params"]["speaker"] == _sample_base64()
