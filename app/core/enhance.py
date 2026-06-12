"""音频增强 / 降噪：resemble-enhance 的薄封装。

设计要点：
- `load_enhancer()` 走 `@cache` 单例，首次调用下载模型权重并加载到内存；后续调用复用。
- `run_enhance(audio, sr, *, denoise, enhance, solver, nfe, tau)` 是对外 API。
  - `denoise=True` → `lambd=0.9`（强降噪）
  - `denoise=False` + `enhance=True` → `lambd=0.1`（轻增强）
  - 都 False → passthrough（不调模型）
- 输出采样率固定 44100Hz（resemble-enhance 模型内部上采样到此）。

参考 ChatTTS-Enhanced-main：
- `processors/enhance_processors.py:24-29`（`lambd=0.9 if denoise else 0.1`）
- `modules/enhance/enhancer/inference.py:27-41`（denoise / enhance 签名）
"""
from __future__ import annotations

from functools import cache
from typing import Any

import numpy as np


OUTPUT_SAMPLE_RATE = 44100
"""resemble-enhance 模型输出固定 44100Hz。"""


# 模块级句柄：测试可通过 monkeypatch 替换；运行时懒加载
_lib_enhance: Any = None


def _get_lib_enhance() -> Any:
    """懒加载 resemble_enhance.enhancer.inference.enhance 函数。"""
    global _lib_enhance
    if _lib_enhance is None:
        from resemble_enhance.enhancer.inference import enhance
        _lib_enhance = enhance
    return _lib_enhance


# === 懒加载 enhancer 模型（@cache 单例） ===

@cache
def _load_enhancer(run_dir: str | None = None, device: str = "cpu") -> Any:
    """加载 enhancer 模型，@cache 单例。

    Args:
        run_dir: 模型权重目录（None → 走默认 download()）。
        device: "cpu" / "cuda" / "mps"。

    Returns:
        `resemble_enhance.enhancer.train.Enhancer` 实例。
    """
    from resemble_enhance.enhancer.inference import load_enhancer as _lib_load
    return _lib_load(run_dir, device)


# === 公开 API ===

def run_enhance(
    audio: np.ndarray,
    sr: int,
    *,
    denoise: bool = False,
    enhance: bool = False,
    solver: str = "midpoint",
    nfe: int = 64,
    tau: float = 0.5,
    device: str = "cpu",
    run_dir: str | None = None,
) -> tuple[np.ndarray, int]:
    """跑增强 / 降噪，返回 (audio, OUTPUT_SAMPLE_RATE)。

    Args:
        audio: float32 numpy 数组（单声道）。
        sr: 输入采样率（一般 24000）。
        denoise: True → lambd=0.9（强降噪）。
        enhance: True → lambd=0.1（轻增强；与 denoise 互不冲突，但 denoise 优先）。
        solver: midpoint / rk4 / euler。
        nfe: 1-128。
        tau: 0-1。
        device: 推理设备。
        run_dir: 模型权重目录（None 默认）。

    Returns:
        `(enhanced_audio, OUTPUT_SAMPLE_RATE)`。

    Raises:
        RuntimeError: 模型未加载 / 推理失败。
    """
    if not (denoise or enhance):
        # passthrough：原 audio + 原 sr 原样返回（不调库、不变采样率）
        return audio, sr

    # plan: lambd=0.9 if denoise else 0.1
    lambd = 0.9 if denoise else 0.1

    # 调库函数（测试可 monkeypatch app.core.enhance._lib_enhance）
    lib_enhance = _get_lib_enhance()
    out_audio = lib_enhance(
        audio, sr, device,
        nfe=nfe, solver=solver, lambd=lambd, tau=tau,
        run_dir=run_dir,
    )
    return out_audio, OUTPUT_SAMPLE_RATE


# === 兼容旧 API（test 期望 load_enhancer 名字暴露） ===

def load_enhancer(run_dir: str | None = None, device: str = "cpu") -> Any:
    """公共 API：返回 cached enhancer 实例。"""
    return _load_enhancer(run_dir, device)
