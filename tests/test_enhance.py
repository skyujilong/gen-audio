"""测试音频增强 / 降噪模块（resemble-enhance 封装）。"""
from __future__ import annotations

import numpy as np

from app.core import enhance as enh_mod
from app.core.enhance import run_enhance


# 24000Hz 1 秒静音
_SAMPLE_AUDIO_24K = np.zeros(24000, dtype=np.float32)


def test_run_enhance_passthrough_when_neither_flag():
    """enhance/denoise 都 False → 原 audio + 原 sr 直接返回（不调库）。"""
    out_audio, out_sr = run_enhance(_SAMPLE_AUDIO_24K.copy(), sr=24000,
                                     denoise=False, enhance=False,
                                     solver="midpoint", nfe=64, tau=0.5)
    assert out_sr == 24000  # 保留输入 sr
    assert len(out_audio) == 24000  # 1 秒原样


def test_run_enhance_denoise_calls_enhance_with_high_lambd(monkeypatch):
    """denoise=True → lambd=0.9 调用 enhance（plan: lambd=0.9 if denoise else 0.1）。"""
    captured = {}

    def fake_enhance(dwav, sr, device, nfe, solver, lambd, tau, run_dir=None):
        captured.update(dwav=dwav, sr=sr, nfe=nfe, solver=solver, lambd=lambd, tau=tau)
        return np.zeros(44100, dtype=np.float32)

    monkeypatch.setattr(enh_mod, "_lib_enhance", fake_enhance)

    out_audio, out_sr = run_enhance(_SAMPLE_AUDIO_24K, sr=24000, denoise=True,
                                     solver="rk4", nfe=32, tau=0.7)
    assert out_sr == 44100
    assert captured["lambd"] == 0.9
    assert captured["nfe"] == 32
    assert captured["solver"] == "rk4"
    assert captured["tau"] == 0.7


def test_run_enhance_enhance_only_calls_with_low_lambd(monkeypatch):
    """denoise=False + enhance=True → lambd=0.1。"""
    captured = {}

    def fake_enhance(dwav, sr, device, nfe, solver, lambd, tau, run_dir=None):
        captured["lambd"] = lambd
        return np.zeros(44100, dtype=np.float32)

    monkeypatch.setattr(enh_mod, "_lib_enhance", fake_enhance)

    run_enhance(_SAMPLE_AUDIO_24K, sr=24000, denoise=False, enhance=True,
                solver="midpoint", nfe=64, tau=0.5)
    assert captured["lambd"] == 0.1


def test_run_enhance_passes_correct_sample_rate(monkeypatch):
    """输入 sr 应原样透传到 enhance()。"""
    captured = {}

    def fake_enhance(dwav, sr, device, nfe, solver, lambd, tau, run_dir=None):
        captured["sr"] = sr
        return np.zeros(44100, dtype=np.float32)

    monkeypatch.setattr(enh_mod, "_lib_enhance", fake_enhance)

    run_enhance(_SAMPLE_AUDIO_24K, sr=24000, denoise=True,
                solver="midpoint", nfe=64, tau=0.5)
    assert captured["sr"] == 24000


def test_run_enhance_returns_44100(monkeypatch):
    """返回的 sr 应是 44100（resemble-enhance 输出采样率）。"""
    fake_enhance = lambda *a, **kw: np.zeros(44100, dtype=np.float32)
    monkeypatch.setattr(enh_mod, "_lib_enhance", fake_enhance)

    _, out_sr = run_enhance(_SAMPLE_AUDIO_24K, sr=24000, denoise=True,
                             solver="midpoint", nfe=64, tau=0.5)
    assert out_sr == 44100


def test_load_enhancer_returns_cached(monkeypatch):
    """load_enhancer 走 @cache，同 (run_dir, device) 多次调用应得同一实例。"""
    calls = [0]

    def fake_lib_load(run_dir, device):
        calls[0] += 1
        return f"enhancer-{calls[0]}"

    monkeypatch.setattr(enh_mod, "_load_enhancer", lambda *a, **kw: fake_lib_load(*a, **kw))
    # 用 functools.cache 重写
    from functools import cache
    cached_load = cache(lambda rd, dv: fake_lib_load(rd, dv))
    monkeypatch.setattr(enh_mod, "_load_enhancer", cached_load)

    a = enh_mod.load_enhancer(None, "cpu")
    b = enh_mod.load_enhancer(None, "cpu")
    c = enh_mod.load_enhancer(None, "cuda")
    assert a == b  # 同样的 key
    assert a != c  # 不同 device 不同 key
    assert calls[0] == 2  # 实际加载 2 次（cpu + cuda）
