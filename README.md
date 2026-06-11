# gen-audio

本地单用户 TTS「抽卡 + 批量合成」工具，基于 [2noise/ChatTTS](https://github.com/2noise/ChatTTS)。

## 功能

- **抽卡**：随机生成 ChatTTS 参数包（seed / 温度 / top_p / top_k / 声音），自动合成一段 demo 试听
- **收藏**：喜欢的参数卡可收藏，方便复用
- **批量合成**：用选中的卡 + 任意文本批量提交合成任务；任务队列保护 GPU 显存
- **参数导入**：从 JSON 导入参数卡

## 快速开始

### Windows

双击 `run.bat`，或：

```bat
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### macOS / Linux

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。

**首次启动**会加载 ChatTTS 模型到内存，需要 5-15 秒（取决于机器配置）。

## 使用流程

1. 去「抽卡」页点 `🎲 抽一张` → 试听 demo → `⭐ 收藏` 或 `🗑 丢弃`
2. 去「合成」页选卡 + 输入多段文本 → `🚀 全部提交` → 等待合成 → 试听 / 下载
3. 去「列表」页查看所有参数卡，tab 切收藏

## 测试

```bash
pytest tests/ -v
```

## 数据存储

- SQLite 数据库：`data/gen-audio.db`
- 音频 / 字幕 / 参数快照：`data/audio/<card_id>/...`

重启应用数据保留；删除 `data/` 目录可清空所有数据。

## 文档

- 设计文档：`docs/superpowers/specs/2026-06-11-gen-audio-design.md`
- 实现计划：`docs/superpowers/plans/2026-06-11-gen-audio.md`

## 技术栈

FastAPI · Pydantic v2 · SQLite · 2noise/ChatTTS · asyncio · 原生 HTML/JS · pytest
