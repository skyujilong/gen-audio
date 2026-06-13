"""Phase 7：长文本切分 + 单段重试 + 塌缩检测的集成测试。

为了在没有真实 ChatTTS 模型的情况下走 chunk 流水线，测试用一个 stub `_FakeModel`
模拟 `_MODEL` —— 提供 `has_loaded()` / `sample_random_speaker()` —— 然后 mock
`_synthesize_audio` 来控制每次推理的输出，绕过 `_build_infer_code_params` 对真实
`ChatTTS.Chat.InferCodeParams` 类的依赖。

覆盖：
- TN → chunk → 多段串行 infer 调用
- 强制 `skip_refine_text=True`：即使传 `refiner_text` 也不走 refine
- `_MODEL.infer` 应带 `split_text=False`（间接验证：mock `_synthesize_audio` 触发，但
  我们在 `test_split_text_false_passed_to_infer` 用 `_FakeModel.infer` 直接捕获）
- 单段塌缩 → 重试 → 成功
- 单段重试耗尽 → 抛 ChunkSynthesisError，错误信息含段索引 + 段文本 + 原因
- seed=0 → 提前固定为非 0
- 进度回调按 `(i+1)/n*0.6` + `0.95` + `1.0` 触发
- segments 时间戳与拼接结果对齐
- 段数超 TEXT_CHUNK_MAX_SEGMENTS → 抛 ValueError
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import numpy as np
import pytest

from app import config
from app.core import chat_tts
from app.core.chat_tts import ChunkSynthesisError, synthesize_to_wav_bytes
from app.core.params import TtsParams


# === 公共工具 ===

class _FakeModel:
    """模拟 ChatTTS.Chat 实例：提供 has_loaded / sample_random_speaker /
    infer，让 chat_tts 走「模型已加载」分支进入 chunk 流水线。
    """

    def __init__(self):
        self.has_loaded_value = True
        self.infer_calls: list[dict] = []  # 记录每次 _MODEL.infer 调用的 kwargs
        self._random_spk_returns = "FAKE_SAMPLED_SPEAKER"
        # Phase 8: spk_smp 二段法相关
        self.sample_audio_calls: list[np.ndarray] = []
        self.sample_audio_returns = "REF_SMP_FAKE"  # str 或 callable(wav)->str
        self.sample_audio_raises: Exception | None = None

    def has_loaded(self) -> bool:
        return self.has_loaded_value

    def sample_random_speaker(self) -> str:
        return self._random_spk_returns

    def sample_audio_speaker(self, wav):
        self.sample_audio_calls.append(wav)
        if self.sample_audio_raises is not None:
            raise self.sample_audio_raises
        if callable(self.sample_audio_returns):
            return self.sample_audio_returns(wav)
        return self.sample_audio_returns

    def infer(self, text, **kwargs):
        # 默认返回 1s 单声道有声 wav（防塌缩）
        self.infer_calls.append({"text": text, **kwargs})
        return [np.full(24000, 0.3, dtype=np.float32)]


@pytest.fixture
def fake_model(monkeypatch):
    """注入 _FakeModel 到 chat_tts._MODEL，并 patch ChatTTS 类避免真实 import。"""
    fm = _FakeModel()
    monkeypatch.setattr(chat_tts, "_MODEL", fm)

    # 注入 fake InferCodeParams / RefineTextParams（避免 import 真实 ChatTTS）
    class _FakeInferCodeParams:
        def __init__(self, **kw):
            self.kw = kw

    class _FakeRefineTextParams:
        def __init__(self, **kw):
            self.kw = kw

    monkeypatch.setattr(chat_tts, "_ChatInferCodeParams", _FakeInferCodeParams, raising=False)
    monkeypatch.setattr(chat_tts, "_ChatRefineTextParams", _FakeRefineTextParams, raising=False)

    return fm


@pytest.fixture(autouse=True)
def _disable_text_norm(monkeypatch):
    """全部测试默认关 TN，避免依赖未装的 WeTextProcessing。
    单测 normalize 行为放在 test_text_norm.py。
    """
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", False)


def _good_audio(samples: int = 24000, level: float = 0.3) -> np.ndarray:
    """生成「足够响」的音频，绕过塌缩检测。"""
    return np.full(samples, level, dtype=np.float32)


def _silent_audio(samples: int = 24000) -> np.ndarray:
    """全静音 → 必被判定为塌缩。"""
    return np.zeros(samples, dtype=np.float32)


# === 1. 多段串行调 _synthesize_audio ===

def test_multi_chunks_serially_invoke_synth(fake_model, monkeypatch):
    """长文本切成多段 → 每段独立调一次 _synthesize_audio。"""
    calls = []

    def fake_synth(params, text):
        calls.append(text)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    text = "第一句话。第二句话。第三句话。"
    params = TtsParams(seed=42, speaker="X")
    wav_bytes, segments = synthesize_to_wav_bytes(params, text)

    # 应有 3 次调用，文本对齐切分结果
    assert len(calls) == 3
    assert calls == ["第一句话。", "第二句话。", "第三句话。"]
    # segments 也是 3 条，且元素是 (text, start, end) 三元组
    assert len(segments) == 3
    for (chunk_text, start, end), expected in zip(segments, calls):
        assert chunk_text == expected
        assert start < end
    # WAV 格式正确
    assert wav_bytes[:4] == b"RIFF"


def test_split_text_false_passed_to_real_infer(fake_model, monkeypatch):
    """_synthesize_audio（真版）调 _MODEL.infer 时必须带 split_text=False。

    我们直接用 _FakeModel.infer 捕获参数，不 mock _synthesize_audio。
    """
    text = "你好世界。再见。"
    params = TtsParams(seed=1, speaker="X")
    synthesize_to_wav_bytes(params, text)

    # 切成 2 段 → 至少 2 次 infer 调用
    assert len(fake_model.infer_calls) >= 1
    for call in fake_model.infer_calls:
        assert call.get("split_text") is False, f"call missing split_text=False: {call}"
        # 同时应带 skip_refine_text=True
        assert call.get("skip_refine_text") is True


# === 2. 强制 skip_refine_text=True，忽略 refiner_text ===

def test_refiner_text_is_ignored(fake_model, monkeypatch):
    """即使 params 带 refiner_text，pipeline 也强制忽略（不走 refine）。"""
    refine_called = []

    def fake_refine(params, text):
        refine_called.append(text)
        return text + "-REFINED"

    def fake_synth(params, text):
        # 验证 params 已被改成 skip_refine_text=True + refiner_text=None
        assert params.skip_refine_text is True
        assert params.refiner_text is None
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_refine_text", fake_refine)
    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    params = TtsParams(seed=1, speaker="X", refiner_text="[oral_5]想要的风格")
    synthesize_to_wav_bytes(params, "短句一。短句二。")

    # _refine_text 不应被调用
    assert refine_called == []


# === 3. 单段塌缩 → 重试 → 成功 ===

def test_collapse_then_retry_recovers(fake_model, monkeypatch):
    """第 N 段第一次塌缩、第二次成功 → 整体成功。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_RETRIES", 2)
    monkeypatch.setattr(config, "TEXT_CHUNK_COLLAPSE_RATIO", 0.05)

    call_count = {"n": 0}

    def fake_synth(params, text):
        call_count["n"] += 1
        # 第一次塌缩、第二次成功
        if call_count["n"] == 1:
            return _silent_audio(), [(0.0, 1.0)]
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    params = TtsParams(seed=100, speaker="X")
    wav_bytes, segments = synthesize_to_wav_bytes(params, "只有一段话。")

    # 跑了 2 次（1 塌 + 1 成功）
    assert call_count["n"] == 2
    assert len(segments) == 1
    assert wav_bytes[:4] == b"RIFF"


