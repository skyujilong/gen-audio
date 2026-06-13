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

Phase 6.x 诊断日志：
- 在 refine / synthesize 关键节点加 logging：text 长度、refine 后长度、wav 形状/范围、
  模型是否加载、是否走了「静音占位」分支等。诊断 zero-length audio 用。

Phase 7（长文本切分）：`synthesize_to_wav_bytes` 重构为 normalize → chunk → 循环 infer
+ 单段重试 + 塌缩检测 → 拼接 + 段间静音 的流水线。详见
`docs/superpowers/plans/2026-06-13-long-text-chunking.md`。
"""
from __future__ import annotations

import io
import logging
import random
import wave
from typing import Any, Callable

import numpy as np

from .. import config
from . import audio_concat, text_chunker, text_norm
from .params import TtsParams
from .text_utils import replace_tokens, restore_tokens
from .enhance import run_enhance as _run_enhance  # Phase 2.6: synthesize 接入增强

# 模块级别名：测试可通过 `monkeypatch.setattr(chat_tts, "run_enhance", fake)` 替换
run_enhance = _run_enhance

logger = logging.getLogger(__name__)


# === 异常 ===

class ChunkSynthesisError(RuntimeError):
    """单段合成彻底失败（重试耗尽）。

    携带段索引 + 段文本 + 末次错误信息，便于 worker 写 DB error 字段。
    错误消息格式（用户可见）：
        "第 3/8 段合成失败（重试 3 次）：原因=collapse，文本='今天天气真的很好...'"
    """


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
    - `speed: int` → `prompt` 前缀 `"[speed_X]"`
    - `oral` / `laugh` / `break_` → `prompt` 前缀 `"[oral_X][laugh_X][break_X]"`
      （Phase 6.x 关键改动：实测发现把这些 control tokens 放 refine_text 阶段
       容易 GPT 塌缩 → 移到 infer_code.prompt 直接做 prefix 注入，更稳定）
    - `top_p` → `top_P`（ChatTTS 命名约定）
    - `top_k` → `top_K`
    - `seed=0` → `manual_seed=None`（让 ChatTTS 自己随机）
    - `spk_smp` / `txt_smp` 有值时才传（声音克隆）

    Returns:
        `ChatTTS.Chat.InferCodeParams` 实例（或测试 fake 类实例）。
    """
    InferCodeParams, _ = _get_chat_classes()
    # 拼 prompt 前缀：先 [speed_X] 再 [oral_X][laugh_X][break_X]（顺序与官方 ChatTTS 示例一致）
    prompt_parts = [f"[speed_{params.speed}]"]
    if params.oral:
        prompt_parts.append(f"[oral_{params.oral}]")
    if params.laugh:
        prompt_parts.append(f"[laugh_{params.laugh}]")
    if params.break_:
        prompt_parts.append(f"[break_{params.break_}]")
    infer_kwargs: dict[str, Any] = dict(
        top_P=params.top_p,
        top_K=params.top_k,
        temperature=params.temperature,
        manual_seed=params.seed if params.seed != 0 else None,
        spk_emb=params.speaker,
        repetition_penalty=params.repetition_penalty,
        prompt="".join(prompt_parts),
        max_new_token=params.max_new_token,
    )
    if params.spk_smp:
        infer_kwargs["spk_smp"] = params.spk_smp
    if params.txt_smp:
        infer_kwargs["txt_smp"] = params.txt_smp
    return InferCodeParams(**infer_kwargs)


