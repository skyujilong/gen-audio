# 抽卡与合成面板全面升级：移植 ChatTTS-Enhanced 范式 + 引入音色库

## Context

当前 gen-audio 项目的抽卡（`/draw`）与合成（`/synthesize`）两个页面已能跑通，但 UI 与参数控制相对简陋：

- **5 个核心滑条**（温度/Top_P/Top_K/Repetition_Penalty + 语速下拉），缺少 ChatTTS 关键的 `Oral` / `Laugh` / `Break` 语义化控制。
- **Speaker 是不可见的 base64 字符串**，用户无法命名、无法管理、无法二次复用。
- **抽卡同步阻塞**，无进度条；前端缺少与 ChatTTS 0.2.5 API 对齐的 refine_text 控制。
- **没有"音色库"概念**，抽到喜欢的声音只能靠收藏整张卡，体验割裂。

而 `/Users/nbe01/workspace/ChatTTS-Enhanced-main`（Gradio WebUI）已经在这些点上做得成熟：
- 7 个滑条（Speed/Oral/Laugh/Break/Temp/Top_P/Top_K）的精细控制
- `params_infer_code = {'prompt': '[speed_X]'}` + `params_refine_text = {'prompt': '[oral_X][laugh_X][break_X]'}` 范式
- `replace_tokens`/`restore_tokens` 保护控制字符
- 音色配置保存/加载
- `sample_random_speaker()` 高斯采样

**目标**：把上述范式完整移植到 gen-audio，重做抽卡与合成两个页面（两栏布局 + 共享参数面板），并引入"音色库"概念（speaker 文件存储 + 命名/标签/收藏/上传），让用户能够真正管理自己的"声音卡片组"。

## 关键设计决策

| 决策点 | 选择 |
|---|---|
| 抽卡页 UI | **完全重做**（两栏布局 + 6 个 Row 区块 + 音色库入口） |
| 合成页 UI | 改造（嵌入与抽卡页共享的 `param-panel` 组件） |
| 音色库 | **做**（新增 `speakers` 表 + `.pt` 文件存储 + 命名/标签/收藏/上传） |
| 长文本切分 | **不做**（本期不引入 `split_text` / `num2text` / `normalize_zh`） |
| 降噪 / 音频增强 | **做**。pip `resemble-enhance`；draw 试听**不增强**，仅 synthesize 正式合成时增强 |
| 增强采样率 | 增强后输出 **44100Hz**（ChatTTS 原始 24000Hz），`_numpy_to_wav_bytes` 改为支持可变 `sample_rate` |
| 异步化 / 进度推送 | **不做**（draw 同步阻塞保留，synthesize 仍走 `startPolling` 2s 轮询） |
| speaker 引用 | **双轨**：`cards.speaker_id`（FK，可空）+ `cards.speaker`（TEXT base64 字符串快照），老数据可读 |
| speed 迁移 | **整数 0–10**，老字符串 `"[speed_5]"` 由 Pydantic validator 自动转 int |
| refine 范式 | **两步**：`refine_text_only=True` 先归一化 → `skip_refine_text=True` 出 wav |
| text_utils | **只抄 `replace_tokens`/`restore_tokens`**，不抄 `split_text`/`num2text` |
| migration | `app/core/migrations.py` 用 `PRAGMA user_version` 走 v0→v1→v2 幂等升级 |

## 实施步骤（拆分到原子级，可逐步推进）

> **节奏建议**：每个步骤完成后跑一次相关测试，**通过后再进入下一步**。一个 phase 完成后做一次 git commit。  
> **共 6 个 phase / 21 个子步骤 / 7 个建议 commit 节点**。

---

### Phase 1：基础 schema + 参数模型（独立可验证）

**目标**：把数据层和参数层准备好，零业务逻辑变更。