def test_retry_uses_different_seed(fake_model, monkeypatch):
    """每次重试 seed = base_seed + attempt（让 ChatTTS 切换到不同 GPT 路径）。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_RETRIES", 3)
    monkeypatch.setattr(config, "TEXT_CHUNK_COLLAPSE_RATIO", 0.05)

    seeds_seen: list[int] = []
    call_count = {"n": 0}

    def fake_synth(params, text):
        seeds_seen.append(params.seed)
        call_count["n"] += 1
        # 前 2 次塌缩，第 3 次成功
        if call_count["n"] < 3:
            return _silent_audio(), [(0.0, 1.0)]
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    params = TtsParams(seed=1000, speaker="X")
    synthesize_to_wav_bytes(params, "一段话。")

    # 应看到 base, base+1, base+2 三个不同 seed
    assert seeds_seen == [1000, 1001, 1002]


# === 4. 重试耗尽 → ChunkSynthesisError ===

def test_chunk_exhausts_retries_raises(fake_model, monkeypatch):
    """单段全部重试都塌缩 → 抛 ChunkSynthesisError，含段索引 + 段文本 + 原因。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_RETRIES", 2)
    monkeypatch.setattr(config, "TEXT_CHUNK_COLLAPSE_RATIO", 0.05)

    # 总是返回静音 → 必塌缩
    monkeypatch.setattr(
        chat_tts, "_synthesize_audio",
        lambda p, t: (_silent_audio(), [(0.0, 1.0)]),
    )

    params = TtsParams(seed=42, speaker="X")
    with pytest.raises(ChunkSynthesisError) as exc:
        synthesize_to_wav_bytes(params, "第一段。第二段。第三段。")

    msg = str(exc.value)
    # 错误信息应能让人定位「哪一段、哪句话、什么原因」
    assert "第 1/3 段" in msg, msg  # 第一段就失败了，停在 1
    assert "重试 3 次" in msg, msg  # max_retries=2 → 总共 3 次（1 + 2 重试）
    assert "collapse" in msg, msg
    assert "第一段" in msg, msg


