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


# === Phase 6.3：synthesize 页端到端（选卡 → 改 oral=5 → 批量 3 行 → 全 done） ===

def test_e2e_synthesize_page_flow_select_card_override_oral_batch(tmp_path, monkeypatch):
    """Phase 6.3：模拟 synthesize 页主流程。

    1. 建一个 speaker 入库
    2. 用库内 speaker 抽卡，得到 card_id
    3. 模拟前端：拿 card 详情 → 用列表 API 找到这张卡
    4. 模拟前端：param-panel 改 oral=5
    5. 模拟前端：3 行任务批量提交（/api/synthesize/batch）
    6. 轮询所有 job 直到都 done，验证：
       - 每个 job 的 params.oral 都是 5（用户改写生效）
       - params.speaker_id 来自 card 自带（沿用卡内）
    """
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "DB_PATH", tmp_path / "data" / "gen-audio.db")
    monkeypatch.setattr(main_mod, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main_mod, "SPEAKERS_DIR", tmp_path / "data" / "speakers")
    client, _ = _setup(tmp_path)

    # 1) 建 speaker
    sid = client.post("/api/speakers", json={
        "name": "phase6",
        "tensor_base64": _sample_base64(),
    }).json()["id"]

    # 2) mock 抽卡推理
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=42, speaker=_sample_base64(), oral=2, laugh=1, break_=1),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"DRAW", [(0.0, 1.0)]),
    )

    # 3) 抽卡（带 speaker_id）
    res = client.post("/api/draw", json={"speaker_id": sid, "demo_text": "原卡文本"})
    assert res.status_code == 200
    card_id = res.json()["card_id"]
    # DB.speaker_id 已落
    from app.db.queries import get_card
    assert get_card(config.DB_PATH, card_id)["speaker_id"] == sid

    # 4) 模拟前端：列卡找到这张（与 synthesize.js renderCardPicker 等价）
    listing = client.get("/api/cards").json()
    this_card = next(c for c in listing if c["id"] == card_id)
    assert this_card["speaker_id"] == sid
    # 拉详情拿到 params（与 synthesize.js selectCard 等价）
    detail = client.get(f"/api/cards/{card_id}").json()
    assert detail["params"]["oral"] == 2
    assert detail["params"]["seed"] == 42
    assert detail["speaker_id"] == sid

    # 5) 模拟前端：param-panel setParams + 改 oral=5（用户改写）
    submission_params = dict(detail["params"])
    submission_params["oral"] = 5  # 改写
    submission_params["laugh"] = 3  # 顺便改
    # 沿用卡内音色 → 把 speaker_id 一起带（synthesize.js _buildSubmitParams 行为）
    submission_params["speaker_id"] = detail["speaker_id"]
    submission_params.pop("speaker", None)
    # TtsParams.speaker 是必填 str 字段；用 speaker_id 时前端要塞空串占位
    submission_params["speaker"] = ""

    # 6) mock worker 合成（确保 db 跟 tests 用的同一个）
    from app.core import queue as queue_mod_inner
    async def fake_synthesize_with_progress(
        card_id, text, params, on_progress=None, job_id=None,
    ):
        from app.storage.files import write_synthesis_files
        if on_progress:
            on_progress(0.5)
        paths = write_synthesis_files(
            data_root=config.DATA_ROOT,
            card_id=card_id,
            job_id=job_id,
            audio_bytes=b"JOB_WAV",
            srt=f"00:00:00,000 --> 00:00:01,000\n{text}",
            params=params,
        )
        if on_progress:
            on_progress(1.0)
        return paths["audio_path"], paths["subtitle_path"], paths["params_path"]
    monkeypatch.setattr(queue_mod_inner, "synthesize_with_progress", fake_synthesize_with_progress)

    # 7) 批量提交 3 行（与 synthesize.js submitAllBtn 等价）
    with TestClient(app) as poll_client:
        rows = [
            {"rowEl": None, "text": f"任务行 #{i} 内容"} for i in range(1, 4)
        ]
        batch_res = poll_client.post("/api/synthesize/batch", json={
            "items": [
                {"card_id": card_id, "params": submission_params, "text": r["text"]}
                for r in rows
            ],
        })
        assert batch_res.status_code == 200, batch_res.text
        jobs = batch_res.json()
        assert len(jobs) == 3
        job_ids = [j["id"] for j in jobs]
        # 落库的 params 应当带 oral=5 + speaker_id
        for j in jobs:
            assert j["params"]["oral"] == 5
            assert j["params"]["laugh"] == 3
            assert j["params"]["speaker_id"] == sid
            assert j["card_id"] == card_id

        # 8) 轮询全部 done（与 synthesize.js startPolling(refreshJobs) 等价）
        deadline = time.time() + 10.0
        seen_done = set()
        while time.time() < deadline and len(seen_done) < 3:
            r = poll_client.get("/api/jobs?limit=100")
            assert r.status_code == 200
            for j in r.json():
                if j["id"] in job_ids and j["status"] == "done":
                    seen_done.add(j["id"])
            if len(seen_done) < 3:
                time.sleep(0.05)
        assert len(seen_done) == 3, f"only {len(seen_done)}/3 jobs done"

        # 9) 每个 done job 都能拿到 audio + subtitle
        for jid in job_ids:
            audio = poll_client.get(f"/api/jobs/{jid}/audio")
            assert audio.status_code == 200
            assert audio.content == b"JOB_WAV"
            srt = poll_client.get(f"/api/jobs/{jid}/subtitle")
            assert srt.status_code == 200
            assert "任务行" in srt.text
            params_json = poll_client.get(f"/api/jobs/{jid}/params.json")
            assert params_json.status_code == 200
            # params.json 反序列化后仍能看到 oral=5（持久化生效）
            p = params_json.json()
            assert p["oral"] == 5
            assert p["laugh"] == 3


