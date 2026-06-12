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
    assert params.speed == 5
    assert params.skip_refine_text is False
    assert params.max_new_token == 2048
    assert params.spk_smp is None
    assert params.txt_smp is None
    # Phase 1.2: 新增 3 个整数字段
    assert params.oral == 0
    assert params.laugh == 0
    assert params.break_ == 0


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
        speed=3,
        skip_refine_text=True,
        max_new_token=1024,
        spk_smp="REF_AUDIO_B64",
        txt_smp="参考文本",
    )
    assert params.repetition_penalty == 1.5
    assert params.speed == 3
    assert params.skip_refine_text is True
    assert params.max_new_token == 1024
    assert params.spk_smp == "REF_AUDIO_B64"
    assert params.txt_smp == "参考文本"


def test_tts_params_new_fields_defaults():
    """TtsParams 新字段有正确默认值。"""
    params = TtsParams(seed=1, speaker="x")
    assert params.repetition_penalty == 1.05
    assert params.speed == 5
    assert params.skip_refine_text is False
    assert params.max_new_token == 2048
    assert params.spk_smp is None
    assert params.txt_smp is None
    # Phase 1.2: oral/laugh/break_ 默认 0
    assert params.oral == 0
    assert params.laugh == 0
    assert params.break_ == 0


# === Phase 2.2: _build_infer_code_params ===

def test_build_infer_code_params_speed_5_prompts_speed_token(monkeypatch):
    """speed=5 → prompt='[speed_5]'。"""
    from app.core import chat_tts
    # 避免 import ChatTTS（避免加载模型）
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            self.kw = kw
    # 注入假类到 chat_tts 命名空间
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(seed=42, speaker="x", speed=5)
    infer_params = chat_tts._build_infer_code_params(params)
    assert infer_params.kw["prompt"] == "[speed_5]"


def test_build_infer_code_params_speed_3_prompts_speed_3(monkeypatch):
    from app.core import chat_tts
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            self.kw = kw
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(seed=1, speaker="x", speed=3)
    infer_params = chat_tts._build_infer_code_params(params)
    assert infer_params.kw["prompt"] == "[speed_3]"


def test_build_infer_code_params_maps_all_tuning(monkeypatch):
    """temperature/top_p/top_k/repetition_penalty/seed/speaker 都应透传。"""
    from app.core import chat_tts
    captured = {}
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(
        seed=42, speaker="MYSPK",
        temperature=0.5, top_p=0.8, top_k=15,
        repetition_penalty=1.2, max_new_token=1024,
    )
    chat_tts._build_infer_code_params(params)
    assert captured["top_P"] == 0.8
    assert captured["top_K"] == 15
    assert captured["temperature"] == 0.5
    assert captured["repetition_penalty"] == 1.2
    assert captured["max_new_token"] == 1024
    assert captured["manual_seed"] == 42
    assert captured["spk_emb"] == "MYSPK"
    assert captured["prompt"] == "[speed_5]"


def test_build_infer_code_params_seed_zero_uses_none(monkeypatch):
    """seed=0 → manual_seed=None（让 ChatTTS 自己随机）。"""
    from app.core import chat_tts
    captured = {}
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(seed=0, speaker="x")
    chat_tts._build_infer_code_params(params)
    assert captured["manual_seed"] is None


def test_build_infer_code_params_voice_clone_args(monkeypatch):
    """spk_smp / txt_smp 有值时透传。"""
    from app.core import chat_tts
    captured = {}
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(
        seed=1, speaker="x",
        spk_smp="REF_B64", txt_smp="参考文本",
    )
    chat_tts._build_infer_code_params(params)
    assert captured["spk_smp"] == "REF_B64"
    assert captured["txt_smp"] == "参考文本"


# === Phase 2.3 → Phase 6.x: _build_refine_text_params ===
#
# Phase 6.x 关键改动：[oral_X][laugh_X][break_X] 不再走 refine 阶段（实测 GPT 塌缩），
# 而是塞到 infer_code.prompt 前缀里。本函数现在只在显式 refiner_text 时返回非 None。

def test_build_refine_text_params_free_text_used(monkeypatch):
    """refiner_text 非空时走 refine 阶段（自由文本 prompt）。"""
    from app.core import chat_tts
    captured = {}
    class _FakeRefineTextParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatRefineTextParams", _FakeRefineTextParams, raising=False)

    params = TtsParams(seed=1, speaker="x", refiner_text="[oral_5][laugh_2]")
    chat_tts._build_refine_text_params(params)
    assert captured["prompt"] == "[oral_5][laugh_2]"