def test_chunk_infer_exception_is_caught_and_retried(fake_model, monkeypatch):
    """_synthesize_audio 抛 RuntimeError 也走重试流程，重试耗尽 → ChunkSynthesisError。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_RETRIES", 1)

    def boom(params, text):
        raise RuntimeError("CUDA OOM")

    monkeypatch.setattr(chat_tts, "_synthesize_audio", boom)

    params = TtsParams(seed=1, speaker="X")
    with pytest.raises(ChunkSynthesisError) as exc:
        synthesize_to_wav_bytes(params, "一段话。")
    assert "CUDA OOM" in str(exc.value)
    assert "infer error" in str(exc.value)


def test_second_chunk_failure_reports_correct_index(fake_model, monkeypatch):
    """第二段才失败 → 错误信息显示「第 2/3 段」。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_RETRIES", 1)
    monkeypatch.setattr(config, "TEXT_CHUNK_COLLAPSE_RATIO", 0.05)

    counter = {"n": 0}

    def fake_synth(params, text):
        counter["n"] += 1
        # 第一段（前 2 次调用算第一段或重试）成功；第二段必败
        if "第二段" in text:
            return _silent_audio(), [(0.0, 1.0)]
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    with pytest.raises(ChunkSynthesisError) as exc:
        synthesize_to_wav_bytes(
            TtsParams(seed=1, speaker="X"),
            "第一段。第二段。第三段。",
        )
    assert "第 2/3 段" in str(exc.value)
    assert "第二段" in str(exc.value)


# === 5. seed=0 提前固定 ===

def test_seed_zero_is_fixed_to_nonzero(fake_model, monkeypatch):
    """seed=0 时进 chunk 前应被固定为非 0，所有 chunk 共用同一 base_seed。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_RETRIES", 0)

    # 强制 _random_int 返回确定值，便于断言
    monkeypatch.setattr(chat_tts, "_random_int", lambda lo, hi: 12345)

    seeds_seen: list[int] = []

    def fake_synth(params, text):
        seeds_seen.append(params.seed)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    params = TtsParams(seed=0, speaker="X")  # seed=0
    synthesize_to_wav_bytes(params, "第一段。第二段。")

    # 各段（attempt=0）拿到的 seed 都 = 12345（fixed seed）
    assert seeds_seen == [12345, 12345]


def test_seed_nonzero_kept_as_base(fake_model, monkeypatch):
    """seed 非 0 时不动。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_RETRIES", 0)

    seeds_seen: list[int] = []

    def fake_synth(params, text):
        seeds_seen.append(params.seed)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=999, speaker="X"),
        "第一段。第二段。",
    )
    assert seeds_seen == [999, 999]


# === 6. 进度回调 ===

def test_progress_callback_pattern(fake_model, monkeypatch):
    """进度按 (i+1)/n*0.6 触发，最后到 0.95 + 1.0。"""
    monkeypatch.setattr(
        chat_tts, "_synthesize_audio",
        lambda p, t: (_good_audio(), [(0.0, 1.0)]),
    )

    calls: list[float] = []
    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "第一段。第二段。第三段。第四段。",  # 4 段
        on_progress=calls.append,
    )

    # 0.0 起点 + 4 段进度（0.15, 0.3, 0.45, 0.6）+ 0.95 + 1.0
    assert calls[0] == 0.0
    assert calls[-1] == 1.0
    # 中间应包含 4 个递增进度（不超过 0.6）
    chunks_progress = [c for c in calls if 0 < c <= 0.6]
    assert len(chunks_progress) == 4
    assert chunks_progress == sorted(chunks_progress)
    # 每个进度值大致是 i/4 * 0.6
    for i, p in enumerate(chunks_progress, start=1):
        assert p == pytest.approx(i / 4 * 0.6)


