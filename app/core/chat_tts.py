"""ChatTTS 模型封装（适配 ChatTTS >= 0.2.5）。

对外暴露三个函数：
- `is_model_loaded()`: 当前模型是否已加载进内存。
- `draw_one(refiner_text)`: 生成 —— 随机生成一个 `TtsParams`。
- `synthesize_to_wav_bytes(params, text, on_progress)`: 用指定参数合成一段音频。

设计要点：
- 模型是 module-level 单例，加载一次后所有请求复用。
- 随机参数走 `_random_int` / `_random_float` 等内部函数，测试可以 patch 掉。
- 真实模型推理走 `_infer_audio(params, text) -> (np.ndarray, segments)`，测试可 patch。
- **不吞错**：任何异常都直接往上抛，由调用方（worker）转 `TtsError`。

ChatTTS 0.2.5 API 变更（相比 0.1.x）：
- `load_models()` → `load()`
- 新增 `InferCodeParams` / `RefineTextParams` 参数类
- 新增 `sample_random_speaker()` 返回 speaker embedding 字符串
- `has_loaded()` 替代手动 `_LOADED` 标记
- `infer()` 接受 `params_infer_code` / `params_refine_text` 参数
"""
from __future__ import annotations

import io
import random
import wave
from typing import Callable

import numpy as np

from .params import TtsParams


# === 单例状态 ===

_MODEL = None   # 真实 ChatTTS 模型实例（首次加载后填入）


def is_model_loaded() -> bool:
    """返回模型是否已加载到内存。"""
    if _MODEL is None:
        return False
    return _MODEL.has_loaded()


def load_model() -> None:
    """加载 ChatTTS 模型到内存。失败抛 `RuntimeError`（由调用方 wrap 为 `TtsError`）。"""
    global _MODEL
    try:
        import ChatTTS  # 延迟导入：测试环境可能没装
        _MODEL = ChatTTS.Chat()
        _MODEL.load(compile=False)  # ChatTTS 0.2.5: load_models → load
    except Exception as e:
        _MODEL = None
        raise RuntimeError(f"ChatTTS 模型加载失败: {e}") from e


# === 随机工具（可被测试 patch） ===

def _random_int(lo: int, hi: int) -> int:
    """在 [lo, hi] 区间内取随机整数。"""
    return random.randint(lo, hi)


def _random_float(lo: float, hi: float) -> float:
    """在 [lo, hi] 区间内取随机浮点数。"""
    return random.uniform(lo, hi)


def _random_choice(items: list):
    """从列表中随机选一个元素。"""
    return random.choice(items)


def _random_speaker() -> str:
    """从 ChatTTS 随机采样一个 speaker embedding 字符串。

    ChatTTS 0.2.5 提供 `sample_random_speaker()` 返回 speaker 字符串。
    若模型未加载，回退到占位字符串。
    """
    if _MODEL is not None and _MODEL.has_loaded():
        return _MODEL.sample_random_speaker()
    return "QkFTRTY0U1BLQUNLRVI="  # 占位："BASE64SPKACKER" 的 base64


# === 生成 ===

def draw_one(refiner_text: str | None = None) -> TtsParams:
    """生成：随机生成完整 `TtsParams`（seed/speaker 随机，其余默认值）。

    Args:
        refiner_text: 可选风格 prompt；为 None 时不设置。
    """
    return draw_one_from_params(refiner_text=refiner_text)


def draw_one_from_params(
    seed: int | None = None,
    temperature: float = 0.3,
    top_p: float = 0.7,
    top_k: int = 20,
    speaker: str | None = None,
    refiner_text: str | None = None,
    repetition_penalty: float = 1.05,
    speed: str = "[speed_5]",
    skip_refine_text: bool = False,
    max_new_token: int = 2048,
    spk_smp: str | None = None,
    txt_smp: str | None = None,
) -> TtsParams:
    """生成：seed/speaker 为 None 时随机，其余使用传入值。

    Args:
        seed: 随机种子，None 时随机生成。
        temperature: 采样温度。
        top_p: nucleus sampling 阈值。
        top_k: top-k sampling 阈值。
        speaker: speaker embedding，None 时随机采样。
        refiner_text: 可选风格 prompt。
        repetition_penalty: 重复惩罚，>1 抑制重复。
        speed: 语速控制 token。
        skip_refine_text: 跳过文本精炼。
        max_new_token: 最大生成 token 数。
        spk_smp: 参考音频 speaker（声音克隆）。
        txt_smp: 参考音频对应文本。
    """
    if seed is None:
        seed = _random_int(0, 2**31 - 1)
    if speaker is None:
        speaker = _random_speaker()
    return TtsParams(
        seed=seed,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        speaker=speaker,
        refiner_text=refiner_text,
        repetition_penalty=repetition_penalty,
        speed=speed,
        skip_refine_text=skip_refine_text,
        max_new_token=max_new_token,
        spk_smp=spk_smp,
        txt_smp=txt_smp,
    )