def test_e2e_synthesize_page_speaker_override(tmp_path, monkeypatch):
    """Phase 6.3 增强：用户在 synthesize 页改了音色（点"加载"选另一个库内音色）。

    验证：提交时用的是新音色的 speaker_id，不是 card 自带的。
    """
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "DB_PATH", tmp_path / "data" / "gen-audio.db")
    monkeypatch.setattr(main_mod, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main_mod, "SPEAKERS_DIR", tmp_path / "data" / "speakers")
    client, _ = _setup(tmp_path)

    # 建 2 个 speaker：A 是卡内音色，B 是用户改用的
    sid_a = client.post("/api/speakers", json={
        "name": "voice-A",
        "tensor_base64": _sample_base64(),
    }).json()["id"]
    sid_b = client.post("/api/speakers", json={
        "name": "voice-B",
        "tensor_base64": _sample_base64(),
    }).json()["id"]
    assert sid_a != sid_b

    # 抽卡带 sid_a
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=1, speaker=_sample_base64()),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"x", [(0.0, 1.0)]),
    )
    cid = client.post("/api/draw", json={"speaker_id": sid_a}).json()["card_id"]

    detail = client.get(f"/api/cards/{cid}").json()
    # 模拟前端：用户点"加载"选了 voice-B → state.currentSpeaker.speaker_id = sid_b
    user_override_speaker = {
        "speaker_id": sid_b,
        "name": "voice-B",
        "tensor_base64": _sample_base64(),
    }
    # synthesize.js _buildSubmitParams 行为：
    #   currentSpeaker 优先于 card.speaker_id
    submission = dict(detail["params"])
    if user_override_speaker["speaker_id"] is not None:
        submission["speaker_id"] = user_override_speaker["speaker_id"]
        submission.pop("speaker", None)
    # TtsParams.speaker 必填：传空串占位
    submission["speaker"] = ""

    # mock worker
    from app.core import queue as queue_mod_inner
    async def fake_synth(card_id, text, params, on_progress=None, job_id=None):
        from app.storage.files import write_synthesis_files
        if on_progress: on_progress(1.0)
        paths = write_synthesis_files(
            data_root=config.DATA_ROOT, card_id=card_id, job_id=job_id,
            audio_bytes=b"JOB", srt="00:00:00,000 --> 00:00:01,000\nx",
            params=params,
        )
        return paths["audio_path"], paths["subtitle_path"], paths["params_path"]
    monkeypatch.setattr(queue_mod_inner, "synthesize_with_progress", fake_synth)

    with TestClient(app) as poll_client:
        res = poll_client.post("/api/synthesize", json={
            "card_id": cid, "params": submission, "text": "改音色",
        })
        assert res.status_code == 200
        job = res.json()
        # 关键断言：用的是 voice-B（sid_b），不是 voice-A（sid_a）
        assert job["params"]["speaker_id"] == sid_b
        assert job["params"]["speaker_id"] != sid_a


