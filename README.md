# gen-audio

本地单用户 TTS「生成 + 批量合成」工具，基于 [2noise/ChatTTS](https://github.com/2noise/ChatTTS) v0.2.5。

## 功能

- **生成 / 抽卡**：随机或自定义 ChatTTS 参数（seed / 温度 / top_p / top_k / 语速 / oral / laugh / break / 音色），自动合成一段 demo 试听
- **音色管理**：上传 / 收藏 / 命名 `.pt` speaker 文件，复用同一个音色出多张卡
- **收藏**：喜欢的参数卡可收藏，方便复用
- **批量合成**：用选中的卡 + 任意文本批量提交合成任务；任务队列保护 GPU 显存
- **长文本切分流水线**（Phase 7+）：长文本自动 normalize → chunk → 单段重试 → 拼接，对外仍是 1 个任务
- **跨段音色一致性增强**（Phase 8）：多段任务时把首段做参考音频喂给后续段（spk_smp 二段法）
- **音频增强 / 降噪**（可选）：vendored resemble-enhance 0.0.1，部分环境受限，详见下面
- **参数导入**：从 JSON 导入参数卡

## 环境要求

| 依赖 | 版本 |
|---|---|
| Python | >= 3.12 |
| ChatTTS | >= 0.2.5 |
| PyTorch | >= 2.0 |
| openfst (macOS) | 任意（仅文本规范化用，可选） |

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

启动脚本会自动创建 `.venv` 虚拟环境并安装依赖。浏览器打开 <http://127.0.0.1:9876>。

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
uvicorn app.main:app --reload --host 127.0.0.1 --port 9876
```

### macOS 用户：安装 openfst（文本规范化用）

文本规范化（TN）用 `WeTextProcessing` → `pynini` → openfst C++ 库。**没装 openfst，pip 编译 pynini 会失败**，最终结果是数字 / 日期 / 单位**不会被转成中文念法**（`/api/health` 会显示 `tn_status: "disabled"`，合成仍可用、只是数字读音不稳）。

```bash
brew install openfst

# 然后重装 WeTextProcessing 让它能找到头文件
PREFIX=$(brew --prefix openfst)
CPPFLAGS="-I${PREFIX}/include" \
LDFLAGS="-L${PREFIX}/lib" \
CXXFLAGS="-std=c++17 -I${PREFIX}/include" \
uv pip install --reinstall WeTextProcessing
```

Linux 通常 `pip install WeTextProcessing` 直接 work；Windows 推荐 conda：`conda install -c conda-forge pynini`。

> TN 是优化项不是必需项。装不上也能跑，只是数字 / 日期 / 单位的中文念法走 ChatTTS 内置兜底（不太稳定）。

### 环境变量配置

复制 `.env.example` 为 `.env.local`，按需改默认值，`./run.sh` 启动时会自动加载：

```bash
cp .env.example .env.local
# 改 .env.local 里的 TEXT_CHUNK_*、TEXT_NORM_*、MAX_CONCURRENT_SYNTHESIS 等
./run.sh
```

不创建 `.env.local` 时使用 `app/config.py` 里的默认值。

### 依赖说明

| 文件 | 用途 |
|---|---|
| `requirements.txt` | 声明依赖 + 安装注意事项注释，供人工阅读 |
| `requirements-lock.txt` | 锁定全部精确版本（含子依赖），部署/复现唯一可信源 |

更新依赖时修改 `requirements.txt`，然后重新生成 lock：

```bash
uv pip install -r requirements.txt
uv pip freeze > requirements-lock.txt
```

> ⚠️ `resemble-enhance==0.0.1` 在 metadata 里硬 pin `torch==2.1.1` + `cp311`，**故意不列在 lock 里** —— 进 lock 会让 `uv pip install -r requirements-lock.txt` 在新机器上 resolve 失败。需要它请按 `requirements.txt` 注释里的 `--no-deps` 步骤手动装。

## 首次启动

首次启动时会自动从 HuggingFace 下载 ChatTTS 模型文件到 `asset/` 目录（约 500MB），下载时间取决于网络速度。模型加载完成后 `/api/health` 会返回：

```json
{
  "status": "ok",
  "model_loaded": true,
  "queue_size": 0,
  "tn_status": "ok"        // loading | ok | error | disabled
}
```

`tn_status` 含义：

| 值 | 含义 |
|---|---|
| `loading` | FST 还在编译（5–30s 首次启动会出现） |
| `ok` | 文本规范化可用，数字/日期/单位会转中文念法 |
| `error` | 加载失败（看 server log）；TN 不可用，但合成仍能跑 |
| `disabled` | 没装 `WeTextProcessing` 或 `TEXT_NORM_ENABLED=false`；TN 不可用 |

## 使用流程

1. 去「生成」页点 `🔊 生成` → 试听 demo → `⭐ 收藏` 或 `🗑 丢弃`
2. 去「合成」页选卡 + 输入多段文本 → `🚀 全部提交` → 等待合成 → 试听 / 下载
3. 去「列表」页查看所有参数卡，tab 切收藏；右侧「音色」tab 管理 .pt 文件

## 长文本切分流水线（Phase 7+）

`synthesize_to_wav_bytes` 不是单次 infer，而是一条流水线：

```
text_norm.normalize_text     ←  WeTextProcessing 规范化数字/日期/单位
        ↓
text_chunker.split_text      ←  按标点切成 ~15-20 字短段
        ↓
每段独立 _infer_audio + 重试 + 塌缩检测
        ↓
audio_concat.concat_with_pauses  ←  段间 0.12s 静音
        ↓
[(text, start, end), ...]    →  build_srt（字幕时间轴对齐）
```

对外仍是「1 个任务 = 1 条 `synthesize_jobs` 记录」，chunks 只活在 worker 内部。任意段重试耗尽 → 整个 job 标 FAILED 写错误信息到 DB（不静音兜底）。所有阈值通过 `.env.local` 的 `TEXT_CHUNK_*` / `TEXT_NORM_*` 环境变量调，详见 `.env.example`。

### 音色一致性的现状（说在前面）

ChatTTS 0.2.5 的 GPT 解码本质是采样过程，即便 `spk_emb` + `manual_seed` 全程一致，不同段的语义也会让 timbre / prosody 微变。本项目做了几层缓解：

1. **段内**：每段重试时 `seed = base_seed + attempt`，避开同一塌缩路径；
2. **段间**：抽卡时 sample 出来的 speaker embedding 全程不变；seed=0 时启动前固定一次；
3. **跨段（Phase 8 spk_smp 二段法）**：第 1 个达标段（默认 ≥ 8 字）合成完后用 `_MODEL.sample_audio_speaker(wav)` 编码为 `spk_smp` 注入后续段，让后续段「模仿第 1 段」。开关 `TEXT_CHUNK_USE_FIRST_AS_REF=true`（默认开），用户已传 `params.spk_smp` 时自动让位。

⚠️ **即便如此，长文本跨段音色仍可能有可感漂移** —— 这是 ChatTTS 本身的采样性质决定的，不是流水线 bug。当前可调的几个旋钮：

- `TEXT_CHUNK_REF_MIN_CHARS`：调高（如 12-15）让参考段更"代表性"，可能稍稳但牺牲短首段任务覆盖率
- `TEXT_CHUNK_USE_FIRST_AS_REF=false`：完全关掉二段法回到 Phase 7 行为（如果发现 spk_smp 反而让某些情况更糟）
- 自己上传一份 .pt 音色用 `params.spk_smp` 显式传参考音频，跳过自动二段法
- 缩短文本（5-10 段以内通常较稳）

如果你要做"严格一致"的成片合成，目前最稳的姿势是：先单独抽一张你满意的卡（带 `seed`），然后**用同一张卡跑短一点的段落**，主观听感差异最小。

## 测试

```bash
source .venv/bin/activate
pytest tests/ -v             # 全跑（约 320 用例）
pytest tests/test_chat_tts_chunked.py -v   # 长文本切分 + spk_smp 二段法
pytest tests/ -k "speaker"   # 按关键字
```

## 数据存储

| 路径 | 内容 |
|---|---|
| `data/gen-audio.db` | SQLite 数据库（cards / synthesis_jobs / speakers） |
| `data/audio/<card_id>/` | demo + params 快照（抽卡试听产物） |
| `data/audio/<card_id>/jobs/<job_id>/` | 合成任务的 wav / srt / params |
| `data/speakers/<id>.pt` | 上传 / 收藏的音色文件 |
| `data/wetext_cache/` | WeTextProcessing 编译好的 FST（5-30s 编译，缓存命中后秒起） |
| `asset/` | ChatTTS 模型文件（首次启动自动下载，~500MB） |

重启应用数据保留；删除 `data/` 目录可清空所有数据。删 `data/wetext_cache/` 会让下次启动重新编译 FST（5-30s）。

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
- 长文本切分流水线（Phase 7）：`docs/superpowers/plans/2026-06-13-long-text-chunking.md`
- 首段参考音频 / spk_smp 二段法（Phase 8）：`docs/superpowers/plans/2026-06-13-spk-smp-cross-chunk-consistency.md`
- 给 Claude Code 用的项目说明：`CLAUDE.md`

## 技术栈

Python 3.12 · FastAPI · Pydantic v2 · SQLite · ChatTTS 0.2.5 · PyTorch · WeTextProcessing · resemble-enhance (vendored) · asyncio · 原生 HTML/JS · pytest