# === 7. SRT segments 时间戳与拼接对齐 ===

def test_segments_align_with_concat_times(fake_model, monkeypatch):
    """segments[i] 的 (start, end) 应与 audio_concat 的累加时间一致，**不含**段间静音。

    构造 3 段：每段 0.5s 音频；段间静音 0.12s。
    预期 segments = [(0.0, 0.5), (0.62, 1.12), (1.24, 1.74)]
    （注意第二段 start = 0.5 + 0.12，第三段 start = 0.5 + 0.12 + 0.5 + 0.12）
    """
    monkeypatch.setattr(config, "TEXT_CHUNK_PAUSE_SEC", 0.12)

    audio_05s = _good_audio(samples=12000)  # 0.5s @ 24kHz

    def fake_synth(params, text):
        return audio_05s, [(0.0, 0.5)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    _, segments = synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "第一段。第二段。第三段。",
    )

    assert len(segments) == 3
    # 每段长度 ≈ 0.5s
    for chunk_text, start, end in segments:
        assert end - start == pytest.approx(0.5, abs=0.001)
    # 第一段从 0 开始
    assert segments[0][1] == pytest.approx(0.0, abs=0.001)
    assert segments[0][2] == pytest.approx(0.5, abs=0.001)
    # 第二段紧接 0.12s 静音 → start = 0.5 + 0.12 = 0.62
    assert segments[1][1] == pytest.approx(0.62, abs=0.001)
    assert segments[1][2] == pytest.approx(1.12, abs=0.001)
    # 第三段 start = 1.12 + 0.12
    assert segments[2][1] == pytest.approx(1.24, abs=0.001)
    assert segments[2][2] == pytest.approx(1.74, abs=0.001)


# === 8. 段数上限 ===