| # | 子步骤 | 涉及文件 | 产物 | 验证 |
|---|---|---|---|---|
| 1.1 | 拉分支 `feature/chatts-enhanced-port` | 终端 | git 分支就绪 | `git branch --show-current` |
| 1.2 | `TtsParams` 加 `oral/laugh/break_` 整数字段，`speed` 改 int（默认 5），Pydantic validator 兼容老字符串 `"[speed_5]"` | `app/core/params.py` | 字段 + 兼容逻辑 | `pytest tests/test_params.py -v` |
| 1.3 | 新增 `SpeakerBase/Create/Update/Out/ListItem` Pydantic 模型 | `app/core/params.py` | CRUD 模型 | 单元测试覆盖 |
| 1.4 | 新增 `app/core/migrations.py`：用 `PRAGMA user_version` 实现 v0→v1→v2 幂等升级 | `app/core/migrations.py`（新） | migration 框架 | `tests/test_migrations.py` |
| 1.5 | `database.py` 改造：`init_schema` → `migrate` wrapper；v1 建 `speakers` 表 + 索引；v2 给 `cards` 加可空 `speaker_id` 列 | `app/db/database.py` | schema 升级逻辑 | 老库 + 新库都跑通 |
| 1.6 | `queries.py` 加 speakers CRUD 函数（`insert_speaker / get_speaker / list_speakers / update_speaker / delete_speaker / toggle_speaker_favorite`）；`insert_card` 加可选 `speaker_id` 参数 | `app/db/queries.py` | DB 函数 | `tests/test_queries.py` |

**Phase 1 提交节点**：`chore(db): v0→v2 schema migration + TtsParams 新增 oral/laugh/break_/speed=int`

**Phase 1 验收**：
- `pytest tests/test_params.py tests/test_migrations.py tests/test_database.py tests/test_queries.py -v` 全过
- 启动服务 `GET /api/health` 仍 200
- 老的 9 个 draw + 9 个 chat_tts 测试不破

---

### Phase 2：core 推理改造（独立可验证）

**目标**：把 Enhanced 的参数拼装范式和两步 infer 搬进 `chat_tts.py`。

| # | 子步骤 | 涉及文件 | 产物 | 验证 |
|---|---|---|---|---|
| 2.1 | 新增 `app/core/text_utils.py`：实现 `replace_tokens` / `restore_tokens` / `prepare_text_for_refine`（移植自 text_utils.py:236-252） | `app/core/text_utils.py`（新） | 纯函数 | `tests/test_text_utils.py` |
| 2.2 | 抽 `_build_infer_code_params(params) -> InferCodeParams` 内部函数（含 `prompt=f"[speed_{speed}]"` 拼装） | `app/core/chat_tts.py` | 纯函数 | 单测覆盖：speed 5 → `[speed_5]` |
| 2.3 | 抽 `_build_refine_text_params(params) -> RefineTextParams`（`prompt=f"[oral_X][laugh_X][break_X]"` + 补 `top_P/top_K/temperature`）；处理 `refiner_text` 自由文本与 3 整数的互斥 | `app/core/chat_tts.py` | 纯函数 | 单测覆盖：3 种组合 |
| 2.4 | 拆分 `_infer_audio`：`_refine_text(params, text) -> str`（进度 0.0→0.3）+ `_synthesize_audio(params, text) -> np.ndarray`（进度 0.3→1.0）；`replace_tokens` 包裹 refine 输出 | `app/core/chat_tts.py` | 拆分函数 | mock 验证两步调用 |
| 2.5 | `draw_one_from_params` 和 `synthesize_to_wav_bytes` 走新拼装函数（行为对外不变，参数透传） | `app/core/chat_tts.py` | 集成 | 老 9 个 chat_tts 测试不破 |

**Phase 2 提交节点**：`refactor(chat_tts): 移植 Enhanced 的 infer_code/refine_text 拼装范式 + 两步 refine 范式`

**Phase 2 验收**：
- `pytest tests/test_chat_tts.py tests/test_text_utils.py -v` 全过
- 老 chat_tts 测试零修改通过
- 行为对外不变：相同 TtsParams 生成的 wav 与改造前一致（可在 mock 中验证调用参数）

---

### Phase 2.6：音频增强 / 降噪接入（独立可验证）

**目标**：移植 Enhanced 的 `enhance` / `denoise` 后处理（基于 `resemble-enhance`），只在 synthesize 正式合成时生效，draw 试听跳过。