def test_build_refine_text_params_returns_none_when_no_refiner_text(monkeypatch):
    """refiner_text 为空时直接返回 None（oral/laugh/break_ 由 infer_code 处理）。"""
    from app.core import chat_tts
    params = TtsParams(seed=1, speaker="x", oral=3, laugh=2, break_=7)
    assert chat_tts._build_refine_text_params(params) is None


def test_build_refine_text_params_all_zero_returns_none(monkeypatch):
    """refiner_text 空 → 返回 None（不精炼）。"""
    from app.core import chat_tts
    params = TtsParams(seed=1, speaker="x")
    assert chat_tts._build_refine_text_params(params) is None


# === Phase 6.x: _build_infer_code_params 把 oral/laugh/break_ 拼到 prompt ===

def test_build_infer_code_params_includes_oral_laugh_break_in_prompt(monkeypatch):
    """[oral_X][laugh_X][break_X] 应拼到 infer_code.prompt 前缀（[speed_X] 之后）。"""
    from app.core import chat_tts
    captured = {}
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(seed=1, speaker="x", speed=6, oral=5, laugh=3, break_=4)
    chat_tts._build_infer_code_params(params)
    assert captured["prompt"] == "[speed_6][oral_5][laugh_3][break_4]"


def test_build_infer_code_params_partial_ints_in_prompt(monkeypatch):
    """oral/laugh/break_ 部分为 0 时只拼非零部分。"""
    from app.core import chat_tts
    captured = {}
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(seed=1, speaker="x", speed=5, oral=2, laugh=0, break_=4)
    chat_tts._build_infer_code_params(params)
    assert captured["prompt"] == "[speed_5][oral_2][break_4]"


def test_build_infer_code_params_no_ints(monkeypatch):
    """oral/laugh/break_ 都为 0 时 prompt 只含 [speed_X]。"""
    from app.core import chat_tts
    captured = {}
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)

    params = TtsParams(seed=1, speaker="x", speed=5)
    chat_tts._build_infer_code_params(params)
    assert captured["prompt"] == "[speed_5]"


def test_build_refine_text_params_includes_tuning(monkeypatch):
    """RefineTextParams 应补 top_P/top_K/temperature（refiner_text 模式下）。"""
    from app.core import chat_tts
    captured = {}
    class _FakeRefineTextParams:
        def __init__(self, **kw):
            captured.update(kw)
    monkeypatch.setattr(chat_tts, "_ChatRefineTextParams", _FakeRefineTextParams, raising=False)

    # Phase 6.x：必须设 refiner_text 才会走 refine
    params = TtsParams(seed=1, speaker="x", temperature=0.5, top_p=0.9, top_k=30,
                       refiner_text="[oral_2]")
    chat_tts._build_refine_text_params(params)
    assert captured["top_P"] == 0.9
    assert captured["top_K"] == 30
    assert captured["temperature"] == 0.7  # 固定 refine 默认温度
    assert captured["prompt"] == "[oral_2]"


# === Phase 2.4: 两步范式 _refine_text + _synthesize_audio ===

def test_refine_text_returns_text_when_model_not_loaded(monkeypatch):
    """模型未加载时 _refine_text 直接返回原 text。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    params = TtsParams(seed=1, speaker="x", oral=2)
    out = chat_tts._refine_text(params, "你好世界")
    assert out == "你好世界"


def test_synthesize_audio_returns_silence_when_model_not_loaded(monkeypatch):
    """模型未加载时 _synthesize_audio 返回静音。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    params = TtsParams(seed=1, speaker="x")
    audio, segments = chat_tts._synthesize_audio(params, "hi")
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert len(audio) == 24000  # 1 秒静音