def test_too_many_chunks_raises_value_error(fake_model, monkeypatch):
    """切完段数 > TEXT_CHUNK_MAX_SEGMENTS → 抛 ValueError。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_MAX_SEGMENTS", 3)
    monkeypatch.setattr(
        chat_tts, "_synthesize_audio",
        lambda p, t: (_good_audio(), [(0.0, 1.0)]),
    )

    # 5 段（> 3）
    text = "第一句。第二句。第三句。第四句。第五句。"
    with pytest.raises(ValueError) as exc:
        synthesize_to_wav_bytes(TtsParams(seed=1, speaker="X"), text)

    assert "5 段" in str(exc.value) or "5段" in str(exc.value)
    assert "3" in str(exc.value)


# === 9. 空文本 / 全标点输入 → 1s 静音占位 ===

def test_empty_text_returns_silence_placeholder(fake_model, monkeypatch):
    """切完 0 段（如纯标点 / 空文本）→ 1s 静音占位 + 空 segments。"""
    synth_called = []

    def fake_synth(p, t):
        synth_called.append(t)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    wav_bytes, segments = synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "。。。、，",  # 全标点
    )
    # 不应调 _synthesize_audio
    assert synth_called == []
    # 仍返回有效 WAV + 空 segments
    assert wav_bytes[:4] == b"RIFF"
    assert segments == []


# === 10. speaker 空 → 提前 sample 一次 ===

def test_empty_speaker_sampled_once(fake_model, monkeypatch):
    """speaker 空 → 一次 sample，所有 chunk 共用。"""
    fake_model._random_spk_returns = "SAMPLED_SPK_X"

    seen_speakers: list[str] = []

    def fake_synth(params, text):
        seen_speakers.append(params.speaker)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker=""),  # 空 speaker
        "第一段。第二段。第三段。",
    )

    # 3 段，全用同一 sampled speaker
    assert seen_speakers == ["SAMPLED_SPK_X"] * 3


def test_explicit_speaker_not_resampled(fake_model, monkeypatch):
    """speaker 非空时不重采样。"""
    fake_model._random_spk_returns = "DO_NOT_USE"

    seen_speakers: list[str] = []

    def fake_synth(params, text):
        seen_speakers.append(params.speaker)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="EXPLICIT_SPK"),
        "第一段。第二段。",
    )
    assert seen_speakers == ["EXPLICIT_SPK", "EXPLICIT_SPK"]


# === 11. TN 在切分之前 ===

def test_tn_runs_before_chunking(fake_model, monkeypatch):
    """text_norm.normalize_text 在 split_text 之前调一次（整段）；切分作用于规范化结果。"""
    # 开 TN，注入 fake normalizer
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)

    from app.core import text_norm
    monkeypatch.setattr(text_norm, "_normalizer", MagicMock(
        normalize=lambda s: s.replace("1998", "一九九八")
    ))
    monkeypatch.setattr(text_norm, "_status", "ok")

    seen_chunks: list[str] = []

    def fake_synth(params, text):
        seen_chunks.append(text)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "我出生于1998年。",
    )

    # 数字应已被规范化
    assert any("一九九八" in c for c in seen_chunks)
    assert not any("1998" in c for c in seen_chunks)


# === 12. Phase 8: spk_smp 二段法（首段做参考音频）===

def test_first_chunk_used_as_ref(fake_model, monkeypatch):
    """首段达标 → 后续段 params.spk_smp = sample_audio_speaker(wav_0)，
    txt_smp = chunks[0]。第 1 段裸跑（spk_smp 仍 None）。
    """
    monkeypatch.setattr(config, "TEXT_CHUNK_USE_FIRST_AS_REF", True)
    monkeypatch.setattr(config, "TEXT_CHUNK_REF_MIN_CHARS", 4)

    fake_model.sample_audio_returns = "REF_FROM_FIRST"

    seen: list[tuple[str | None, str | None, str]] = []  # (spk_smp, txt_smp, chunk)

    def fake_synth(params, text):
        seen.append((params.spk_smp, params.txt_smp, text))
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "第一段长一点。第二段。第三段。",
    )

    # 第 1 段裸跑（spk_smp 仍未设置）
    assert seen[0][0] is None or seen[0][0] == ""
    assert seen[0][1] is None or seen[0][1] == ""
    # 第 2、3 段被注入
    assert seen[1][0] == "REF_FROM_FIRST"
    assert seen[1][1] == seen[0][2]  # txt_smp = chunks[0] 的实际文本
    assert seen[2][0] == "REF_FROM_FIRST"
    assert seen[2][1] == seen[0][2]

    # sample_audio_speaker 只调一次
    assert len(fake_model.sample_audio_calls) == 1


def test_short_first_chunk_uses_first_qualifying(fake_model, monkeypatch):
    """chunks[0] < min_chars → 用第一个达标段做参考；之前裸跑，之后才注入。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_USE_FIRST_AS_REF", True)
    # 把门槛拉高到 8，让短首段不达标
    monkeypatch.setattr(config, "TEXT_CHUNK_REF_MIN_CHARS", 8)

    fake_model.sample_audio_returns = "REF_FROM_QUALIFYING"

    seen: list[tuple[str | None, str]] = []

    def fake_synth(params, text):
        seen.append((params.spk_smp, text))
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    # 首段 "你好世界呀。"（6 字，> TEXT_CHUNK_MIN_CHARS=4 不合并；< ref MIN_CHARS=8 不达标）
    # 第二段长 → 拿来当参考；第三段被注入
    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "你好世界呀。今天天气真的非常好阳光温暖。下午一起去散步吧。",
    )

    # 应该 3 段
    assert len(seen) == 3, [s[1] for s in seen]
    # 第一段裸跑（< 8 不达标）
    assert seen[0][0] is None or seen[0][0] == ""
    assert len(seen[0][1]) < 8
    # 第二段也裸跑（在它合成"之后"才提取）
    assert seen[1][0] is None or seen[1][0] == ""
    assert len(seen[1][1]) >= 8
    # 但因为它达标 → 提取 ref；第三段开始注入
    assert seen[2][0] == "REF_FROM_QUALIFYING"
    # sample_audio_speaker 1 次（在第二段后）
    assert len(fake_model.sample_audio_calls) == 1