def test_e2e_synthesize_panel_no_seed_must_422_without_card_seed(tmp_path, monkeypatch):
    """回归测试：模拟 synthesize.js 真实提交路径（panel.getParams 不含 seed）。

    修复前：前端 panel.showSeed=false → getParams() 不返回 seed → POST 缺 seed → 422
            静默 catch 吞掉 → 用户看不到任何任务。
    修复后：synthesize.js _buildSubmitParams() 从 state.selectedCard.params.seed 兜底。
    本测试断言**未做兜底时**请求会 422（验证后端校验确实拒了缺 seed），
    并断言**做了兜底后**请求会 200（验证前端修复路径有效）。
    """
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "DB_PATH", tmp_path / "data" / "gen-audio.db")
    monkeypatch.setattr(main_mod, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main_mod, "SPEAKERS_DIR", tmp_path / "data" / "speakers")
    client, _ = _setup(tmp_path)

    # 抽卡得到一张有 seed=42 的卡
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=42, speaker=_sample_base64()),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"x", [(0.0, 1.0)]),
    )
    cid = client.post("/api/draw", json={}).json()["card_id"]
    detail = client.get(f"/api/cards/{cid}").json()
    assert detail["params"]["seed"] == 42

    # mock worker（避免实际跑 ChatTTS）
    from app.core import queue as queue_mod_inner
    async def fake_synth(card_id, text, params, on_progress=None, job_id=None):
        from app.storage.files import write_synthesis_files
        if on_progress: on_progress(1.0)
        paths = write_synthesis_files(
            data_root=config.DATA_ROOT, card_id=card_id, job_id=job_id,
            audio_bytes=b"JOB", srt="00:00:00,000 --> 00:00:01,000\nx",
            params=params,
        )
        return paths["audio_path"], paths["subtitle_path"], paths["params_path"]
    monkeypatch.setattr(queue_mod_inner, "synthesize_with_progress", fake_synth)

    with TestClient(app) as poll_client:
        # === 场景 A：模拟真实前端 panel.getParams()，不含 seed ===
        # 来自 detail["params"] 但 panel.showSeed=false 会过滤掉 seed 字段
        panel_params = {k: v for k, v in detail["params"].items() if k != "seed"}
        assert "seed" not in panel_params  # 模拟真实性
        # 同时附 oral/laugh 改写
        panel_params["oral"] = 5
        panel_params["laugh"] = 3
        # 沿用卡内音色：speaker_id 模式 + 空串占位
        panel_params["speaker_id"] = detail.get("speaker_id")
        panel_params["speaker"] = ""

        res = poll_client.post("/api/synthesize", json={
            "card_id": cid, "params": panel_params, "text": "测试",
        })
        # 关键断言：没 seed → 后端必须 422（验证 bug 的后端侧形状）
        assert res.status_code == 422, res.text
        assert "seed" in res.text.lower()

        # === 场景 B：前端已从 card 兜底 seed 后的提交 ===
        fixed_params = dict(panel_params)
        fixed_params["seed"] = detail["params"]["seed"]  # _buildSubmitParams 的兜底行为
        res2 = poll_client.post("/api/synthesize", json={
            "card_id": cid, "params": fixed_params, "text": "测试",
        })
        assert res2.status_code == 200, res2.text
        job = res2.json()
        # 落库的 params 应当含 oral=5/laugh=3/seed=42
        assert job["params"]["seed"] == 42
        assert job["params"]["oral"] == 5
        assert job["params"]["laugh"] == 3


