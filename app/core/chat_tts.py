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

Phase 2.2-2.5 改造：
- 抽 `_build_infer_code_params(params)` —— 拼 `InferCodeParams`，把 `speed: int` 拼成 `prompt="[speed_X]"`
- 抽 `_build_refine_text_params(params)` —— 拼 `RefineTextParams`（refiner_text 自由文本或 3 整数互斥）
- 拆 `_infer_audio` 为 `_refine_text` + `_synthesize_audio` 两步范式，refine 输出用 `replace_tokens` 保护
"""
from __future__ import annotations

import io
import random
import wave
from typing import Any, Callable

import numpy as np

from .params import TtsParams
from .text_utils import replace_tokens, restore_tokens
from .enhance import run_enhance as _run_enhance  # Phase 2.6: synthesize 接入增强

# 模块级别名：测试可通过 `monkeypatch.setattr(chat_tts, "run_enhance", fake)` 替换
run_enhance = _run_enhance


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


# === Phase 2.2: 参数拼装函数 ===

# 模块级句柄 → 测试可通过 monkeypatch 替换；运行时由 _get_chat_classes() 懒加载
_ChatInferCodeParams: Any = None
_ChatRefineTextParams: Any = None


def _get_chat_classes() -> tuple[Any, Any]:
    """懒加载 ChatTTS.Chat 的 InferCodeParams / RefineTextParams 类。

    用 module-level 句柄缓存 + 延迟导入：测试环境 import ChatTTS 会拖慢 / 失败，
    这里只在真正调用推理时才走 import。

    测试可通过 monkeypatch 设 `_ChatInferCodeParams` / `_ChatRefineTextParams`：
    - 设单个 → 该 fake 用上，另一个照样 import 真实类。
    - 都设 → 全部走 fake，不 import。
    """
    global _ChatInferCodeParams, _ChatRefineTextParams
    # 只在**两个都未设置**时 import；防止部分 monkeypatch 时被覆盖
    if _ChatInferCodeParams is None and _ChatRefineTextParams is None:
        import ChatTTS
        _ChatInferCodeParams = ChatTTS.Chat.InferCodeParams
        _ChatRefineTextParams = ChatTTS.Chat.RefineTextParams
    return _ChatInferCodeParams, _ChatRefineTextParams


def _build_infer_code_params(params: TtsParams) -> Any:
    """把 `TtsParams` 拼装成 ChatTTS 的 `InferCodeParams`。

    关键映射：
    - `speed: int` → `prompt="[speed_X]"`
    - `top_p` → `top_P`（ChatTTS 命名约定）
    - `top_k` → `top_K`
    - `seed=0` → `manual_seed=None`（让 ChatTTS 自己随机）
    - `spk_smp` / `txt_smp` 有值时才传（声音克隆）

    Returns:
        `ChatTTS.Chat.InferCodeParams` 实例（或测试 fake 类实例）。
    """
    InferCodeParams, _ = _get_chat_classes()
    infer_kwargs: dict[str, Any] = dict(
        top_P=params.top_p,
        top_K=params.top_k,
        temperature=params.temperature,
        manual_seed=params.seed if params.seed != 0 else None,
        spk_emb=params.speaker,
        repetition_penalty=params.repetition_penalty,
        prompt=f"[speed_{params.speed}]",
        max_new_token=params.max_new_token,
    )
    if params.spk_smp:
        infer_kwargs["spk_smp"] = params.spk_smp
    if params.txt_smp:
        infer_kwargs["txt_smp"] = params.txt_smp
    return InferCodeParams(**infer_kwargs)


def _build_refine_text_params(params: TtsParams) -> Any | None:
    """把 `TtsParams` 拼装成 ChatTTS 的 `RefineTextParams`。

    互斥逻辑：
    1. 若 `refiner_text` 非空 → 用自由文本 prompt
    2. 否则 → 由 `oral` / `laugh` / `break_` 3 整数拼 `[oral_X][laugh_X][break_X]`
    3. 三者都=0 且 refiner_text 空 → 返回 None（不精炼）

    额外补 `top_P/top_K/temperature` 让 refine 阶段采样与 infer_code 一致。
    """
    _, RefineTextParams = _get_chat_classes()

    if params.refiner_text:
        prompt = params.refiner_text
    else:
        parts = []
        if params.oral:
            parts.append(f"[oral_{params.oral}]")
        if params.laugh:
            parts.append(f"[laugh_{params.laugh}]")
        if params.break_:
            parts.append(f"[break_{params.break_}]")
        if not parts:
            return None
        prompt = "".join(parts)

    return RefineTextParams(
        prompt=prompt,
        top_P=params.top_p,
        top_K=params.top_k,
        temperature=params.temperature,
    )


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
    speed: int = 5,
    skip_refine_text: bool = False,
    max_new_token: int = 2048,
    spk_smp: str | None = None,
    txt_smp: str | None = None,
    oral: int = 0,
    laugh: int = 0,
    break_: int = 0,
    enhance_audio: bool = False,
    denoise_audio: bool = False,
    solver: str = "midpoint",
    nfe: int = 64,
    tau: float = 0.5,
) -> TtsParams:
    """生成：seed/speaker 为 None 时随机，其余使用传入值。

    Phase 2.6：增强 / 降噪参数也透传。draw 试听时这些参数会被 draw 路由层忽略
    （强制不调 run_enhance）；synthesize 时则生效。
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
        oral=oral,
        laugh=laugh,
        break_=break_,
        enhance_audio=enhance_audio,
        denoise_audio=denoise_audio,
        solver=solver,
        nfe=nfe,
        tau=tau,
    )