def test_infer_audio_skip_refine_skips_refine(monkeypatch):
    """skip_refine_text=True 时，_infer_audio 不调 _refine_text。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    refine_called = []
    synth_called = []

    def fake_refine(params, text):
        refine_called.append(text)
        return "REFINED"

    def fake_synth(params, text):
        synth_called.append(text)
        return np.zeros(24000, dtype=np.float32), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_refine_text", fake_refine)
    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    params = TtsParams(seed=1, speaker="x", skip_refine_text=True)
    chat_tts._infer_audio(params, "hi")
    assert refine_called == []  # 不调
    assert synth_called == ["hi"]  # 直接 synthesize


def test_infer_audio_two_step_calls_refine_then_synth(monkeypatch):
    """正常两步：_refine_text 先调 → _synthesize_audio 用 refined_text。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    call_order = []

    def fake_refine(params, text):
        call_order.append(("refine", text))
        return text + "-REFINED"

    def fake_synth(params, text):
        call_order.append(("synth", text))
        return np.zeros(24000, dtype=np.float32), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_refine_text", fake_refine)
    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    params = TtsParams(seed=1, speaker="x", oral=2)  # skip_refine_text=False
    chat_tts._infer_audio(params, "hi")
    assert call_order == [("refine", "hi"), ("synth", "hi-REFINED")]


def test_refine_text_protects_tokens(monkeypatch):
    """_refine_text 调真实模型时，应先 replace_tokens 保护 control token。

    实现说明：这版 ChatTTS 没有独立 `refine_text()`，refine 走统一的
    `infer(..., refine_text_only=True)`，收到 `list[str]`。
    """
    from app.core import chat_tts
    chat_tts._MODEL = None
    # 模拟有模型，但只检查 infer 收到的 text
    refine_received = []

    class _FakeModel:
        def has_loaded(self): return True
        def infer(self, text, **kwargs):
            # 只在 refine_text_only=True 时捕获
            if kwargs.get("refine_text_only"):
                refine_received.append(text)
                return [text]  # list[str]
            raise RuntimeError("not expected in this test")

    chat_tts._MODEL = _FakeModel()
    # Phase 6.x：mock 出来一个带 .prompt / .top_P / .top_K / .temperature 的对象
    class _FakeRefine:
        prompt = "MOCK_PROMPT"
        top_P = 0.7
        top_K = 20
        temperature = 0.7
    monkeypatch.setattr(chat_tts, "_build_refine_text_params", lambda p: _FakeRefine())

    params = TtsParams(seed=1, speaker="x", refiner_text="x")  # refiner_text 必须非空才会被 build
    chat_tts._refine_text(params, "你好[oral_2]世界[laugh_1]")
    # 收到的应该是替换后的文本（无 [oral_2] / [laugh_1]）
    assert "[oral_2]" not in refine_received[0]
    assert "[laugh_1]" not in refine_received[0]
    assert "你好" in refine_received[0]
    assert "世界" in refine_received[0]


# === Phase 2.6.3: _numpy_to_wav_bytes 可变 sample_rate + synthesize 接入增强 ===

def test_numpy_to_wav_bytes_default_24k(monkeypatch):
    """默认 24000Hz 头部。"""
    from app.core import chat_tts
    audio = np.zeros(24000, dtype=np.float32)
    wav = chat_tts._numpy_to_wav_bytes(audio)
    assert wav[:4] == b"RIFF"
    # sample rate 字段在 24-27 字节（大端）
    import struct
    sr = struct.unpack("<I", wav[24:28])[0]
    assert sr == 24000


def test_numpy_to_wav_bytes_44k():
    """传入 sample_rate=44100 → 头部 44100Hz。"""
    from app.core import chat_tts
    audio = np.zeros(44100, dtype=np.float32)
    wav = chat_tts._numpy_to_wav_bytes(audio, sample_rate=44100)
    assert wav[:4] == b"RIFF"
    import struct
    sr = struct.unpack("<I", wav[24:28])[0]
    assert sr == 44100


def test_synthesize_skips_enhance_when_no_flags(monkeypatch):
    """enhance_audio/denoise_audio 都 False 时，synthesize 不调 run_enhance。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    enhance_called = []

    def fake_run_enhance(*a, **kw):
        enhance_called.append((a, kw))
        return np.zeros(44100, dtype=np.float32), 44100

    monkeypatch.setattr(chat_tts, "run_enhance", fake_run_enhance)

    params = TtsParams(seed=1, speaker="x", enhance_audio=False, denoise_audio=False)
    wav, segments = chat_tts.synthesize_to_wav_bytes(params, "hi")
    assert enhance_called == []  # 不调
    assert isinstance(wav, bytes)


def test_synthesize_calls_enhance_when_denoise(monkeypatch):
    """denoise_audio=True → 调 run_enhance(denoise=True, enhance=False, ...)。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    enhance_called = []

    def fake_run_enhance(audio, sr, **kw):
        enhance_called.append(kw)
        return np.zeros(44100, dtype=np.float32), 44100

    monkeypatch.setattr(chat_tts, "run_enhance", fake_run_enhance)

    params = TtsParams(
        seed=1, speaker="x",
        denoise_audio=True, solver="rk4", nfe=32, tau=0.7,
    )
    wav, segments = chat_tts.synthesize_to_wav_bytes(params, "hi")
    assert len(enhance_called) == 1
    assert enhance_called[0]["denoise"] is True
    assert enhance_called[0]["enhance"] is False
    assert enhance_called[0]["solver"] == "rk4"
    assert enhance_called[0]["nfe"] == 32
    assert enhance_called[0]["tau"] == 0.7
    # 输出 wav 应是 44100Hz（增强后）
    import struct
    sr = struct.unpack("<I", wav[24:28])[0]
    assert sr == 44100