def test_e2e_synthesize_passes_every_panel_field_through(tmp_path, monkeypatch):
    """回归：前端 param-panel 暴露 16 个字段，模拟「synthesize.js _buildSubmitParams()」
    全量提交 + 选 voice-B 改音色」后，落库的 job.params 必须保留全部 16 个字段。
    """
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "DB_PATH", tmp_path / "data" / "gen-audio.db")
    monkeypatch.setattr(main_mod, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main_mod, "SPEAKERS_DIR", tmp_path / "data" / "speakers")
    client, _ = _setup(tmp_path)

    # 建一个 speaker 库项
    sid_b = client.post("/api/speakers", json={
        "name": "voice-B",
        "tensor_base64": _sample_base64(),
    }).json()["id"]

    # mock 抽卡 + worker
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=42, speaker=_sample_base64()),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"x", [(0.0, 1.0)]),
    )
    cid = client.post("/api/draw", json={}).json()["card_id"]

    # 模拟前端 _buildSubmitParams 行为：
    #   1) 拉 panel.getParams() → 16 字段
    #   2) 设 oral/laugh/break_/enhance/denoise 等所有可改字段
    #   3) state.currentSpeaker = voice-B → p.speaker_id=sid_b; delete p.speaker
    panel_full = {
        "seed": 42,                                  # 兜底自 card
        "temperature": 0.42,
        "top_p": 0.55,
        "top_k": 15,
        "repetition_penalty": 1.15,
        "speed": 6,
        "oral": 5,                                   # 风格
        "laugh": 3,                                  # 风格
        "break_": 4,                                 # 风格
        "max_new_token": 1536,
        "skip_refine_text": False,
        "enhance_audio": True,                       # 增强
        "denoise_audio": True,                       # 降噪
        "solver": "rk4",                             # 增强
        "nfe": 32,                                   # 增强
        "tau": 0.3,                                  # 增强
    }
    # state.currentSpeaker = { speaker_id: sid_b, ... }
    panel_full["speaker_id"] = sid_b
    panel_full.pop("speaker", None)                  # 库引用优先 → 清空字符串
    panel_full["speaker"] = ""                       # TtsParams.speaker 必填占位

    # mock worker
    from app.core import queue as queue_mod_inner
    async def fake_synth(card_id, text, params, on_progress=None, job_id=None):
        from app.storage.files import write_synthesis_files
        if on_progress: on_progress(1.0)
        paths = write_synthesis_files(
            data_root=config.DATA_ROOT, card_id=card_id, job_id=job_id,
            audio_bytes=b"JOB", srt="00:00:00,000 --> 00:00:01,000\nx",
            params=params,
        )
        return paths["audio_path"], paths["subtitle_path"], paths["params_path"]
    monkeypatch.setattr(queue_mod_inner, "synthesize_with_progress", fake_synth)

    with TestClient(app) as poll_client:
        res = poll_client.post("/api/synthesize", json={
            "card_id": cid, "params": panel_full, "text": "全字段测试",
        })
        assert res.status_code == 200, res.text
        job = res.json()
        persisted = job["params"]

        # 关键断言：16 个 panel 字段全部出现在落库的 job.params
        expected_keys = {
            "seed", "temperature", "top_p", "top_k", "repetition_penalty",
            "speed", "oral", "laugh", "break_",
            "max_new_token", "skip_refine_text",
            "enhance_audio", "denoise_audio", "solver", "nfe", "tau",
            "speaker", "speaker_id",
        }
        actual_keys = set(persisted.keys())
        missing = expected_keys - actual_keys
        assert not missing, f"前端传的字段被后端丢了: {missing}"

        # 数值精确比对（验证整数 round 没出错）
        assert persisted["seed"] == 42
        assert persisted["oral"] == 5
        assert persisted["laugh"] == 3
        assert persisted["break_"] == 4
        assert persisted["speed"] == 6
        assert persisted["top_k"] == 15
        assert persisted["nfe"] == 32
        assert persisted["max_new_token"] == 1536
        assert persisted["temperature"] == 0.42
        assert persisted["top_p"] == 0.55
        assert persisted["repetition_penalty"] == 1.15
        assert persisted["tau"] == 0.3
        assert persisted["solver"] == "rk4"
        assert persisted["enhance_audio"] is True
        assert persisted["denoise_audio"] is True
        assert persisted["skip_refine_text"] is False
        # 音色：选了 voice-B → speaker_id 落库；speaker 字符串在 _resolve_speaker_id
        # 里被覆盖为 voice-B 的 tensor_base64 快照（双轨设计）。
        assert persisted["speaker_id"] == sid_b
        assert persisted["speaker"] == _sample_base64()