# === 合成 ===

def _infer_audio(params: TtsParams, text: str) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """真实 ChatTTS 推理。返回 (audio_array, segments)。

    **必须返回 audio 数组（float32，单声道）和 (start_sec, end_sec) 段列表。**

    ChatTTS 0.2.5 的 infer() 接口：
    - text: 待合成文本
    - params_infer_code: InferCodeParams(top_P, top_K, temperature, manual_seed, spk_emb, ...)
    - params_refine_text: RefineTextParams(prompt=refiner_text, ...)
    - 返回: 生成器或 list[np.ndarray]，    """
    if _MODEL is None or not _MODEL.has_loaded():
        # 模型未加载 —— 返回静音占位
        duration_sec = max(1.0, len(text) * 0.15)
        sample_rate = 24000  # ChatTTS 默认采样率
        audio = np.zeros(int(duration_sec * sample_rate), dtype=np.float32)
        segments = [(0.0, duration_sec)]
        return audio, segments

    import ChatTTS

    # 构造推理参数
    infer_kwargs = dict(
        top_P=params.top_p,
        top_K=params.top_k,
        temperature=params.temperature,
        manual_seed=params.seed if params.seed != 0 else None,
        spk_emb=params.speaker,
        repetition_penalty=params.repetition_penalty,
        prompt=params.speed,
        max_new_token=params.max_new_token,
    )
    # 声音克隆参数（可选）
    if params.spk_smp:
        infer_kwargs["spk_smp"] = params.spk_smp
    if params.txt_smp:
        infer_kwargs["txt_smp"] = params.txt_smp

    infer_params = ChatTTS.Chat.InferCodeParams(**infer_kwargs)

    refine_params = None
    if params.refiner_text:
        refine_params = ChatTTS.Chat.RefineTextParams(
            prompt=params.refiner_text,
        )

    # skip_refine_text: 用户显式跳过 或 没有精炼参数
    should_skip = params.skip_refine_text or (refine_params is None)

    # 调用推理
    wavs = _MODEL.infer(
        text,
        params_infer_code=infer_params,
        params_refine_text=refine_params,
        skip_refine_text=should_skip,
    )

    # wavs 是一个生成器或 list，每个元素是 np.ndarray
    audio_chunks = []
    segments = []
    offset = 0.0
    sample_rate = 24000  # ChatTTS 默认输出 24kHz

    for chunk in wavs:
        if isinstance(chunk, np.ndarray):
            chunk = chunk.squeeze()  # 去掉多余维度
            audio_chunks.append(chunk)
            duration = len(chunk) / sample_rate
            segments.append((offset, offset + duration))
            offset += duration

    if not audio_chunks:
        # 空结果 —— 返回短静音
        audio = np.zeros(sample_rate, dtype=np.float32)
        segments = [(0.0, 1.0)]
        return audio, segments

    audio = np.concatenate(audio_chunks)
    return audio, segments


def _numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = 24000) -> bytes:
    """把 float32 numpy 数组转 16-bit PCM WAV 字节串。"""
    audio_int16 = np.clip(audio, -1.0, 1.0)
    audio_int16 = (audio_int16 * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def synthesize_to_wav_bytes(
    params: TtsParams,
    text: str,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[bytes, list[tuple[float, float]]]:
    """用指定参数合成一段音频。

    Args:
        params: 合成参数。
        text: 待合成文本。
        on_progress: 可选进度回调，参数为 0.0–1.0。

    Returns:
        (wav_bytes, segments)；`segments` 是 `[(start_sec, end_sec), ...]`。

    Raises:
        Exception: 推理失败时由 `_infer_audio` 抛出原样上抛（不吞错）。
    """
    if on_progress:
        on_progress(0.0)

    audio, segments = _infer_audio(params, text)

    if on_progress:
        on_progress(0.7)  # 推理完成

    wav_bytes = _numpy_to_wav_bytes(audio)

    if on_progress:
        on_progress(1.0)

    return wav_bytes, segments
