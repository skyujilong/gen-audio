# gen-audio

本地单用户 TTS「抽卡 + 批量合成」工具，基于 [2noise/ChatTTS](https://github.com/2noise/ChatTTS) v0.2.5。

## 功能

- **抽卡**：随机生成 ChatTTS 参数包（seed / 温度 / top_p / top_k / 声音），自动合成一段 demo 试听
- **收藏**：喜欢的参数卡可收藏，方便复用
- **批量合成**：用选中的卡 + 任意文本批量提交合成任务；任务队列保护 GPU 显存
- **参数导入**：从 JSON 导入参数卡

## 环境要求

| 依赖 | 版本 |
|---|---|
| Python | >= 3.12 |
| ChatTTS | >= 0.2.5 |
| PyTorch | >= 2.0 |

> ChatTTS 0.2.5 的 API 与 0.1.x 不兼容，本项目的 `app/core/chat_tts.py` 已适配新版。
> macOS 用户建议安装 [uv](https://docs.astral.sh/uv/) 以加速依赖安装。

## 快速开始

### 一键启动

**macOS / Linux：**

```bash
./run.sh
```

**Windows：**

双击 `run.bat`，或：

```bat
run.bat
```

启动脚本会自动创建 `.venv` 虚拟环境并安装依赖。浏览器打开 <http://127.0.0.1:8000>。

### 手动启动

```bash
# 1. 创建虚拟环境
uv venv .venv --python 3.12    # 推荐用 uv
# 或: python3.12 -m venv .venv

# 2. 激活
source .venv/bin/activate      # macOS/Linux
# .venv\Scripts\activate       # Windows

# 3. 安装依赖（锁定版本）
uv pip install -r requirements-lock.txt
# 或: pip install -r requirements-lock.txt

# 4. 启动
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 依赖说明

| 文件 | 用途 |
|---|---|
| `requirements.txt` | 声明依赖及最低版本，供人工阅读 |
| `requirements-lock.txt` | 锁定全部精确版本（含子依赖），部署/复现用 |

更新依赖时修改 `requirements.txt`，然后重新生成 lock：

```bash
uv pip install -r requirements.txt
uv pip freeze > requirements-lock.txt
```

## 首次启动

首次启动时会自动从 HuggingFace 下载 ChatTTS 模型文件到 `asset/` 目录（约 500MB），下载时间取决于网络速度。模型加载完成后 `/api/health` 会返回 `status: "ok"`。

## 使用流程

1. 去「抽卡」页点 `🎲 抽一张` → 试听 demo → `⭐ 收藏` 或 `🗑 丢弃`
2. 去「合成」页选卡 + 输入多段文本 → `🚀 全部提交` → 等待合成 → 试听 / 下载
3. 去「列表」页查看所有参数卡，tab 切收藏

## 测试

```bash
source .venv/bin/activate
pytest tests/ -v
```

## 数据存储

| 路径 | 内容 |
|---|---|
| `data/gen-audio.db` | SQLite 数据库（卡片、任务） |
| `data/audio/<card_id>/...` | 音频 / 字幕 / 参数快照 |
| `asset/` | ChatTTS 模型文件（自动下载） |

重启应用数据保留；删除 `data/` 目录可清空所有数据。

## ChatTTS 版本适配

本项目适配 ChatTTS 0.2.5，相比 0.1.x 的主要 API 变更：

| 0.1.x | 0.2.5 |
|---|---|
| `chat.load_models()` | `chat.load()` |
| 手动 `_LOADED` 标记 | `chat.has_loaded()` |
| 手动构造 speaker | `chat.sample_random_speaker()` |
| `chat.infer(text, ...)` | `chat.infer(text, params_infer_code=..., params_refine_text=...)` |
| 输出 16kHz | 输出 24kHz |

适配代码在 `app/core/chat_tts.py`。

## 文档

- 设计文档：`docs/superpowers/specs/2026-06-11-gen-audio-design.md`
- 实现计划：`docs/superpowers/plans/2026-06-11-gen-audio.md`

## 技术栈

Python 3.12 · FastAPI · Pydantic v2 · SQLite · ChatTTS 0.2.5 · PyTorch · asyncio · 原生 HTML/JS · pytest