| # | 子步骤 | 涉及文件 | 产物 | 验证 |
|---|---|---|---|---|
| 2.6.1 | `TtsParams` / `DrawRequest` 加 `enhance_audio`(bool=False) / `denoise_audio`(bool=False) / `solver`(str="midpoint") / `nfe`(int=64, 1–128) / `tau`(float=0.5, 0–1)；validator 校验 solver 枚举与 nfe/tau 范围 | `app/core/params.py` | 字段 + 校验 | `tests/test_params.py` 默认值 + 范围 |
| 2.6.2 | 新增 `app/core/enhance.py`：`@cache` 单例 `load_enhancer()` + `run_enhance(audio, sr, *, denoise, solver, nfe, tau) -> (np.ndarray, 44100)`；`lambd=0.9 if denoise else 0.1`（移植自 `enhance_processors.py:24-29`） | `app/core/enhance.py`（新） | 增强函数 | `tests/test_enhance.py`（mock `resemble_enhance.enhancer.inference`） |
| 2.6.3 | `_numpy_to_wav_bytes` 接受 `sample_rate` 参数；`synthesize_to_wav_bytes` 合成后若 `enhance_audio or denoise_audio` → `run_enhance`，进度 0.0→0.6（合成）→1.0（增强） | `app/core/chat_tts.py` | 接入 | 增强分支 mock + 老测试不破 |
| 2.6.4 | **draw 试听强制不增强**：`draw_one_from_params` / draw 路由生成试听音频时不调 `run_enhance`（即便参数里 enhance/denoise=True） | `app/core/chat_tts.py`、`app/api/draw.py` | 抽卡不变慢 | mock 验证 draw 不调增强 |
| 2.6.5 | `requirements.txt` 加 `resemble-enhance`，注释标注 torch==2.1.1 / deepspeed 冲突风险，建议 `--no-deps` 手装 | `requirements.txt` | 依赖 | `import resemble_enhance` 与 ChatTTS 共存验证 |

**Phase 2.6 提交节点**：`feat(enhance): 接入 resemble-enhance 降噪/增强（仅 synthesize 生效）`

**Phase 2.6 验收**：
- `pytest tests/test_enhance.py -v` 全过
- synthesize 设 `denoise_audio=True` → 走增强分支（mock 验证调用）→ 输出 44.1kHz wav
- draw 即便参数带 `enhance_audio=True` 也**不**调用 `run_enhance`（抽卡耗时不变）
- 隔离环境验证：`resemble-enhance` 与 `ChatTTS>=0.2.5` 可同时 import

---

### Phase 3：speakers 存储 + API（独立可验证）

**目标**：音色库的后端 CRUD + 文件 I/O 完整跑通。

| # | 子步骤 | 涉及文件 | 产物 | 验证 |
|---|---|---|---|---|
| 3.1 | `app/storage/speakers.py`：`SpeakerStorage` 类（`dir` / `save_tensor` / `load_tensor` / `load_tensor_bytes` / `delete` / `exists`） | `app/storage/speakers.py`（新） | 文件 I/O 工具 | `tests/test_storage_speakers.py` |
| 3.2 | `app/api/speakers.py` 路由实现：`GET /`、`GET /{id}`、`POST /`（创建）、`POST /upload`（上传 .pt）、`PATCH /{id}`、`DELETE /{id}`（级联）、`POST /{id}/favorite`、`GET /random` | `app/api/speakers.py`（新） | 8 个路由 | `tests/test_api_speakers.py` |
| 3.3 | `app/config.py` 加 `SPEAKERS_DIR = DATA_ROOT / "speakers"` | `app/config.py` | 配置项 | 启动时建目录 |
| 3.4 | `app/main.py` 注册 `speakers.router` + lifespan 建 `SPEAKERS_DIR`；`init_schema` 替换为 `migrate` | `app/main.py` | 路由注册 | `GET /api/speakers` 200 |

**Phase 3 提交节点**：`feat(speakers): 音色库 API + .pt 文件存储`

**Phase 3 验收**：
- `pytest tests/test_api_speakers.py tests/test_storage_speakers.py -v` 全过
- 手动 `curl POST /api/speakers` 创建 → `GET /api/speakers` 列表中可见 → `DELETE` 后列表清空且文件被删
- `GET /api/speakers/random` 返回 `{speaker_id, tensor_base64}`

---

### Phase 4：draw 路由 + synthesize 路由适配（独立可验证）

**目标**：业务路由层与新数据模型打通，**前端可暂时不调**。