# === 合成 ===

def _refine_text(params: TtsParams, text: str) -> str:
    """第一步：refine_text 阶段。把口语化 / 标点 / 停顿注入文本。

    - 用 `replace_tokens` 保护用户注入的 control token（[oral_X] / [laugh_X] / [break_X]），
      避免被 refine 模型破坏。
    - 返回精炼后的文本（占位符已恢复回原 token）。
    """
    if _MODEL is None or not _MODEL.has_loaded():
        # 模型未加载 —— 跳过 refine
        return text

    # 保护 control token
    safe_text, pairs = replace_tokens(text)

    refine_params = _build_refine_text_params(params)
    if refine_params is None:
        return text  # 无可精炼

    refined_safe = _MODEL.refine_text(
        safe_text,
        params=refine_params,
    )
    # 恢复 control token
    return restore_tokens(refined_safe, pairs)


def _synthesize_audio(
    params: TtsParams, text: str
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """第二步：infer_code 阶段。生成 wav 数组。

    Returns:
        (audio_array, segments) —— audio 是 float32 单声道，segments 是 [(start_sec, end_sec), ...]。
    """
    if _MODEL is None or not _MODEL.has_loaded():
        # 模型未加载 —— 返回静音占位
        duration_sec = max(1.0, len(text) * 0.15)
        sample_rate = 24000
        audio = np.zeros(int(duration_sec * sample_rate), dtype=np.float32)
        segments = [(0.0, duration_sec)]
        return audio, segments

    infer_params = _build_infer_code_params(params)

    wavs = _MODEL.infer(
        text,
        params_infer_code=infer_params,
        skip_refine_text=True,  # refine 已在外层单独跑
    )

    audio_chunks: list[np.ndarray] = []
    segments: list[tuple[float, float]] = []
    offset = 0.0
    sample_rate = 24000

    for chunk in wavs:
        if isinstance(chunk, np.ndarray):
            chunk = chunk.squeeze()
            audio_chunks.append(chunk)
            duration = len(chunk) / sample_rate
            segments.append((offset, offset + duration))
            offset += duration

    if not audio_chunks:
        audio = np.zeros(sample_rate, dtype=np.float32)
        segments = [(0.0, 1.0)]
        return audio, segments

    return np.concatenate(audio_chunks), segments


def _infer_audio(
    params: TtsParams, text: str
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """两步范式：先 refine_text，再 infer_code。

    - 若 `skip_refine_text=True` → 跳过 refine 阶段
    - 进度由 `synthesize_to_wav_bytes` 负责（这里不接回调，保持 (params, text) 二参签名
      以便老测试 `monkeypatch.setattr(_infer_audio, fake)` 用 2-arg fake）。
    """
    if params.skip_refine_text:
        return _synthesize_audio(params, text)

    refined_text = _refine_text(params, text)
    return _synthesize_audio(params, refined_text)


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

    Phase 2.6：合成后若 `params.enhance_audio or params.denoise_audio` → 调
    `_run_enhance`（增强后输出 44100Hz）。draw 路由不调本函数，而是直接调
    `_infer_audio` + `_numpy_to_wav_bytes` 来跳过增强。
    """
    if on_progress:
        on_progress(0.0)

    audio, segments = _infer_audio(params, text)

    if on_progress:
        on_progress(0.6)  # 推理完成；增强从 0.6 → 1.0

    # Phase 2.6: 增强 / 降噪
    sample_rate = 24000
    if params.enhance_audio or params.denoise_audio:
        audio, sample_rate = run_enhance(
            audio, sr=sample_rate,
            denoise=params.denoise_audio,
            enhance=params.enhance_audio,
            solver=params.solver,
            nfe=params.nfe,
            tau=params.tau,
        )

    if on_progress:
        on_progress(0.95)

    wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=sample_rate)

    if on_progress:
        on_progress(1.0)

    return wav_bytes, segments