def test_synthesize_calls_enhance_when_enhance_audio(monkeypatch):
    """enhance_audio=True → 调 run_enhance(denoise=False, enhance=True, ...)。"""
    from app.core import chat_tts
    chat_tts._MODEL = None

    def fake_run_enhance(audio, sr, **kw):
        return np.zeros(44100, dtype=np.float32), 44100

    captured = {}
    def fake_capture(audio, sr, **kw):
        captured.update(kw)
        return np.zeros(44100, dtype=np.float32), 44100

    monkeypatch.setattr(chat_tts, "run_enhance", fake_capture)

    params = TtsParams(seed=1, speaker="x", enhance_audio=True)
    chat_tts.synthesize_to_wav_bytes(params, "hi")
    assert captured["denoise"] is False
    assert captured["enhance"] is True


def test_synthesize_returns_44k_wav_after_enhance(monkeypatch):
    """增强后 wav 字节流的 sr 字段应为 44100。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    monkeypatch.setattr(chat_tts, "run_enhance",
                        lambda audio, sr, **kw: (np.zeros(44100, dtype=np.float32), 44100))

    params = TtsParams(seed=1, speaker="x", denoise_audio=True)
    wav, _ = chat_tts.synthesize_to_wav_bytes(params, "hi")
    import struct
    sr = struct.unpack("<I", wav[24:28])[0]
    assert sr == 44100


def test_synthesize_progress_callback_fires_with_enhance(monkeypatch):
    """增强开启时，进度回调应至少触发 [0.0, ..., 1.0]。"""
    from app.core import chat_tts
    chat_tts._MODEL = None
    monkeypatch.setattr(chat_tts, "run_enhance",
                        lambda audio, sr, **kw: (np.zeros(44100, dtype=np.float32), 44100))

    calls = []
    params = TtsParams(seed=1, speaker="x", denoise_audio=True)
    chat_tts.synthesize_to_wav_bytes(params, "hi", on_progress=lambda p: calls.append(p))
    assert calls[0] == 0.0
    assert calls[-1] == 1.0


# === Phase 2.6.4: draw 试听强制不增强 ===

def test_draw_one_from_params_skips_enhance(monkeypatch):
    """draw_one_from_params 返回的 TtsParams 即便带 enhance_audio 也不影响试听。

    试听不调 run_enhance（验证在 draw 路由层做，这里只验证 draw_one 仍正常返回）。
    """
    from app.core import chat_tts
    chat_tts._MODEL = None
    monkeypatch.setattr("app.core.chat_tts._random_int", lambda lo, hi: 1)
    monkeypatch.setattr("app.core.chat_tts._random_speaker", lambda: "X")

    params = chat_tts.draw_one_from_params(
        seed=42, speaker="x", denoise_audio=True, enhance_audio=True,
    )
    # 即便带增强参数，draw_one 也能正常返回（试听由 draw 路由强制跳过）
    assert params.denoise_audio is True
    assert params.enhance_audio is True


def test_synthesize_actually_does_skip_enhance(monkeypatch):
    """直接调 synthesize_to_wav_bytes + 增强参数 → 调 run_enhance。

    这是反向验证：synthesize 调增强（draw 路由需要主动控制是否调）。
    """
    from app.core import chat_tts
    chat_tts._MODEL = None
    enhance_called = []
    def fake_enhance(audio, sr, **kw):
        enhance_called.append(kw)
        return np.zeros(44100, dtype=np.float32), 44100
    monkeypatch.setattr(chat_tts, "run_enhance", fake_enhance)

    params = TtsParams(seed=1, speaker="x", denoise_audio=True)
    chat_tts.synthesize_to_wav_bytes(params, "hi")
    assert len(enhance_called) == 1  # synthesize 会调
