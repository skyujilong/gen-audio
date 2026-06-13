"""audio_concat.concat_with_pauses 的单元测试。

覆盖：
- 段间静音长度精确（24000 × 0.12 = 2880）
- 最后一段后**不**加静音
- segment_times 累加正确
- 空输入返回 1s 静音 + 空列表
- 单段输入无静音
- pause_sec=0 时退化为纯拼接
- 自定义 sample_rate
- 浮点 wav 精度保留
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.audio_concat import concat_with_pauses


def test_empty_input_returns_one_second_silence():
    """空 audios → 1s 静音占位 + 空 segments。"""
    merged, segs = concat_with_pauses([])
    assert merged.shape == (24000,)
    assert merged.dtype == np.float32
    assert np.all(merged == 0)
    assert segs == []


def test_single_segment_no_silence():
    """单段 → 无静音，segment_times 是 [(0, duration)]。"""
    a = np.ones(2400, dtype=np.float32)  # 0.1s
    merged, segs = concat_with_pauses([a])
    assert merged.shape == (2400,)
    assert np.all(merged == 1.0)
    assert segs == [(0.0, 0.1)]


def test_two_segments_silence_between():
    """两段 → 中间一段 0.12s × 24000Hz = 2880 静音；最后没有。"""
    a = np.ones(2400, dtype=np.float32)
    b = np.full(4800, 0.5, dtype=np.float32)
    merged, segs = concat_with_pauses([a, b])

    expected_pause = int(round(0.12 * 24000))  # 2880
    expected_total = 2400 + expected_pause + 4800
    assert merged.shape == (expected_total,)

    # 第一段
    assert np.all(merged[:2400] == 1.0)
    # 静音段
    assert np.all(merged[2400:2400 + expected_pause] == 0)
    # 第二段
    assert np.all(merged[2400 + expected_pause:] == 0.5)

    # segments 不含静音区间
    assert segs[0] == (0.0, 0.1)
    # 第二段 start = (2400 + 2880) / 24000
    expected_start_b = (2400 + expected_pause) / 24000
    expected_end_b = (2400 + expected_pause + 4800) / 24000
    assert segs[1] == pytest.approx((expected_start_b, expected_end_b))


def test_three_segments_pauses_between_only():
    """三段 → 段间各一段静音，最后一段后不加。"""
    a = np.ones(2400, dtype=np.float32)
    b = np.ones(3600, dtype=np.float32) * 0.3
    c = np.ones(4800, dtype=np.float32) * 0.7
    merged, segs = concat_with_pauses([a, b, c])

    pause = int(round(0.12 * 24000))
    expected = 2400 + pause + 3600 + pause + 4800  # 2 静音
    assert merged.shape == (expected,)
    assert len(segs) == 3
    # 最后一段结尾即 merged 总长
    assert segs[-1][1] == pytest.approx(merged.shape[0] / 24000)


def test_segment_times_no_silence_in_them():
    """segment_times[i] 只覆盖 chunk 自身时长，不含静音。"""
    durations = [0.1, 0.2, 0.15]
    audios = [np.ones(int(d * 24000), dtype=np.float32) for d in durations]
    _, segs = concat_with_pauses(audios)
    # 每段时长应等于原始 chunk 时长
    for (start, end), d in zip(segs, durations):
        assert end - start == pytest.approx(d, abs=1e-4)


def test_custom_pause_sec():
    """自定义 pause_sec 改变段间静音样本数。"""
    a = np.ones(1000, dtype=np.float32)
    b = np.ones(1000, dtype=np.float32)
    merged, _ = concat_with_pauses([a, b], pause_sec=0.5)
    expected_pause = int(round(0.5 * 24000))  # 12000
    assert merged.shape == (1000 + expected_pause + 1000,)


def test_pause_zero_no_silence():
    """pause_sec=0 → 纯拼接，无静音。"""
    a = np.ones(1000, dtype=np.float32)
    b = np.ones(2000, dtype=np.float32)
    merged, segs = concat_with_pauses([a, b], pause_sec=0.0)
    assert merged.shape == (3000,)
    # 第二段紧接第一段
    assert segs[1][0] == pytest.approx(1000 / 24000)


def test_custom_sample_rate():
    """自定义 sample_rate 改变 0.12s 对应的样本数。"""
    a = np.ones(8000, dtype=np.float32)
    b = np.ones(8000, dtype=np.float32)
    merged, _ = concat_with_pauses([a, b], sample_rate=16000, pause_sec=0.1)
    expected_pause = int(round(0.1 * 16000))  # 1600
    assert merged.shape == (8000 + expected_pause + 8000,)


def test_dtype_is_float32():
    """合并后 dtype 是 float32（即使输入混入 float64）。"""
    a = np.ones(1000, dtype=np.float64)  # 故意 float64
    b = np.ones(1000, dtype=np.float32)
    merged, _ = concat_with_pauses([a, b])
    assert merged.dtype == np.float32


def test_float_values_preserved():
    """float 振幅值精确保留（不被裁剪 / 量化）。"""
    a = np.array([0.1, -0.5, 0.99, -0.99, 0.0], dtype=np.float32)
    merged, _ = concat_with_pauses([a], pause_sec=0.0)
    np.testing.assert_array_equal(merged, a)


def test_multidim_input_squeezed():
    """二维 (N, 1) 输入会被 reshape(-1) 拉成一维。"""
    a = np.ones((1000, 1), dtype=np.float32)
    merged, segs = concat_with_pauses([a])
    assert merged.shape == (1000,)
    assert segs == [(0.0, 1000 / 24000)]


def test_zero_length_segment_keeps_index_alignment():
    """空段（len=0）仍记一个 segment，offset 不变。"""
    a = np.ones(2400, dtype=np.float32)
    empty = np.zeros(0, dtype=np.float32)
    c = np.ones(2400, dtype=np.float32)
    merged, segs = concat_with_pauses([a, empty, c])

    # 三段，索引一一对应
    assert len(segs) == 3
    # 空段的 start == end
    assert segs[1][0] == segs[1][1]
    # 但 offset 仍前进了一段静音（因为空段后还要加静音 → offset += pause）
    pause = int(round(0.12 * 24000))
    # a + pause + 0 + pause + c
    assert merged.shape == (2400 + pause + 0 + pause + 2400,)
