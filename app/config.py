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
