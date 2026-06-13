"""全局配置：DB 路径、data 根目录等。

约定：
- 所有路径都用 `pathlib.Path`。
- 测试可通过 `monkeypatch.setattr` 覆盖 `DATA_ROOT` / `DB_PATH`。
"""
from __future__ import annotations

import os
from pathlib import Path


# === 路径常量 ===

# 项目根目录 = 本文件所在目录的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 数据根目录：默认 <PROJECT_ROOT>/data；可通过环境变量 DATA_ROOT 覆盖
DATA_ROOT = Path(os.getenv("DATA_ROOT", str(PROJECT_ROOT / "data")))

# SQLite 数据库路径：<DATA_ROOT>/gen-audio.db
DB_PATH = DATA_ROOT / "gen-audio.db"

# 音色库目录：<DATA_ROOT>/speakers（每个音色一份 .pt 文件）
SPEAKERS_DIR = DATA_ROOT / "speakers"

# 前端静态文件目录
STATIC_DIR = PROJECT_ROOT / "static"


# === 长文本切分流水线配置 ===
#
# 测试 monkeypatch 提示：消费方应当用 `from .. import config; config.TEXT_CHUNK_SOFT_MAX`
# 而不是 `from ..config import TEXT_CHUNK_SOFT_MAX` —— 后者把值在 import 时绑死，
# `monkeypatch.setattr(config, "TEXT_CHUNK_SOFT_MAX", ...)` 不生效。
# 详见 CLAUDE.md「路径绑定坑」一节。

# --- 文本规范化（TN, Text Normalization） ---
TEXT_NORM_ENABLED = os.getenv("TEXT_NORM_ENABLED", "true").lower() == "true"
"""WeTextProcessing 文本规范化总开关。关闭后 normalize_text 直接返回原文。"""

TEXT_NORM_CACHE_DIR = Path(
    os.getenv("TEXT_NORM_CACHE_DIR", str(DATA_ROOT / "wetext_cache"))
)
"""FST 编译缓存目录。首次启动慢，后续读 cache 加速。"""

TEXT_NORM_REMOVE_ERHUA = os.getenv("TEXT_NORM_REMOVE_ERHUA", "false").lower() == "true"
"""是否在规范化时去掉「儿化音」后缀（默认保留）。"""

# --- 切分参数 ---
TEXT_CHUNK_SOFT_MAX = int(os.getenv("TEXT_CHUNK_SOFT_MAX", "20"))
"""日常目标段长。中文 15-20 字是 ChatTTS 最稳定的甜点区。超过此值会用更弱的标点继续切。"""

TEXT_CHUNK_HARD_MAX = int(os.getenv("TEXT_CHUNK_HARD_MAX", "35"))
"""硬切兜底上限。仅当一段无标点且超过此值时按字数硬砍。"""

TEXT_CHUNK_MIN_CHARS = int(os.getenv("TEXT_CHUNK_MIN_CHARS", "4"))
"""短段阈值。短于此值的段尝试和后一段合并（合并后超 soft_max 则不合并）。"""

# --- 段间静音 ---
TEXT_CHUNK_PAUSE_SEC = float(os.getenv("TEXT_CHUNK_PAUSE_SEC", "0.12"))
"""段与段之间插入的静音长度（秒）。最后一段后不加。"""

# --- 重试 + 塌缩检测 ---
TEXT_CHUNK_MAX_RETRIES = int(os.getenv("TEXT_CHUNK_MAX_RETRIES", "2"))
"""单段重试次数（不含首次）。每次重试 seed = base_seed + attempt 避开同一塌缩路径。"""

TEXT_CHUNK_COLLAPSE_RATIO = float(os.getenv("TEXT_CHUNK_COLLAPSE_RATIO", "0.05"))
"""塌缩阈值：|wav| > 1e-5 的样本占比 < 此值即视为塌缩，触发重试。"""

# --- 段数上限保护 ---
TEXT_CHUNK_MAX_SEGMENTS = int(os.getenv("TEXT_CHUNK_MAX_SEGMENTS", "50"))
"""单 job 最大段数。防超长文本独占 worker。切完超过此值 → 抛 ValueError。"""

# --- 首段参考音频（Phase 8：跨段音色一致性，spk_smp 二段法）---
TEXT_CHUNK_USE_FIRST_AS_REF = (
    os.getenv("TEXT_CHUNK_USE_FIRST_AS_REF", "true").lower() == "true"
)
"""是否启用「首段做参考音频」二段法。多段任务（chunks > 1）且用户没传
`params.spk_smp` 时生效：第一个长度达标段合成完后用 `sample_audio_speaker`
编码为 spk_smp，后续段全注入。单段任务零开销；用户传 spk_smp 时让位。"""

TEXT_CHUNK_REF_MIN_CHARS = int(os.getenv("TEXT_CHUNK_REF_MIN_CHARS", "8"))
"""最短参考段字数。短于此值的段不当参考，跳到下一段；防止 1-2 字的极短句
编码出来的 spk_smp 信息量不足以稳定后续段。"""
