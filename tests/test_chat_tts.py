import numpy as np
import pytest

from app.core.params import TtsParams
from app.core.chat_tts import draw_one, synthesize_to_wav_bytes, is_model_loaded


def test_is_model_loaded_initially_false(monkeypatch):
    """在不真正加载模型的情况下，初始状态应为 False。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    assert is_model_loaded() is False


def test_draw_one_returns_valid_params(monkeypatch):
    """不真正加载模型；patch 掉随机来源。"""
    from app.core import chat_tts
    chat_tts._MODEL = None

    # patch random 函数让它返回确定值
    monkeypatch.setattr("app.core.chat_tts._random_int", lambda lo, hi: 12345)
    monkeypatch.setattr("app.core.chat_tts._random_float", lambda lo, hi: 0.5)
    monkeypatch.setattr("app.core.chat_tts._random_choice", lambda items: items[0])
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "BASE64SPEAKER")

    params = draw_one(refiner_text=None)
    assert isinstance(params, TtsParams)
    assert params.seed == 12345
    assert params.temperature == 0.5
    assert params.top_p == 0.5
    assert params.top_k == 10  # _random_choice  mock 返回 items[0] = 10
    assert params.speaker == "BASE64SPEAKER"
    assert params.refiner_text is None


def test_synthesize_to_wav_bytes_returns_wav_bytes(monkeypatch):
    """mock 真正的 ChatTTS 推理，验证接口形状。"""
    from app.core import chat_tts
    chat_tts._MODEL = None

    def fake_infer(params, text):
        # 返回 1 秒 24kHz 单声道浮点
        return np.zeros(24000, dtype=np.float32), [(0.0, 1.0)]

    monkeypatch.setattr("app.core.chat_tts._infer_audio", fake_infer)

    params = TtsParams(seed=1, speaker="x")
    wav_bytes, segments = synthesize_to_wav_bytes(params, text="hi")

    assert isinstance(wav_bytes, bytes)
    assert wav_bytes[:4] == b"RIFF"  # WAV 文件头
    assert segments == [(0.0, 1.0)]


def test_synthesize_invokes_progress_callback(monkeypatch):
    from app.core import chat_tts
    chat_tts._MODEL = None

    def fake_infer(params, text):
        return np.zeros(24000, dtype=np.float32), [(0.0, 1.0)]

    monkeypatch.setattr("app.core.chat_tts._infer_audio", fake_infer)

    calls = []
    def on_progress(p):
        calls.append(p)

    params = TtsParams(seed=1, speaker="x")
    synthesize_to_wav_bytes(params, text="hi", on_progress=on_progress)

    # 至少有一次进度回调被调用（具体次数不重要，验证回调通路）
    assert len(calls) >= 1
    assert calls[-1] == 1.0  # 最终进度 = 1.0
