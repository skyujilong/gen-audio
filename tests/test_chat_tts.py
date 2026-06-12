import numpy as np
import pytest

from app.core.params import TtsParams
from app.core.chat_tts import draw_one, draw_one_from_params, synthesize_to_wav_bytes, is_model_loaded


def test_is_model_loaded_initially_false(monkeypatch):
    """在不真正加载模型的情况下，初始状态应为 False。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    assert is_model_loaded() is False


def test_draw_one_returns_valid_params(monkeypatch):
    """不真正加载模型；patch 掉随机来源。draw_one 只随机 seed 和 speaker，其余用默认值。"""
    from app.core import chat_tts
    chat_tts._MODEL = None

    # patch random 函数让它返回确定值
    monkeypatch.setattr("app.core.chat_tts._random_int", lambda lo, hi: 12345)
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "BASE64SPEAKER")

    params = draw_one(refiner_text=None)
    assert isinstance(params, TtsParams)
    assert params.seed == 12345
    assert params.temperature == 0.3   # 默认值
    assert params.top_p == 0.7         # 默认值
    assert params.top_k == 20          # 默认值
    assert params.speaker == "BASE64SPEAKER"
    assert params.refiner_text is None
    assert params.repetition_penalty == 1.05
    assert params.speed == "[speed_5]"
    assert params.skip_refine_text is False
    assert params.max_new_token == 2048
    assert params.spk_smp is None
    assert params.txt_smp is None


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


def test_draw_one_from_params_seed_none_random(monkeypatch):
    """seed=None 时随机生成 seed。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    monkeypatch.setattr("app.core.chat_tts._random_int", lambda lo, hi: 999)
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "SPK")

    params = draw_one_from_params(seed=None, temperature=0.5, top_p=0.8, top_k=15)
    assert params.seed == 999
    assert params.temperature == 0.5
    assert params.top_p == 0.8
    assert params.top_k == 15
    assert params.speaker == "SPK"


def test_draw_one_from_params_seed_given(monkeypatch):
    """seed 有值时保留传入值，不随机。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "SPK")

    params = draw_one_from_params(seed=42, temperature=0.3, top_p=0.7, top_k=20)
    assert params.seed == 42
    assert params.temperature == 0.3
    assert params.speaker == "SPK"


def test_draw_one_from_params_speaker_given(monkeypatch):
    """speaker 有值时保留传入值，不随机。"""
    from app.core import chat_tts
    chat_tts._MODEL = None

    params = draw_one_from_params(seed=1, speaker="MY_SPEAKER")
    assert params.speaker == "MY_SPEAKER"


def test_draw_one_from_params_refiner_text(monkeypatch):
    """refiner_text 传入时保留。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    monkeypatch.setattr("app.core.chat_tts._random_int", lambda lo, hi: 1)
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "SPK")

    params = draw_one_from_params(refiner_text="[oral_2]")
    assert params.refiner_text == "[oral_2]"


def test_draw_one_from_params_new_fields(monkeypatch):
    """新增字段（repetition_penalty/speed/skip_refine_text/max_new_token/spk_smp/txt_smp）传入时保留。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    monkeypatch.setattr("app.core.chat_tts._random_int", lambda lo, hi: 1)
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "SPK")

    params = draw_one_from_params(
        seed=42,
        temperature=0.5,
        top_p=0.8,
        top_k=10,
        speaker="MYSPK",
        repetition_penalty=1.5,
        speed="[speed_3]",
        skip_refine_text=True,
        max_new_token=1024,
        spk_smp="REF_AUDIO_B64",
        txt_smp="参考文本",
    )
    assert params.repetition_penalty == 1.5
    assert params.speed == "[speed_3]"
    assert params.skip_refine_text is True
    assert params.max_new_token == 1024
    assert params.spk_smp == "REF_AUDIO_B64"
    assert params.txt_smp == "参考文本"


def test_tts_params_new_fields_defaults():
    """TtsParams 新字段有正确默认值。"""
    params = TtsParams(seed=1, speaker="x")
    assert params.repetition_penalty == 1.05
    assert params.speed == "[speed_5]"
    assert params.skip_refine_text is False
    assert params.max_new_token == 2048
    assert params.spk_smp is None
    assert params.txt_smp is None