def test_user_spk_smp_takes_precedence(fake_model, monkeypatch):
    """params.spk_smp 已传 → sample_audio_speaker 0 次调用，全程沿用用户值。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_USE_FIRST_AS_REF", True)
    monkeypatch.setattr(config, "TEXT_CHUNK_REF_MIN_CHARS", 4)

    seen: list[str | None] = []

    def fake_synth(params, text):
        seen.append(params.spk_smp)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X", spk_smp="USER_PROVIDED", txt_smp="用户参考"),
        "第一段长一点。第二段。第三段。",
    )

    # 全段都用用户传的值；sample_audio_speaker 0 次
    assert all(s == "USER_PROVIDED" for s in seen)
    assert len(fake_model.sample_audio_calls) == 0


def test_disable_ref_via_config(fake_model, monkeypatch):
    """TEXT_CHUNK_USE_FIRST_AS_REF=False → 全段裸跑，sample_audio_speaker 0 次。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_USE_FIRST_AS_REF", False)
    monkeypatch.setattr(config, "TEXT_CHUNK_REF_MIN_CHARS", 4)

    seen: list[str | None] = []

    def fake_synth(params, text):
        seen.append(params.spk_smp)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "第一段长一点。第二段。第三段。",
    )

    # 全裸跑
    assert all(s is None or s == "" for s in seen)
    assert len(fake_model.sample_audio_calls) == 0


def test_sample_audio_speaker_failure_fallback(fake_model, monkeypatch, caplog):
    """sample_audio_speaker 抛异常 → WARNING + 后续段裸跑 + 整 job 仍成功。"""
    import logging
    monkeypatch.setattr(config, "TEXT_CHUNK_USE_FIRST_AS_REF", True)
    monkeypatch.setattr(config, "TEXT_CHUNK_REF_MIN_CHARS", 4)

    fake_model.sample_audio_raises = RuntimeError("encoding failed")

    seen: list[str | None] = []

    def fake_synth(params, text):
        seen.append(params.spk_smp)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    with caplog.at_level(logging.WARNING, logger="app.core.chat_tts"):
        wav_bytes, segments = synthesize_to_wav_bytes(
            TtsParams(seed=1, speaker="X"),
            "第一段长一点。第二段。第三段。",
        )

    # 后续段也裸跑（fallback），整 job 仍成功
    assert all(s is None or s == "" for s in seen)
    assert wav_bytes[:4] == b"RIFF"
    assert len(segments) == 3
    # WARNING 信息出现
    assert any("sample_audio_speaker failed" in r.message for r in caplog.records)
    # 仅尝试编码 1 次（之后 ref_pending 关闭）
    assert len(fake_model.sample_audio_calls) == 1


def test_single_chunk_no_ref(fake_model, monkeypatch):
    """单段任务 → sample_audio_speaker 0 次调用（不进 ref 分支）。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_USE_FIRST_AS_REF", True)
    monkeypatch.setattr(config, "TEXT_CHUNK_REF_MIN_CHARS", 4)

    seen: list[str | None] = []

    def fake_synth(params, text):
        seen.append(params.spk_smp)
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    synthesize_to_wav_bytes(
        TtsParams(seed=1, speaker="X"),
        "只有一段话。",
    )

    assert len(seen) == 1
    assert seen[0] is None or seen[0] == ""
    assert len(fake_model.sample_audio_calls) == 0


def test_txt_smp_uses_chunked_text_not_raw(fake_model, monkeypatch):
    """txt_smp 必须是切分后的实际 chunk[0] 文本，不是用户原文（覆盖 normalize 后）。"""
    monkeypatch.setattr(config, "TEXT_CHUNK_USE_FIRST_AS_REF", True)
    monkeypatch.setattr(config, "TEXT_CHUNK_REF_MIN_CHARS", 4)

    fake_model.sample_audio_returns = "REF_OK"

    seen: list[tuple[str | None, str]] = []

    def fake_synth(params, text):
        seen.append((params.txt_smp, text))
        return _good_audio(), [(0.0, 1.0)]

    monkeypatch.setattr(chat_tts, "_synthesize_audio", fake_synth)

    raw = "第一段长一点。第二段。第三段。"
    synthesize_to_wav_bytes(TtsParams(seed=1, speaker="X"), raw)

    # 第 1 段裸跑
    first_chunk_text = seen[0][1]
    # 第 2、3 段的 txt_smp 应等于第 1 段的实际 chunk 文本（不是整段 raw）
    assert seen[1][0] == first_chunk_text
    assert seen[2][0] == first_chunk_text
    # 而 first_chunk_text 显然不等于完整原文
    assert first_chunk_text != raw
    assert len(first_chunk_text) < len(raw)