| # | 子步骤 | 涉及文件 | 产物 | 验证 |
|---|---|---|---|---|
| 4.1 | `draw.py`：接受 7 个数字字段；`/api/draw/random_speaker` 转发到 `/api/speakers/random`；绑定 speaker 时存 `speaker_id` + 字符串快照 | `app/api/draw.py` | 适配层 | `tests/test_api_draw.py` |
| 4.2 | `synthesize.py`：`req.params.speed` 当 int 用；`speaker_id` 优先于 `speaker` 字符串 | `app/api/synthesize.py` | 适配层 | `tests/test_api_synthesize.py` |
| 4.3 | `cards.py`：`_row_to_list_item` 返回 `speaker_id` 字段 | `app/api/cards.py` | 字段扩展 | 列表 API 返回 `speaker_id` |
| 4.4 | 新增 `tests/test_e2e_draw.py`：建 speaker → 抽卡 → 音频字节 → 合成 → 进度轮询 | `tests/test_e2e_draw.py`（新） | 端到端 | 全链路通过 |

**Phase 4 提交节点**：`feat(api): draw/synthesize 路由适配新参数 + speaker 引用双轨`

**Phase 4 验收**：
- `pytest tests/test_api_draw.py tests/test_api_synthesize.py tests/test_e2e_draw.py -v` 全过
- 老 9 个 draw + 4 个 synthesize 测试不破（兼容 speed 字符串 / 老 speaker 字符串）
- `POST /api/speakers` 建一个音色 → `POST /api/draw`（用 `speaker_id`）→ DB `cards.speaker_id` 正确写入

---

### Phase 5：前端组件 + draw 页重做（独立可验证）

**目标**：抽卡页 UI 完全重做，引入共享 `param-panel` 和 `speaker-picker` 组件。

| # | 子步骤 | 涉及文件 | 产物 | 验证 |
|---|---|---|---|---|
| 5.1 | `static/css/components.css`：滑条、网格、modal 基础样式 | `static/css/components.css`（新） | 样式 | 浏览器打开无样式错乱 |
| 5.2 | `static/js/components/param-panel.js`：`renderParamPanel(container, initial) → {getParams, setParams}` 封装 7 滑条 + 数值框 + 范围绑定 + **增强分区**（增强/降噪 checkbox + solver 下拉 + nfe 滑条 1–128 + tau 滑条 0–1）；draw 页该分区灰显并标注"试听不应用，仅最终合成生效" | `static/js/components/param-panel.js`（新） | 组件 | 控制台 `param-panel` 存在 |
| 5.3 | `static/js/components/speaker-picker.js`：弹下拉/网格，列 `/api/speakers`，支持搜索/收藏/上传；返回 `speaker_id + tensor_base64` | `static/js/components/speaker-picker.js`（新） | 组件 | 弹窗可关可开 |
| 5.4 | `static/js/api.js` 加 speakers 路由 wrapper + `/random` + `/upload`；`/random_speaker` 改走 `/speakers/random` | `static/js/api.js` | API 封装 | `await api.speakers.list()` 成功 |
| 5.5 | 重写 `static/draw.html`：两栏布局 + 6 个 Row 区块 + 音色库入口 | `static/draw.html` | 新页面 | `/draw` 打开样式正确 |
| 5.6 | 重写 `static/js/draw.js`：模块化 `initForm` / `collectFormData` / `updateFormFromParams` / `generate` / `saveSpeakerToLibrary` / `loadSpeakerFromLibrary` / `randomSpeaker` / `discardCard` / `favCard` / `renameCard` | `static/js/draw.js` | 新逻辑 | 浏览器端到端跑通 |

**Phase 5 提交节点**：`feat(draw): 重做抽卡页面 + 引入 param-panel/speaker-picker 组件`

**Phase 5 验收**：
- 手动 `bash run.sh` → 打开 `/draw`
- 7 滑条拖动 → 数值框同步
- 点 "🎲 随机音色" → speaker 出现在 tag 区
- 点 "💾 保存到音色库" → 弹 modal → 命名 → speaker-picker 下拉里出现
- 点 "📚 加载音色库" → 选回 → 表单更新
- 点 "🔊 生成" → 听音频

---

### Phase 6：synthesize 页改造 + 联调（独立可验证）

**目标**：合成页接入共享组件，整链路联调。

