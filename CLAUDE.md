# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Defaults

- **默认不支持模型 thinking 模式**：本项目的 LLM 调用（包括示例、文档、代码生成的客户端）默认**不**启用 extended thinking / reasoning 模式。需要时再由调用方显式开启，不要把它当默认行为。

## What this is

`gen-audio` 是一个本地单用户的 TTS「生成 + 批量合成」工具，基于 **ChatTTS 0.2.5**，FastAPI + SQLite + 原生 HTML/JS。流程：在「生成 / 抽卡」页随机/自定义参数试听 → 收藏 → 在「合成」页用卡 + 多段文本批量出 wav。

## Run / dev

启动（自动建 venv + 装 lock 依赖 + uvicorn --reload，端口 9876）：

```bash
./run.sh                 # macOS / Linux（推荐）
```

手动：

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 9876
```

依赖管理：编辑 `requirements.txt`（声明，含说明性注释），然后 `uv pip install -r requirements.txt && uv pip freeze > requirements-lock.txt`。**`requirements-lock.txt` 是部署/复现唯一可信源**。

## Tests

```bash
source .venv/bin/activate
pytest tests/ -v                                  # 全跑（约 240+ 用例）
pytest tests/test_api_synthesize.py -v            # 单文件
pytest tests/test_api_synthesize.py::test_xxx     # 单用例
pytest tests/ -k "speaker"                        # 按关键字
```

`pyproject.toml` 已设 `asyncio_mode = "auto"` + `default_fixture_loop_scope = "function"`，写 async 测试不用加 marker。

## High-level architecture

### 后端分层（`app/`）

- **`main.py`** — FastAPI app + `lifespan`：建 DB、建 `data/speakers/`、**后台异步**加载 ChatTTS 模型（失败不阻塞启动，由 `/api/health` 暴露 `loading|ok|error`）、起合成 worker。同时为 `/draw` `/favorites` `/synthesize` 三个干净 URL 显式注册 FileResponse（StaticFiles `html=True` 不支持无后缀查找）。
- **`api/`** — 路由薄层，按资源拆：`draw` `cards` `card_import` `synthesize` `jobs` `speakers` `health`。每个文件一个 router，所有 prefix 都是 `/api`。
- **`core/`** — 业务逻辑：
  - `params.py`：所有 Pydantic v2 模型（请求/响应/`TtsParams`/`Job`/speakers）。`extra="forbid"`。
  - `chat_tts.py`：模型 module-level 单例。`load_model()` / `is_model_loaded()` / `draw_one_from_params()` / `synthesize_to_wav_bytes(params, text, on_progress)`（Phase 7 起：normalize → chunk → 单段重试 → 拼接，详见下方"长文本切分流水线"）。`_infer_audio(params, text)` 是测试 mock 锚点（保留 2-arg 签名）。
  - `text_chunker.py` / `audio_concat.py` / `text_norm.py`：长文本切分流水线（Phase 7）的三件套；TN 是可选依赖（WeTextProcessing），未装时 fallback 到原文。
  - `queue.py`：asyncio 合成队列。`init_queue(db_path)` 起 N 个 worker（CUDA 自由显存 < 4G 或 CPU 强制 N=1，否则 `MAX_CONCURRENT_SYNTHESIS` 默认 2）。worker 错误**写入 DB `status=failed, error=...`**，不静默吞。
  - `enhance.py` + `vendor/resemble_enhance/`：vendored 一份 resemble-enhance 0.0.1（带 `[VENDOR-FIX]` 标记的两处 scipy/torch 兼容修补，详见 `vendor/NOTICE.md`）。`run_enhance` 出口把 torch.Tensor 转 numpy 给下游。
  - `migrations.py`：用 `PRAGMA user_version` 做 v0→v1→v2 幂等升级。**新加 schema 改动只在这里加**（不要直接改 `db/database.py:SCHEMA_SQL`，那个是历史 v0 baseline）。
  - `text_utils.py` `subtitle.py` `exceptions.py`（`AppError` + 各业务错误码，统一 JSON handler 在 `main.py`）。
- **`db/queries.py`** — 全部 SQL 在这里，业务层只调函数。`db/database.py:get_connection` 是 ctxmgr，**已开 `PRAGMA foreign_keys = ON`**。
- **`storage/files.py`** — 音频/字幕/`params.json` 落盘到 `data/audio/<card_id>/...` 或 `data/audio/<card_id>/jobs/<job_id>/...`。**DB 里只存相对 POSIX 路径**，由调用方拼 `data_root`。
- **`storage/speakers.py`** — `data/speakers/<id>.pt` 文件管理。

### 数据存储

- `data/gen-audio.db`（SQLite；表：`cards`、`synthesis_jobs`、`speakers`）
- `data/audio/<card_id>/{demo.wav, demo.srt, params.json}` + `.../jobs/<job_id>/{audio.wav, subtitle.srt, params.json}`
- `data/speakers/<id>.pt`
- `asset/`（首次启动时下载的 ChatTTS 0.2.5 权重，~500MB）

### 关键设计决策（不要重新讨论）

- **路径绑定坑**：路由模块里要写 `from .. import config` 然后用 `config.DB_PATH`，**不要** `from ..config import DB_PATH`。后者把路径在 import 时绑死，测试 `monkeypatch.setattr(config, "DB_PATH", ...)` 不生效。`app/main.py` 的 `lifespan` 同样有这个问题——测试要让 lifespan 走 tmp DB，**必须同时** `monkeypatch.setattr(app.main, "DB_PATH", tmp_path/...)`。
- **音色双轨引用**：`cards.speaker` (TEXT 快照，base64 字符串) + `cards.speaker_id` (FK→speakers，可空)。删音色时 FK 应用层 `SET NULL`（SQLite 不能 `ALTER TABLE ADD COLUMN ... REFERENCES`），字符串快照保留。`TtsParams.speaker_id` 优先于 `speaker`，路由层 `_resolve_speaker_id` 用 id 查库覆盖字符串。
- **`TtsParams.speaker: str` 必填**（非 Optional）：合成页用 `speaker_id` 时前端要塞 `speaker: ""` 占位，由 `_resolve_speaker_id` 覆盖。`DrawRequest.speaker: str | None` 可空，draw 页 `delete p.speaker` 让后端走随机。
- **`speed: int`（0–10）** + `field_validator(mode="before")` 兼容老 `"[speed_X]"` 字符串数据。
- **draw 试听强制不增强**：`draw.py` 用 `params.model_copy(update={"enhance_audio": False, "denoise_audio": False})` 覆盖；增强字段只对 synthesize 生效。
- **测试 mock 提示**：`speakers.py` 用 `from ..core.chat_tts import _random_speaker` 把名字绑到 speakers 模块；测试要 patch `app.api.speakers._random_speaker` 而不是 `chat_tts._random_speaker`。
- **vendor 更新流程**：要刷新 `app/core/vendor/resemble_enhance/` 时按 `vendor/NOTICE.md` 末尾 rsync 步骤同步上游 + 重新应用 3 处 `[VENDOR-FIX]` 标记。`.gitignore` 已排除 `model_repo/` 防权重误 commit。
- **长文本切分流水线**（Phase 7）：`synthesize_to_wav_bytes` 不是单次 infer，而是一条流水线 ——`text_norm.normalize_text` → `text_chunker.split_text` →（每段独立 `_infer_audio` + 重试 + 塌缩检测）→ `audio_concat.concat_with_pauses`（段间 0.12s 静音）。**对外仍然 1 条 `synthesize_jobs` = 1 个 task**，chunks 只活在 worker 内部。强制 `_MODEL.infer(split_text=False)` 防双层切分把字幕错乱；强制 `skip_refine_text=True` + 忽略 `refiner_text`（refine 易音色漂移）。任意段重试耗尽 → 抛 `ChunkSynthesisError(第 i/n 段...)` → worker 标 FAILED 写到 DB error，不静音兜底。所有阈值通过 `app/config.py` 的 `TEXT_CHUNK_*` / `TEXT_NORM_*` 调（消费方必须 `from .. import config; config.TEXT_CHUNK_SOFT_MAX`，路径绑定坑同样适用）。`segments` 现在是 `[(chunk_text, start, end), ...]` 直接喂给 `subtitle.build_srt`。详见 `docs/superpowers/plans/2026-06-13-long-text-chunking.md`。
- **首段参考音频**（Phase 8，spk_smp 二段法）：多段任务时把第一个达标段（≥ `TEXT_CHUNK_REF_MIN_CHARS` 字）的 wav 用 `_MODEL.sample_audio_speaker(wav)` 编码成 `spk_smp` 注入后续段，强化跨段音色一致性。开关 `TEXT_CHUNK_USE_FIRST_AS_REF` 默认 true；用户已传 `params.spk_smp` 时自动让位；编码失败（WARNING + 后续段裸跑）/ 单段任务零开销。每段额外 +10–15% 推理开销，关闭开关可立即回退到 Phase 7 行为。详见 `docs/superpowers/plans/2026-06-13-spk-smp-cross-chunk-consistency.md`。
- **TN 是优化项不是必需项**：`text_norm` 顶部 try-import `WeTextProcessing`，未装时 `is_loaded()` 永远 False，`normalize_text` return 原文不抛。lifespan 用 `asyncio.create_task` 后台跑 `text_norm.load_normalizer`（FST 编译 5–30s），与 ChatTTS 模型加载并行。`/api/health` 暴露 `tn_status: loading|ok|error|disabled`。

### 前端（`static/`）

原生 HTML/JS，无打包。三页：`draw.html`（生成/抽卡）、`synthesize.html`（合成）、`favorites.html`（卡片列表）。

- `static/js/api.js`：fetch 封装，第四参数 `isForm=true` 走 multipart（上传 .pt 用）。
- `static/js/components/param-panel.js`：`renderParamPanel(container, {initial, mode, showSeed})`。`mode='draw'` 灰显增强分区，`mode='synthesize'` 启用。合成页 `showSeed=false`（seed 在抽卡时已定）。
- `static/js/components/speaker-picker.js`：弹窗（搜索 / 仅收藏 / 上传 / 切收藏）。选用时 `GET /api/speakers/{id}` 拿 `tensor_base64`（list 接口不带 tensor 节省带宽）。

## Docs / specs

- 设计：`docs/superpowers/specs/2026-06-11-gen-audio-design.md`
- 实现计划：`docs/superpowers/plans/2026-06-11-gen-audio.md`
- ChatTTS-Enhanced 移植计划（已全部 merge）：`docs/superpowers/plans/2026-06-12-chatts-enhanced-port.md`
- 长文本切分流水线（Phase 7）：`docs/superpowers/plans/2026-06-13-long-text-chunking.md`
- 首段参考音频 / spk_smp 二段法（Phase 8）：`docs/superpowers/plans/2026-06-13-spk-smp-cross-chunk-consistency.md`（执行计划：`docs/superpowers/plans/2026-06-13-spk-smp-execution.md`）
