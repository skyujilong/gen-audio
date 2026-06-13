"""多段 wav 拼接 + 段间静音 + 时间戳累加。

`synthesize_to_wav_bytes` 切分长文本后，逐段调 ChatTTS 拿到一个 `list[np.ndarray]`，
本模块负责把它们拼成一段连续 wav，并在段间插一段固定长度的静音；同时返回每个原始 chunk
在最终 wav 里的 (start, end) 时间区间，**不含静音**——SRT 直接用 chunk 索引对齐。
"""
from __future__ import annotations

import numpy as np


def concat_with_pauses(
    audios: list[np.ndarray],
    *,
    sample_rate: int = 24000,
    pause_sec: float = 0.12,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """拼接多段 wav，段间插静音，返回 (合并后 wav, 每段时间区间)。

    Args:
        audios: 多段 float32 单声道音频。
        sample_rate: 采样率（默认 24000，对齐 ChatTTS 输出）。
        pause_sec: 段间静音长度（秒）。最后一段后**不**加静音。

    Returns:
        `(merged, segment_times)`：
        - `merged`：拼接后的整段 float32 numpy 数组（可能 0 长度，仅当 audios 全空时是 1s 静音）。
        - `segment_times`：与 `audios` 同长的列表，元素 `(start_sec, end_sec)` 是该段在 merged
          里的时间区间。**静音区间不在列表里**，方便 SRT 直接按 chunk 索引取时间。

    边界：
        - `audios` 为空 → 返回 `(np.zeros(sample_rate), [])`，提供 1 秒静音占位。
        - 单段 → 直接返回该段，无静音。
        - 任意段是空数组（长度 0）→ 仍记 (offset, offset)，不破坏索引对齐。
    """
    if not audios:
        return np.zeros(sample_rate, dtype=np.float32), []

    pause_samples = max(0, int(round(pause_sec * sample_rate)))
    silence = np.zeros(pause_samples, dtype=np.float32) if pause_samples > 0 else None

    parts: list[np.ndarray] = []
    segment_times: list[tuple[float, float]] = []
    offset_samples = 0

    n = len(audios)
    for i, audio in enumerate(audios):
        # 强制 float32 + 一维
        a = np.asarray(audio, dtype=np.float32).reshape(-1)
        start = offset_samples / sample_rate
        end = (offset_samples + len(a)) / sample_rate
        segment_times.append((start, end))
        parts.append(a)
        offset_samples += len(a)

        # 最后一段后不加静音
        if silence is not None and i < n - 1:
            parts.append(silence)
            offset_samples += pause_samples

    merged = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    return merged, segment_times