| # | 子步骤 | 涉及文件 | 产物 | 验证 |
|---|---|---|---|---|
| 6.1 | `static/synthesize.html` 重写：左栏选卡 + 选 speaker；右栏上方嵌入 `param-panel`；右栏下方任务行 | `static/synthesize.html` | 新页面 | `/synthesize` 打开样式正确 |
| 6.2 | `static/js/synthesize.js` 重写：选卡自动加载 params 到 `param-panel`；改滑条即改提交 params；保留 `startPolling` 2s 轮询 | `static/js/synthesize.js` | 新逻辑 | 任务行进度走动 |
| 6.3 | 端到端联调：选卡 → 改 Oral=5 → 加 3 行任务 → 全部提交 → 进度条 → done → 播放 | 手动 | 整链路 | 全流程无 bug |
| 6.4 | `favorites.html` 加跳转链接 `→ /draw?card_id=...`（小改动） | `static/favorites.html`、`static/js/favorites.js` | 微调 | 收藏列表有跳抽卡入口 |

**Phase 6 提交节点**：`feat(synthesize): 合成页改造 + 接入 param-panel + 端到端联调`

**Phase 6 验收**：
- 手动跑完"Phase 6.3 端到端联调"全部步骤
- `pytest tests/ -v` 全过（无回归）
- 浏览器 dev console 无 error

---

## 完整文件清单

### 新增（14 个）

| 路径 | 估算行数 | 用途 |
|---|---|---|
| `app/core/text_utils.py` | ~40 | `replace_tokens` / `restore_tokens` |
| `app/core/enhance.py` | ~60 | resemble-enhance 降噪/增强封装 |
| `app/core/migrations.py` | ~50 | schema 版本管理 |
| `app/storage/speakers.py` | ~80 | speaker 文件 I/O |
| `app/api/speakers.py` | ~180 | speaker CRUD 路由 |
| `static/js/components/param-panel.js` | ~220 | 7 滑条共享组件 |
| `static/js/components/speaker-picker.js` | ~180 | 音色库下拉/网格 |
| `static/css/components.css` | ~150 | 组件样式 |
| `tests/test_text_utils.py` | ~30 | token 替换测试 |
| `tests/test_enhance.py` | ~50 | 降噪/增强分支 mock 测试 |
| `tests/test_api_speakers.py` | ~150 | 音色库 API 测试 |
| `tests/test_storage_speakers.py` | ~60 | speaker 存储测试 |
| `tests/test_migrations.py` | ~50 | schema 升级路径测试 |
| `tests/test_e2e_draw.py` | ~100 | 端到端测试 |

### 修改（17 个）

| 路径 | 改动点 |
|---|---|
| `app/core/params.py` | TtsParams 加 oral/laugh/break_；speed 改 int；validator 兼容老字符串；新增 Speaker* 模型 |
| `app/core/chat_tts.py` | 抽 `_build_infer_code_params` / `_build_refine_text_params`；`_infer_audio` 改两步范式；`replace_tokens` 包裹；`synthesize_to_wav_bytes` 接增强；`_numpy_to_wav_bytes` 支持可变 sr；draw 试听跳过增强 |
| `app/db/database.py` | `init_schema` → `migrate` wrapper；加 `user_version` |
| `app/db/queries.py` | 加 speakers CRUD；`insert_card` 加 `speaker_id` 可选参数 |
| `app/api/draw.py` | 接受 7 个数字字段；`/random_speaker` 转发；试听生成跳过增强 |
| `app/api/synthesize.py` | `req.params.speed` 当 int |
| `app/api/cards.py` | `_row_to_list_item` 返回 `speaker_id` |
| `app/main.py` | 注册 speakers 路由；启动建 SPEAKERS_DIR；调 `migrate` |
| `app/config.py` | 加 `SPEAKERS_DIR` |
| `static/js/api.js` | 加 speakers wrapper；`/random_speaker` → `/speakers/random` |
| `requirements.txt` | 加 `resemble-enhance`（标注 torch==2.1.1/deepspeed 冲突风险） |
| `static/css/main.css` | 加 :root 变量、按钮配色、网格类 |
| `static/favorites.html` | 卡片详情加跳转 `/draw?card_id=...` |
| `tests/test_params.py` | 新字段默认值；老 speed 兼容；`speaker_id` 字段；增强 5 字段默认值 + solver/nfe/tau 范围校验 |
| `tests/test_chat_tts.py` | `_build_*_params` 单测；两步范式 mock |
| `tests/test_api_draw.py` | 7 字段路径；`/random` 新格式 |
| `tests/test_api_synthesize.py` | speed=int；整 7 字段 |