def test_e2e_synthesize_frontend_default_submit_path(tmp_path, monkeypatch):
    """回归：用户在前端选卡 + 改 oral/laugh/break_ + 不动音色，模拟 _buildSubmitParams
    真实行为 → 提交必须 200 + audio.wav 真实有内容（不是 0 长度静音）。

    修过两个 bug：
    1) 前端 _buildSubmitParams 漏 fallback 到 state.selectedCard.params.speaker
       → 提交 speaker='' → 后端 LZMAError → job failed
    2) refine 阶段把 [oral_X][laugh_X][break_X] 喂 GPT 容易塌缩成单字
       → 改塞 infer_code.prompt 前缀
    """
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "DB_PATH", tmp_path / "data" / "gen-audio.db")
    monkeypatch.setattr(main_mod, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(main_mod, "SPEAKERS_DIR", tmp_path / "data" / "speakers")
    client, _ = _setup(tmp_path)

    # 抽卡得到带真实 speaker 字符串的卡
    import app.api.draw as draw_mod
    monkeypatch.setattr(
        draw_mod, "draw_one_from_params",
        lambda **kw: TtsParams(seed=42, speaker=_sample_base64(), oral=1, laugh=0, break_=0),
    )
    monkeypatch.setattr(
        draw_mod, "synthesize_to_wav_bytes",
        lambda params, text, on_progress=None: (b"x", [(0.0, 1.0)]),
    )
    cid = client.post("/api/draw", json={}).json()["card_id"]
    detail = client.get(f"/api/cards/{cid}").json()
    real_speaker = detail["params"]["speaker"]
    assert len(real_speaker) > 0, "卡必须带真实 speaker 字符串"

    # 模拟前端 _buildSubmitParams 完整行为：
    #   - panel.getParams() 不返回 speaker / speaker_id（这俩在 param-panel 之外）
    #   - state.selectedCard.speaker_id is null
    #   - 修复后：state.selectedCard.params.speaker 非空 → 用它
    submission = {k: v for k, v in detail["params"].items()
                  if k not in ("speaker", "speaker_id")}
    # 用户改 oral=5 laugh=3 break_=4
    submission["oral"] = 5
    submission["laugh"] = 3
    submission["break_"] = 4
    # 修复后：fallback 到 card 自带 speaker
    submission["speaker"] = real_speaker
    submission.pop("speaker_id", None)
    # 兜底 seed
    submission["seed"] = detail["params"].get("seed", 42)

    # mock worker，验证确实走到 synthesize_to_wav_bytes
    from app.core import queue as queue_mod_inner
    real_wav_called = []
    async def fake_synth(card_id, text, params, on_progress=None, job_id=None):
        real_wav_called.append((text, params))
        from app.storage.files import write_synthesis_files
        if on_progress: on_progress(1.0)
        # 模拟真实 ChatTTS：返回 4s 的非空 audio bytes
        import struct, io, wave as _wave
        buf = io.BytesIO()
        with _wave.open(buf, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
            # 4s 的「有意义」音频（不是全 0 静音）
            samples = b"\x10\x00" * 24000 * 4
            wf.writeframes(samples)
        paths = write_synthesis_files(
            data_root=config.DATA_ROOT, card_id=card_id, job_id=job_id,
            audio_bytes=buf.getvalue(),
            srt="00:00:00,000 --> 00:00:04,000\n" + text,
            params=params,
        )
        return paths["audio_path"], paths["subtitle_path"], paths["params_path"]
    monkeypatch.setattr(queue_mod_inner, "synthesize_with_progress", fake_synth)

    with TestClient(app) as poll_client:
        res = poll_client.post("/api/synthesize", json={
            "card_id": cid, "params": submission, "text": "测试 oral/laugh/break_",
        })
        assert res.status_code == 200, res.text
        job = res.json()
        # 落库参数必须含 oral/laugh/break_ 全量 + 真实 speaker
        assert job["params"]["oral"] == 5
        assert job["params"]["laugh"] == 3
        assert job["params"]["break_"] == 4
        assert job["params"]["speaker"] == real_speaker  # 兜底后保留
        # 验证 worker 真的被调了
        assert len(real_wav_called) == 1
        called_text, called_params = real_wav_called[0]
        assert called_text == "测试 oral/laugh/break_"
        assert called_params.oral == 5
