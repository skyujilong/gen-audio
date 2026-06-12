"""Vendored copy of resemble-enhance 0.0.1 (MIT, see LICENSE).

来源：https://github.com/resemble-ai/resemble-enhance @ v0.0.1
vendor 原因：
  - pip 上的 wheel 锁 cp311 + torch==2.1.1，cp312 装不上；
  - 源码在 scipy>=1.10 / torch>=2.10 上有两处不兼容：
    * enhancer/lcfm/cfm.py:74  fsolve 在新版返回 1d ndarray，float() 拒绝
    * inference.py:34  调 torchaudio.functional.resample，
      而后者内部 _get_sinc_resample_kernel 把 numpy.dtype 传给 torch.arange 报错
  vendor 后我们对源码有完全控制权（见两处 [VENDOR-FIX] 标记）。
权重（1.5G LFS）不复进 git：vendor 启动时若检测到 pip 包位置有已下载权重，
自动复用，避免重复下载/存储。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 权重复用：避免 vendor + pip 各存 1.5G
# ---------------------------------------------------------------------------
# 优先级：环境变量 > pip 包的 model_repo（最常见的 dev 场景）> 默认
_REPO_DIR_OVERRIDE = os.environ.get("RESEMBLE_ENHANCE_MODEL_REPO")
_VENDOR_ROOT = Path(__file__).resolve().parent

if _REPO_DIR_OVERRIDE:
    _REPO_DIR = Path(_REPO_DIR_OVERRIDE)
else:
    # 自动探测 pip 包位置（macOS + uv 安装的常见路径）
    # _VENDOR_ROOT = <project>/app/core/vendor/resemble_enhance
    # 链上 4 次 .parent 才到 <project>
    _PROJECT_ROOT = _VENDOR_ROOT.parent.parent.parent.parent
    _PIP_PKG_REPO = (
        _PROJECT_ROOT
        / ".venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
        / "resemble_enhance"
        / "model_repo"
    )
    if _PIP_PKG_REPO.exists() and (_PIP_PKG_REPO / "enhancer_stage2").exists():
        _REPO_DIR = _PIP_PKG_REPO
    else:
        # fallback：vendor 自己的 model_repo（首次运行会触发 download）
        _REPO_DIR = _VENDOR_ROOT / "model_repo"

# 把 download.REPO_DIR 改到这里
from .enhancer import download as _dl  # noqa: E402

_dl.REPO_DIR = _REPO_DIR
logger.info("resemble_enhance vendor: model weights at %s", _REPO_DIR)