### 重写（4 个）

| 路径 | 重写原因 |
|---|---|
| `static/draw.html` | 两栏布局 + 6 个 Row 区块 + 音色库入口 |
| `static/js/draw.js` | 拆 collectFormData / updateFormFromParams / 音色保存 / 进度显示 |
| `static/synthesize.html` | 嵌入 `param-panel`；任务行交互微调 |
| `static/js/synthesize.js` | 集成 `param-panel`；保留 `startPolling` |

### 不动（保留兼容）

- `static/favorites.js` 主体不动
- `static/index.html`
- `app/api/jobs.py`、`app/api/card_import.py`、`app/api/health.py`
- `app/core/queue.py`、`app/core/subtitle.py`、`app/core/exceptions.py`
- `app/storage/files.py`
- 其余 test 文件

## 7 个建议 commit 节点

```
commit 1: chore(db): v0→v2 schema migration + TtsParams 新增 oral/laugh/break_/speed=int
commit 2: refactor(chat_tts): 移植 Enhanced 的 infer_code/refine_text 拼装范式 + 两步 refine 范式
commit 3: feat(enhance): 接入 resemble-enhance 降噪/增强（仅 synthesize 生效）
commit 4: feat(speakers): 音色库 API + .pt 文件存储
commit 5: feat(api): draw/synthesize 路由适配新参数 + speaker 引用双轨
commit 6: feat(draw): 重做抽卡页面 + 引入 param-panel/speaker-picker 组件
commit 7: feat(synthesize): 合成页改造 + 接入 param-panel + 端到端联调
```

每个 commit 都应能跑通 `pytest tests/ -v` 且 `bash run.sh` 启动后 `/api/health` 200。

## 风险 / 边界

1. **老 speed 字符串兼容**：Pydantic v2 `field_validator(mode="before")` 用正则 `re.search(r'\[speed_(\d+)\]', v)` 提数字；老 JSON `"[speed_5]"` 自动转 5，新前端 POST `5` 也行。
2. **speaker 引用双轨**：`cards.speaker`（TEXT 快照）保留 + `cards.speaker_id`（FK 可空）。删音色库某项时 `ON DELETE SET NULL` + 保留字符串快照，老 card 仍可读。
3. **`RefineTextParams` 补采样参数**：0.2.5 支持 `top_P/top_K/temperature`，建议 `_build_refine_text_params` 把 `params.temperature/top_p/top_k` 一并传入，让 refine 阶段采样与 infer_code 一致。
4. **两步范式返回类型区分**：`refine_text_only=True` 返回 `list[str]`，`skip_refine_text=True` 返回 `list[np.ndarray]`。把两步拆成两个独立函数 `_refine_text` / `_synthesize_audio` 各自 mock 干净；进度回调 0.0→0.3 / 0.3→1.0。
5. **schema 迁移并发**：单 worker 启动先不处理多 worker 竞争；本期在 lifespan 启动时单线程跑 `migrate()`。
6. **抽卡仍同步阻塞**：synthesize 走 `startPolling` 2s 间隔。EventSource 实时进度列入 Phase 7 后续。
7. **删除音色库后引用 card 行为**：`speaker_id=null` + `speaker` 字符串有值 → 列表 UI 显示"音色：未命名" + 灰显；用户可重新从音色库绑定新 speaker 修复。
8. **`refiner_text` 自由文本 vs 3 整数互斥**：若 `refiner_text` 非空 → 优先用自由文本；否则 3 整数拼 refine prompt；三者都=0 → 不生成 refine prompt。
9. **`resemble-enhance` 依赖冲突**：PyPI 仅 0.0.1，硬 pin `torch==2.1.1`/`torchaudio==2.1.1` 并依赖 `deepspeed`（macOS 安装常出问题）。落地第一步在隔离 venv 验证 `import resemble_enhance` + `ChatTTS>=0.2.5` 共存；必要时 `pip install resemble-enhance --no-deps` 再手动核对 torch/torchaudio。
10. **增强改采样率 24k→44.1k**：`_numpy_to_wav_bytes` 必须支持可变 `sample_rate`。`subtitle.py` 段时间戳按秒计算不受影响，但需核对没有按采样点数硬编码 24000 的地方（`app/core/subtitle.py`）。
11. **增强耗时重**：单条 synthesize 任务会显著变长，进度回调留足 0.6→1.0 区间；draw 试听强制跳过增强以保证抽卡不被拖慢。
12. **增强模型权重下载**：`resemble-enhance` 首次运行从 HuggingFace 拉权重（`load_enhancer` 走 `@cache` 单例），需联网；离线环境需预下载并指定 `run_dir`。