def _build_refine_text_params(params: TtsParams) -> Any | None:
    """把 `TtsParams` 拼装成 ChatTTS 的 `RefineTextParams`。

    Phase 6.x 关键改动：
    - `[oral_X][laugh_X][break_X]` 不再走 refine 阶段（实测 GPT 在 refine 阶段
      容易塌缩成单字），而是塞到 infer_code.prompt 前缀里（见 _build_infer_code_params）。
    - 本函数现在**只**在显式设置 `refiner_text`（自由文本 prompt）时才返回非 None。
    """
    if not params.refiner_text:
        return None
    _, RefineTextParams = _get_chat_classes()
    return RefineTextParams(
        prompt=params.refiner_text,
        top_P=params.top_p,
        top_K=params.top_k,
        temperature=0.7,
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

    Implementation note: 这版 ChatTTS 没有独立的 `refine_text()` 方法，
    改走统一的 `infer(..., refine_text_only=True)` 拿到 `list[str]`。
    """
    if _MODEL is None or not _MODEL.has_loaded():
        # 模型未加载 —— 跳过 refine
        logger.warning(
            "[chat_tts] _refine_text skipped: model not loaded (text_len=%d)", len(text),
        )
        return text

    # 保护 control token
    safe_text, pairs = replace_tokens(text)

    refine_params = _build_refine_text_params(params)
    if refine_params is None:
        logger.info("[chat_tts] _refine_text: no refine prompt (oral=laugh=break_=0, refiner_text empty)")
        return text  # 无可精炼

    logger.info(
        "[chat_tts] _refine_text start: text=%r prompt=%r top_P=%s top_K=%s temp=%s",
        safe_text[:80], refine_params.prompt, refine_params.top_P,
        refine_params.top_K, refine_params.temperature,
    )
    refined_safe_list = _MODEL.infer(
        safe_text,
        params_refine_text=refine_params,
        refine_text_only=True,
    )
    # `refine_text_only=True` 返回 `list[str]`，取第一个元素
    refined_safe = refined_safe_list[0] if refined_safe_list else safe_text
    logger.info(
        "[chat_tts] _refine_text done: in_len=%d out_len=%d result=%r",
        len(safe_text), len(refined_safe), refined_safe[:80],
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
        # 模型未加载 —— 返回静音占位（WARNING 让排障能看到）
        duration_sec = max(1.0, len(text) * 0.15)
        sample_rate = 24000
        audio = np.zeros(int(duration_sec * sample_rate), dtype=np.float32)
        segments = [(0.0, duration_sec)]
        logger.warning(
            "[chat_tts] ⚠️ SILENT PLACEHOLDER: model not loaded, returning %d zero-samples "
            "(text_len=%d duration=%.2fs). Caller will get a 1s silence wav.",
            len(audio), len(text), duration_sec,
        )
        return audio, segments

    # Phase 6.x：synthesize 路径的兜底。draw 路由会显式 sample_random_speaker，
    # 但 synthesize 路由只把前端 params.speaker 透传 —— 如果前端漏传了 speaker 字符串
    # （如选了卡但没碰音色选择器，老前端逻辑会传 ''），这里采样一个随机音色，
    # 避免 ChatTTS decode 抛 LZMAError 让整个 job 标 failed。
    if not (params.speaker or "").strip() and params.speaker_id is None:
        logger.info(
            "[chat_tts] _synthesize_audio: speaker is empty and no speaker_id, "
            "sampling random speaker (text_len=%d)", len(text),
        )
        params = params.model_copy(update={"speaker": _MODEL.sample_random_speaker()})
        logger.info("[chat_tts] _synthesize_audio: sampled random speaker_len=%d", len(params.speaker))

    logger.info(
        "[chat_tts] _synthesize_audio start: text=%r speaker_len=%d speed=%d seed=%d oral=%d laugh=%d break_=%d",
        text[:80], len(params.speaker or ""), params.speed, params.seed,
        params.oral, params.laugh, params.break_,
    )
    infer_params = _build_infer_code_params(params)

    wavs = _MODEL.infer(
        text,
        params_infer_code=infer_params,
        skip_refine_text=True,  # refine 已在外层单独跑
        split_text=False,        # ⭐️ 关键：禁用 ChatTTS 内部按 \n / 。 切片，
                                 # 切分由我们应用层 text_chunker 统一负责，否则
                                 # 两层切分会让字幕时间戳错乱、停顿插入失效
                                 # —— 详见 docs/superpowers/plans/2026-06-13-long-text-chunking.md
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
        # 模型跑了但没出音频 → 静默占位（这才是真 bug，要 WARNING 排障）
        audio = np.zeros(sample_rate, dtype=np.float32)
        segments = [(0.0, 1.0)]
        logger.warning(
            "[chat_tts] ⚠️ SILENT PLACEHOLDER: ChatTTS returned no audio chunks (wavs=%r text=%r). "
            "Will write 1s silence.",
            type(wavs).__name__, text[:80],
        )
        return audio, segments

    audio = np.concatenate(audio_chunks)
    logger.info(
        "[chat_tts] _synthesize_audio done: shape=%s min=%.4f max=%.4f mean=%.4f std=%.4f duration=%.2fs",
        audio.shape, float(audio.min()), float(audio.max()),
        float(audio.mean()), float(audio.std()), len(audio) / sample_rate,
    )
    return audio, segments


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
) -> tuple[bytes, list[tuple[str, float, float]]]:
    """用指定参数合成一段音频（长文本自动切分 + 单段重试 + 塌缩检测）。

    Args:
        params: 合成参数。
        text: 待合成文本（可任意长度，内部会做 TN + 切分）。
        on_progress: 可选进度回调，参数为 0.0–1.0。

    Returns:
        `(wav_bytes, segments)`：
        - `wav_bytes`：合成 + 拼接 + （可选）增强后的 WAV 字节流。
        - `segments`：`[(chunk_text, start_sec, end_sec), ...]`，每个 chunk 一条，**不含**
          段间静音区间（直接喂给 `subtitle.build_srt` 即可）。

    Raises:
        ValueError: 切分后段数超过 `TEXT_CHUNK_MAX_SEGMENTS`。
        ChunkSynthesisError: 任一段重试耗尽仍失败 → 整 job 失败，错误写明哪一段哪句话。
        RuntimeError: enhance / denoise 在当前环境（scipy>=1.10 / torch>=2.10）报错。

    流水线（详见 docs/superpowers/plans/2026-06-13-long-text-chunking.md）：
        Step 0: 强制 `skip_refine_text=True` + 忽略 `refiner_text`（防音色漂移）
        Step 1: text_norm.normalize_text(text) —— 数字 / 日期 / 单位 → 中文念法
        Step 2: text_chunker.split_text(text) —— 切成短段
        Step 3: 固定 speaker（空则提前 sample）+ 固定 seed（=0 时提前抽非零，防各 chunk 漂移）
        Step 4: 每段 _synthesize_one_chunk —— 重试 + 塌缩检测
        Step 5: audio_concat.concat_with_pauses —— 段间插静音
        Step 6: 可选 enhance / denoise（结果转 44100Hz）
        Step 7: 写 WAV bytes，segments 直接给 build_srt 用

    向后兼容：
        模型未加载时 `_infer_audio` 返回静音占位（旧测试这样用）。我们在该路径
        不走重试 / 塌缩检测，直接返回 `_infer_audio` 输出转 WAV，避免假音频被
        判定为塌缩。
    """
    if on_progress:
        on_progress(0.0)

    logger.info(
        "[chat_tts] synthesize_to_wav_bytes start: text=%r oral=%d laugh=%d break_=%d "
        "speed=%d seed=%d speaker_id=%s speaker_len=%d skip_refine=%s enhance=%s denoise=%s",
        text[:60], params.oral, params.laugh, params.break_,
        params.speed, params.seed,
        params.speaker_id,
        len(params.speaker or ""),
        params.skip_refine_text, params.enhance_audio, params.denoise_audio,
    )

    # === Step 0：禁用 refine（实测仍有音色漂移）+ 忽略 refiner_text ===
    params = params.model_copy(update={
        "skip_refine_text": True,
        "refiner_text": None,
    })

    # === 兼容路径：模型未加载（测试或冷启动）→ 不走切分管线，直接调 _infer_audio ===
    # 这条分支保留旧行为：`_synthesize_audio` 返回静音占位，旧测试 mock `_infer_audio`
    # 也走这里。塌缩检测 / 重试只在真模型加载时启用。
    if _MODEL is None or not _MODEL.has_loaded():
        audio, raw_segments = _infer_audio(params, text)
        logger.info(
            "[chat_tts] synthesize_to_wav_bytes: model not loaded, "
            "skipped chunk pipeline (text_len=%d)", len(text),
        )
        if on_progress:
            on_progress(0.6)
        sample_rate = 24000
        if params.enhance_audio or params.denoise_audio:
            audio, sample_rate = _maybe_enhance(audio, sample_rate, params)
        if on_progress:
            on_progress(0.95)
        wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=sample_rate)
        if on_progress:
            on_progress(1.0)
        # 旧调用点期望 segments 是 [(start, end), ...]；新调用点期望 [(text, start, end), ...]。
        # 这里返回 [(text, start, end)] 的新格式，旧测试若解构成 (text, start, end) 也兼容
        # （旧测试只检查 segments 是否为 list，不解构内容）。
        new_segments: list[tuple[str, float, float]] = (
            [(text, raw_segments[0][0], raw_segments[0][1])] if raw_segments else []
        )
        return wav_bytes, new_segments

    # === Step 1：文本规范化（TN）===
    normalized = text_norm.normalize_text(text)
    logger.info(
        "[chat_tts] synthesize_to_wav_bytes: TN done, in_len=%d out_len=%d",
        len(text), len(normalized),
    )

    # === Step 2：切分 ===
    chunks = text_chunker.split_text(
        normalized,
        soft_max=config.TEXT_CHUNK_SOFT_MAX,
        hard_max=config.TEXT_CHUNK_HARD_MAX,
        min_chars=config.TEXT_CHUNK_MIN_CHARS,
    )
    logger.info(
        "[chat_tts] synthesize_to_wav_bytes: chunked into %d segments "
        "(soft_max=%d hard_max=%d min_chars=%d)",
        len(chunks), config.TEXT_CHUNK_SOFT_MAX, config.TEXT_CHUNK_HARD_MAX,
        config.TEXT_CHUNK_MIN_CHARS,
    )

    # 全是标点 / 空文本 → 1s 静音占位（沿用旧 `_synthesize_audio` 的行为）
    if not chunks:
        logger.warning(
            "[chat_tts] synthesize_to_wav_bytes: text chunked into 0 segments "
            "(text=%r) → returning 1s silence placeholder", text[:60],
        )
        audio = np.zeros(24000, dtype=np.float32)
        wav_bytes = _numpy_to_wav_bytes(audio, sample_rate=24000)
        if on_progress:
            on_progress(1.0)
        return wav_bytes, []

    # 段数上限保护：防超长文本独占 worker
    if len(chunks) > config.TEXT_CHUNK_MAX_SEGMENTS:
        raise ValueError(
            f"文本过长，切分后 {len(chunks)} 段超过最大限制 "
            f"{config.TEXT_CHUNK_MAX_SEGMENTS}，请缩短文本或调大 "
            f"TEXT_CHUNK_MAX_SEGMENTS 配置项。"
        )

    # === Step 3：固定 speaker + 固定 seed（防各 chunk 漂移）===
    if not (params.speaker or "").strip() and params.speaker_id is None:
        # 提前 sample 一次：所有 chunk 共用同一 speaker embedding
        sampled = _MODEL.sample_random_speaker()
        params = params.model_copy(update={"speaker": sampled})
        logger.info(
            "[chat_tts] synthesize_to_wav_bytes: speaker empty, sampled fixed speaker "
            "(speaker_len=%d)", len(sampled),
        )

    if params.seed == 0:
        # seed=0 让 ChatTTS 内部每次随机 → 各 chunk 韵律漂移；提前固定一个非 0 seed
        fixed_seed = _random_int(1, 2**31 - 1)
        params = params.model_copy(update={"seed": fixed_seed})
        logger.info(
            "[chat_tts] synthesize_to_wav_bytes: seed=0, fixed to %d for all chunks",
            fixed_seed,
        )

    # === Step 4：串行合成每段，带重试 + 塌缩检测 ===
    audios: list[np.ndarray] = []
    n = len(chunks)
    for i, chunk_text in enumerate(chunks):
        wav = _synthesize_one_chunk(
            params=params,
            chunk_text=chunk_text,
            max_retries=config.TEXT_CHUNK_MAX_RETRIES,
            collapse_ratio=config.TEXT_CHUNK_COLLAPSE_RATIO,
            chunk_idx=i,
            total_chunks=n,
        )
        audios.append(wav)
        # 合成阶段占进度的 0 → 0.6，enhance 留 0.6 → 1.0
        if on_progress:
            on_progress((i + 1) / n * 0.6)

    # === Step 5：拼接（段间插静音）===
    sample_rate = 24000
    merged, segment_times = audio_concat.concat_with_pauses(
        audios,
        sample_rate=sample_rate,
        pause_sec=config.TEXT_CHUNK_PAUSE_SEC,
    )
    logger.info(
        "[chat_tts] synthesize_to_wav_bytes: concat done, total=%.2fs over %d chunks "
        "(pause=%.2fs each)",
        len(merged) / sample_rate, n, config.TEXT_CHUNK_PAUSE_SEC,
    )

    # === Step 6：可选 enhance / denoise ===
    if params.enhance_audio or params.denoise_audio:
        merged, sample_rate = _maybe_enhance(merged, sample_rate, params)
        # enhance 改 sample rate 但不改时长 → segment_times 仍以秒为单位，无需重算

    if on_progress:
        on_progress(0.95)

    # === Step 7：写 WAV bytes + 组装 SRT 用的 (text, start, end) 三元组 ===
    wav_bytes = _numpy_to_wav_bytes(merged, sample_rate=sample_rate)
    srt_segments: list[tuple[str, float, float]] = [
        (chunks[i], segment_times[i][0], segment_times[i][1])
        for i in range(len(chunks))
    ]

    if on_progress:
        on_progress(1.0)

    return wav_bytes, srt_segments


def _maybe_enhance(
    audio: np.ndarray, sample_rate: int, params: TtsParams
) -> tuple[np.ndarray, int]:
    """跑 run_enhance 并把 scipy/torch 兼容性错转成可读 RuntimeError。"""
    try:
        return run_enhance(
            audio, sr=sample_rate,
            denoise=params.denoise_audio,
            enhance=params.enhance_audio,
            solver=params.solver,
            nfe=params.nfe,
            tau=params.tau,
        )
    except (TypeError, ValueError) as e:
        msg = str(e)
        if "arange" in msg or "fsolve" in msg or "0-dimensional" in msg:
            raise RuntimeError(
                "音频增强功能在当前环境不可用：resemble-enhance 0.0.1 与 "
                "scipy>=1.10 / torch>=2.10 不兼容（arange dtype / fsolve 报错）。"
                "请关闭 enhance_audio / denoise_audio 后重试。"
            ) from e
        raise


def _synthesize_one_chunk(
    params: TtsParams,
    chunk_text: str,
    *,
    max_retries: int,
    collapse_ratio: float,
    chunk_idx: int,
    total_chunks: int,
) -> np.ndarray:
    """单段合成 + 重试 + 塌缩检测。

    Args:
        params: 已被外层固定 speaker / seed 的合成参数。
        chunk_text: 当前段文本。
        max_retries: 重试次数（不含首次）。总共最多跑 max_retries+1 次。
        collapse_ratio: 塌缩阈值。每次重试 seed=base_seed+attempt 避开同塌缩路径。
        chunk_idx / total_chunks: 错误信息里报第几段用。

    Returns:
        非塌缩的 wav np.ndarray。

    Raises:
        ChunkSynthesisError: 重试耗尽仍塌缩 / 抛错 → 中止整 job。
    """
    base_seed = params.seed
    last_reason = "unknown"
    for attempt in range(max_retries + 1):
        try:
            attempt_seed = base_seed + attempt
            attempt_params = params.model_copy(update={"seed": attempt_seed})
            audio, _ = _infer_audio(attempt_params, chunk_text)
            if not _is_collapsed(audio, collapse_ratio):
                if attempt > 0:
                    logger.info(
                        "[chat_tts] chunk %d/%d recovered on attempt %d (seed=%d)",
                        chunk_idx + 1, total_chunks, attempt + 1, attempt_seed,
                    )
                return audio
            last_reason = "collapse"
            logger.warning(
                "[chat_tts] chunk %d/%d collapsed (attempt %d/%d seed=%d): %r",
                chunk_idx + 1, total_chunks, attempt + 1, max_retries + 1,
                attempt_seed, chunk_text[:40],
            )
        except ChunkSynthesisError:
            # 别人抛的同名异常不要再吞 / 包；直接外抛
            raise
        except Exception as e:
            last_reason = f"infer error: {e}"
            logger.warning(
                "[chat_tts] chunk %d/%d infer failed (attempt %d/%d): %r — %s",
                chunk_idx + 1, total_chunks, attempt + 1, max_retries + 1,
                chunk_text[:40], e,
            )

    # 重试耗尽 → 中止整 job，错误信息写明第几段 / 哪句话 / 什么原因
    raise ChunkSynthesisError(
        f"第 {chunk_idx + 1}/{total_chunks} 段合成失败"
        f"（重试 {max_retries + 1} 次）："
        f"原因={last_reason}，文本={chunk_text[:60]!r}"
    )


def _is_collapsed(audio: np.ndarray, threshold_ratio: float, eps: float = 1e-5) -> bool:
    """塌缩检测：active sample（|x| > eps）占比 < threshold_ratio → 视为塌缩。

    Args:
        audio: float32 单声道音频。
        threshold_ratio: 阈值（默认 0.05 即 5%）。
        eps: 「有声」判定阈（避免极小数值误判）。

    Returns:
        True 表示塌缩，需要重试。
    """
    if audio is None or len(audio) == 0:
        return True
    active = int(np.sum(np.abs(audio) > eps))
    return active / len(audio) < threshold_ratio