## 关键参考文件（搬代码用）

| 来源（ChatTTS-Enhanced-main） | 去向（gen-audio） |
|---|---|
| `processors/audio_processor.py:37-55`（spk 优先级 + params 拼装） | `app/core/chat_tts.py` 的 `_build_infer_code_params` / `_build_refine_text_params` |
| `processors/audio_processor.py:114-142`（两步 infer 范式） | `app/core/chat_tts.py` 的 `_infer_audio` 拆分 |
| `utils/text_utils.py:236-252`（`replace_tokens` / `restore_tokens`） | `app/core/text_utils.py` |
| `processors/config_processor.py`（音色 .pt 存读） | `app/storage/speakers.py` |
| `webui/seed_option.py:39-90`（Audio Seed Textbox + 随机按钮） | `static/js/components/speaker-picker.js` |
| `webui/aduio_option.py:16-48`（7 滑条） | `static/js/components/param-panel.js` |
| `processors/enhance_processors.py:24-29`（denoise→enhance + lambd 逻辑） | `app/core/enhance.py` 的 `run_enhance` |
| `webui/enhance_option.py:16-41`（增强/降噪 + solver/nfe/tau 控件） | `param-panel.js` 增强分区 + `TtsParams` 增强字段 |
| `modules/enhance/enhancer/inference.py:27-41`（`denoise`/`enhance` 签名） | 改用 pip `resemble_enhance.enhancer.inference` 同名 API |

## 完整验证清单

### 自动化测试

- `pytest tests/ -v` 全过
- 新增 `tests/test_text_utils.py`：`replace_tokens("[uv_break]hello[laugh]")` 正确替换
- 新增 `tests/test_api_speakers.py`：CRUD + 上传 + 搜索 + 收藏 + 级联删除
- 新增 `tests/test_migrations.py`：v0→v1→v2 幂等
- 新增 `tests/test_e2e_draw.py`：建 speaker → 抽卡 → 合成 → 进度轮询
- 新增 `tests/test_enhance.py`：mock `resemble_enhance` 验证 denoise/enhance 分支与 `lambd` 取值；draw 不触发增强
- 老测试零修改通过：9 个 `test_api_draw` + 9 个 `test_chat_tts` + synthesize 套件

### 端到端手动验证（Phase 6 之后做一次完整流程）

1. `git checkout -b feature/chatts-enhanced-port` 拉分支
2. `pytest -v` 全过
3. `bash run.sh` 启动
4. 打开 `/draw`：
   - 改 Speed=3 / Oral=2 / Laugh=1 / Break=0
   - 点"🎲 随机音色" → 弹 speaker
   - 点"💾 保存到音色库" → 命名 `男声A` → 出现在"加载音色库"下拉
   - 点"🔊 生成" → 听音频
   - 改 Top_P=0.5 → 点"再生成" → 听差异
5. 打开 `/favorites` → 看到刚才生成的卡 → 收藏 → 跳 `/synthesize?card_id=...`
6. 在 `/synthesize`：
   - 选音色库里的 `男声A` → 改 Oral=5
   - 加 3 行任务文本 → 全部提交
   - 进度条走动 → done → 播放
7. 打开 `/favorites` → 删 `男声A` 音色 → 看引用它的 card 仍可读（speaker_id=null）

## 后续（Phase 7+，本期不做）

- EventSource 实时进度推送（替代 `startPolling`）
- 长文本切分（`split_text` + `num2text` + 中英归一化）
- 批量处理 + 音频拼接（`concatente_processor.py`）+ 批量 SRT
- draw 试听也走增强（可选开关）
- 抽卡异步化（`/api/draw` → job_id + 进度）
- 多 worker schema migration 并发安全
