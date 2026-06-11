# gen-audio 设计文档

> 2026-06-11 · 基于 chat-tts 类库的本地音频生成项目

---

## 1. 项目概述

**目标**：本地单用户的 TTS「抽卡 + 批量合成」工具。

**核心能力**：
- **抽卡**：随机生成完整 ChatTTS 参数包 + 自动合成一段 demo 试听
- **批量合成**：用选中的参数卡 + 多段文本 → 音频 + 字幕 + 参数快照
- **收藏管理**：收藏喜欢的参数卡，方便复用
- **参数导入**：从 JSON 导入参数卡

**非目标**（V1 不做）：多用户、远程部署、声音库、字幕编辑、合成历史浏览页（任务列表本身即为历史）。

**用户偏好**（来自用户级规则）：
- 中文优先
- 关键方法必须有中文注释
- 不写"吞错"的兜底逻辑（异常显式抛，不静默吞）
- 优先确认，不擅自决定

---

## 2. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | FastAPI + Uvicorn | 用户指定；OpenAPI 文档自动生成 |
| TTS | 2noise/ChatTTS | 用户确认 |
| 数据校验 | Pydantic v2 | FastAPI 原生 |
| 数据库 | SQLite + 内置 `sqlite3` | 单用户本机，零依赖；不用 SQLAlchemy |
| 字幕 | 服务端手写 SRT 生成器 | 简单可控 |
| 前端 | 原生 HTML + JS（`fetch`） | 零打包 |
| 静态托管 | FastAPI 挂 `StaticFiles` | 单进程 |
| 启动 | `uvicorn app.main:app --reload` | 单命令 |

---

## 3. 目录结构

```
gen-audio/
├── app/
│   ├── main.py                # FastAPI 入口；挂路由 + 静态文件
│   ├── api/
│   │   ├── draw.py            # POST /api/draw       抽卡
│   │   ├── synthesize.py      # POST /api/synthesize 单条 / 批量合成
│   │   ├── jobs.py            # /api/jobs CRUD、取消、文件流
│   │   ├── cards.py           # /api/cards CRUD、收藏切换、试听文件流
│   │   └── card_import.py      # POST /api/cards/import（注意：不用 import.py，Python 关键字）
│   ├── core/
│   │   ├── chat_tts.py        # ChatTTS 模型加载（单例）、draw_one、synthesize_with_progress
│   │   ├── params.py          # Pydantic: TtsParams、DrawnCard、Job 等
│   │   ├── subtitle.py        # 文本→SRT
│   │   ├── exceptions.py      # AppError 体系
│   │   └── queue.py           # 合成队列（worker 协程、显存探测、并发控制）
│   ├── db/
│   │   ├── database.py        # sqlite3 连接管理、schema 迁移
│   │   └── queries.py         # 所有 SQL 集中这里
│   └── storage/
│       └── files.py           # 音频/字幕/参数快照 存盘与读盘
├── static/                    # 前端
│   ├── index.html             # 首页（导航到三个页面）
│   ├── draw.html              # 抽卡
│   ├── favorites.html         # 收藏（=列表 tab=收藏）
│   ├── synthesize.html        # 批量合成
│   ├── css/main.css
│   └── js/
│       ├── api.js             # fetch 封装、统一错误处理
│       ├── draw.js
│       ├── favorites.js
│       └── synthesize.js
├── data/                      # 运行时数据（.gitignore）
│   ├── gen-audio.db
│   └── audio/
│       └── {card_id}/
│           ├── demo.wav
│           ├── demo.srt
│           ├── params.json
│           └── jobs/
│               └── {job_id}/
│                   ├── audio.wav
│                   ├── subtitle.srt
│                   └── params.json
├── tests/
│   ├── conftest.py             # tmp_db / client / mock_chat_tts
│   ├── test_params.py
│   ├── test_subtitle.py
│   ├── test_queries.py
│   ├── test_chat_tts.py
│   ├── test_api_draw.py
│   ├── test_api_synthesize.py
│   ├── test_api_jobs.py
│   ├── test_api_cards.py
│   ├── test_api_import.py
│   └── test_api_errors.py
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-06-11-gen-audio-design.md
├── requirements.txt
├── pyproject.toml
├── run.bat
├── .gitignore
└── README.md
```

**边界划分**：
- 路由层（`api/`）只做参数接收 + 调用业务层 + 返回响应
- 业务层（`core/`）封装 ChatTTS / 队列行为，不依赖 HTTP
- 数据层（`db/`）所有 SQL 集中一处，路由层不直接拼 SQL
- 文件（音频 / 字幕 / 快照）走文件系统，DB 只存路径

---

## 4. 数据模型

### 4.1 表 `cards`（参数卡）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `name` | TEXT NULL | 用户可改名，方便管理；新建时为 `NULL` |
| `params` | TEXT (JSON) | 完整 `TtsParams` 序列化 |
| `demo_text` | TEXT NOT NULL | 抽卡时用的固定 demo 文本；导入时若为空，后端填 `DEFAULT_DEMO_TEXT` |
| `demo_audio_path` | TEXT | 相对 `data/audio/{id}/demo.wav` |
| `demo_subtitle_path` | TEXT | `data/audio/{id}/demo.srt` |
| `is_favorited` | INTEGER (0/1) | 收藏标记 |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

文件目录：`data/audio/{id}/`

### 4.2 表 `synthesis_jobs`（合成任务）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | UUID |
| `card_id` | INTEGER NOT NULL | 外键 → `cards.id`，`ON DELETE CASCADE` |
| `params` | TEXT NOT NULL | 合成时实际使用的 `TtsParams`（JSON）——保存参数快照；不随 `cards.params` 后续变化 |
| `text` | TEXT NOT NULL | 待合成文本 |
| `status` | TEXT NOT NULL | `pending` / `running` / `done` / `failed` / `canceled` |
| `progress` | REAL NOT NULL | 0.0–1.0（ChatTTS 内部 token 进度） |
| `error` | TEXT NULL | 失败时填入 |
| `result_audio_path` | TEXT NULL | `data/audio/{card_id}/jobs/{job_id}/audio.wav` |
| `result_subtitle_path` | TEXT NULL | 同上 `subtitle.srt` |
| `result_params_path` | TEXT NULL | 同上 `params.json` |
| `duration_sec` | REAL NULL | 合成耗时 |
| `created_at` | TIMESTAMP NOT NULL | |
| `started_at` | TIMESTAMP NULL | 进入 `running` 时填 |
| `finished_at` | TIMESTAMP NULL | 进入 `done`/`failed`/`canceled` 时填 |

索引：`idx_jobs_status(status)`，`idx_jobs_card_id(card_id)`

---

## 5. Pydantic 模型

```python
# === 参数 ===

class TtsParams(BaseModel):
    """完整可合成参数包（抽卡 / 合成共用）。"""
    seed: int
    temperature: float = 0.3
    top_p: float = 0.7
    top_k: int = 20
    speaker: str                       # ChatTTS speaker embedding（base64）
    refiner_text: str | None = None    # 风格 prompt

# === 抽卡 ===

class DrawRequest(BaseModel):
    """抽卡请求：用户可选指定 refiner_text；其他参数后端随机生成。"""
    refiner_text: str | None = None

class DrawnCard(BaseModel):
    """抽卡响应。"""
    card_id: int
    params: TtsParams
    demo_text: str
    demo_audio_url: str                # /api/cards/{id}/audio
    demo_subtitle_url: str             # /api/cards/{id}/subtitle

# === 合成 ===

class SynthesizeRequest(BaseModel):
    """单条合成请求。"""
    card_id: int
    params: TtsParams            # 实际使用的参数（保存快照；不依赖 card.params 后续是否被改）
    text: str

class BatchSynthesizeRequest(BaseModel):
    """批量合成请求。"""
    items: list[SynthesizeRequest]

# === 任务 ===

class JobStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    CANCELED = "canceled"

class Job(BaseModel):
    """合成任务。"""
    id: str                       # UUID
    card_id: int
    params: TtsParams             # 合成时实际使用的参数（保存快照；不随 cards.params 后续变化）
    text: str
    status: JobStatus
    progress: float
    error: str | None
    duration_sec: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

# === 卡片列表 / 更新 ===

class CardListItem(BaseModel):
    id: int
    name: str | None
    is_favorited: bool
    demo_text: str
    params: TtsParams
    created_at: str
    updated_at: str

class CardUpdate(BaseModel):
    name: str | None = None
    is_favorited: bool | None = None

# === 导入 ===

class ImportCardItem(BaseModel):
    name: str | None = None
    params: TtsParams
    demo_text: str | None = None    # 导入时可空；后端填 DEFAULT_DEMO_TEXT
    is_favorited: bool = False

class ImportRequest(BaseModel):
    cards: list[ImportCardItem]

# === 健康检查 ===

class HealthResponse(BaseModel):
    status: str                   # "ok" | "loading" | "error"
    model_loaded: bool
    queue_size: int               # 当前内存中 pending+running 数
```

---

## 6. API 路由

| 方法 | 路径 | 用途 | 响应 |
|---|---|---|---|
| GET | `/api/health` | 健康检查 + 模型 + 队列状态 | `HealthResponse` |
| POST | `/api/draw` | 抽卡 | `DrawnCard` |
| GET | `/api/cards?favorited=true` | 列出参数卡（可过滤收藏） | `list[CardListItem]` |
| GET | `/api/cards/{id}` | 单卡详情 | `CardListItem` |
| PATCH | `/api/cards/{id}` | 改名 / 切收藏 | `CardListItem` |
| DELETE | `/api/cards/{id}` | 删卡（连同音频文件） | 204 |
| GET | `/api/cards/{id}/audio` | 流式返回 `demo.wav` | `audio/wav` |
| GET | `/api/cards/{id}/subtitle` | 返回 `demo.srt` 文本 | `text/plain` |
| POST | `/api/cards/import` | 批量导入 | `{imported: int}` |
| POST | `/api/synthesize` | 单条合成 | `Job` |
| POST | `/api/synthesize/batch` | 批量合成 | `list[Job]` |
| GET | `/api/jobs/{id}` | 查任务 | `Job` |
| GET | `/api/jobs?status=running,pending&limit=50` | 列任务（`status=` 不传 = 全部；多状态用英文逗号分隔） | `list[Job]` |
| DELETE | `/api/jobs/{id}` | 取消任务（**仅** `pending` 状态；其他状态返回 409 `JOB_NOT_CANCELLABLE`；**不**删除 DB 记录） | 204 |
| GET | `/api/jobs/{id}/audio` | 取任务结果音频（`done` 才 200） | `audio/wav` |
| GET | `/api/jobs/{id}/subtitle` | 取任务结果字幕 | `text/plain` |
| GET | `/api/jobs/{id}/params.json` | 取参数快照 | `application/json` |
| GET | `/` | 首页 | `index.html` |
| GET | `/draw` | 抽卡页 | `draw.html` |
| GET | `/favorites` | 收藏页 | `favorites.html` |
| GET | `/synthesize` | 合成页 | `synthesize.html` |

> 任务结果（`/api/jobs/{id}/...`）和 demo 试听（`/api/cards/{id}/...`）均以流式返回音频；前端用 `<audio controls>` 直接消费 URL。

---

## 7. 错误处理（**不吞错原则**）

### 7.1 业务异常体系

```python
# app/core/exceptions.py
class AppError(Exception):
    """业务异常基类。所有 AppError 子类都应被 FastAPI handler 捕获并转为结构化响应。"""
    code: str = "INTERNAL_ERROR"
    status: int = 500
    def __init__(self, detail: str, code: str | None = None, status: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if code is not None: self.code = code
        if status is not None: self.status = status

class InvalidParamsError(AppError):       # 400
    code = "INVALID_PARAMS"
    status = 400

class CardNotFoundError(AppError):        # 404
    code = "CARD_NOT_FOUND"
    status = 404

class JobNotFoundError(AppError):         # 404
    code = "JOB_NOT_FOUND"
    status = 404

class TtsError(AppError):                 # 500
    code = "TTS_FAILED"
    status = 500

class ModelNotReadyError(AppError):       # 503
    code = "MODEL_NOT_LOADED"
    status = 503

class ImportFormatError(AppError):        # 400
    code = "IMPORT_INVALID_FORMAT"
    status = 400

class AudioFileNotFoundError(AppError):   # 404
    code = "AUDIO_FILE_NOT_FOUND"
    status = 404

class JobNotCancellableError(AppError):   # 409
    code = "JOB_NOT_CANCELLABLE"
    status = 409

class JobNotReadyError(AppError):         # 409
    code = "JOB_NOT_READY"
    status = 409
```

### 7.2 统一响应

```json
{ "detail": "人类可读消息", "code": "MACHINE_CODE" }
```

注册 FastAPI `exception_handler(AppError)`：捕获 `AppError` → 返回 `JSONResponse(status_code=e.status, content={"detail": e.detail, "code": e.code})`。

### 7.3 业务代码规则

- **严禁 `try/except Exception: pass` 或 `except: return None`**——任何吞错都被禁止。
- 业务代码遇到错误情况**显式抛** `AppError` 子类，附上 `detail` 描述。
- 唯一允许的"兜底"是边界层（如：模型加载失败时把异常**原样抛**出去并记日志，让用户看到）。

---

## 8. 队列设计

### 8.1 关键参数

| 参数 | 默认值 | 覆盖方式 |
|---|---|---|
| `MAX_CONCURRENT_SYNTHESIS` | `2` | 环境变量 |

### 8.2 启动时显存探测

```
if torch.cuda.is_available():
    free_mem_gb = torch.cuda.mem_get_info()[0] / 1024**3
    if free_mem_gb < 4:
        actual_concurrent = 1
    else:
        actual_concurrent = int(os.getenv("MAX_CONCURRENT_SYNTHESIS", "2"))
else:
    actual_concurrent = 1   # CPU 模式强制 1
```

启动日志打印最终 `actual_concurrent`。

### 8.3 Worker 协程

```python
# app/core/queue.py
import asyncio
import torch
from .chat_tts import synthesize_with_progress
from .params import JobStatus
from ..db.queries import update_job_status, update_job_progress

async def worker_loop(job_queue: asyncio.Queue, in_memory: dict):
    """单个 worker 协程。启动 N 个 = N 路并发。

    Note: 提交阶段（POST /api/synthesize）已把 {id, card_id, params, text, status=pending} 写入 DB。
    worker 阶段仅更新 status=running → done/failed，不重写 params（params 是提交时的快照）。
    """
    while True:
        job = await job_queue.get()
        try:
            update_job_status(job.id, JobStatus.RUNNING, started_at=now())
            def on_progress(p: float):
                update_job_progress(job.id, p)
            audio_path, srt_path, params_path = await synthesize_with_progress(
                card_id=job.card_id, text=job.text, params=job.params,
                on_progress=on_progress,
            )
            update_job_status(job.id, JobStatus.DONE,
                              result_audio_path=audio_path,
                              result_subtitle_path=srt_path,
                              result_params_path=params_path,
                              finished_at=now())
        except Exception as e:
            update_job_status(job.id, JobStatus.FAILED,
                              error=str(e), finished_at=now())
            log.exception("synthesis job failed", job_id=job.id)
        finally:
            in_memory.pop(job.id, None)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            job_queue.task_done()
```

启动时 `asyncio.gather(*[worker_loop(q, in_mem) for _ in range(N)])`。

### 8.4 取消

- `pending`：从 `asyncio.Queue` 移除 + DB 标记 `canceled` + 从 `in_memory` dict 移除
- `running`：**不可**取消；如要取消请等当前完成
- `done` / `failed` / `canceled`：不可取消

### 8.5 持久化

| 状态 | 存储位置 | 重启后 |
|---|---|---|
| `pending` | 内存 `asyncio.Queue` + `in_memory` dict | 丢失 |
| `running` | 内存 `in_memory` dict（DB 也保留） | 丢失；DB 残留 `running` 视为脏数据，下次启动清理 |
| `done` / `failed` / `canceled` | DB `synthesis_jobs` | 保留 |

启动时清理 DB 中所有 `status='running'` 的行（视为崩溃残留）。

---

## 9. 前端

### 9.1 导航

固定顶部导航条：`抽卡` | `列表` | `合成`

### 9.2 抽卡页 `draw.html`

- 顶部：页面标题"抽卡"
- 中心：大按钮 `🎲 抽一张`（抽过后变 `再抽一张`）
- 抽到后下方显示「参数卡面板」：
  - 顶部：参数名输入框（默认 `参数 #123`）+ `⭐ 收藏` 按钮
  - 中间：`<audio controls>` + 字幕显示
  - 底部：参数摘要一行（`seed 12345 · temp 0.30 · top_p 0.70 · top_k 20`）
  - 行动按钮：`🎤 去合成`（跳合成页带 card_id）/ `🗑 丢弃`（删这张卡）

### 9.3 列表页 `cards.html`

- 顶部：标题"参数库" + tab `[全部 | 收藏]` + 右上角 `📥 导入` 按钮
- 列表：每行一张卡
  - 左：缩略（`#id name` + ⭐ 标识）
  - 中：`demo_text` 前 30 字预览
  - 右：操作 `[▶ 试听] [⭐ 收藏切] [🎤 用此卡合成] [🗑 删]`

### 9.4 合成页 `synthesize.html`（多行动态列表）

- **左栏 30%**：参数卡选择
  - 搜索框
  - 列表（可滚动），点选高亮
- **右栏 70%**：
  - 上：**任务行列表**（动态增删）
    - 每行结构：
      ```
      ┌─ 文本 textarea ───────────────────────────┐
      │ 输入要合成的文本...                       │
      └──────────────────────────────────────────┘
      [状态徽章: 排队中(3) | 合成中 50% | 完成 | 失败: <err> | 已取消]
      [████░░░░░░] 50%   ← running 才显示进度条
      [操作: ▶试听(done) | ⬇音频 ⬇字幕 ⬇参数 | ❌取消(pending) | 🔄重试(failed) | 🗑移除]
      ```
    - 顶部工具栏：`[+ 添加一行]` + `🚀 全部提交`（按当前行数创建 job）
  - 下：**全局队列状态**（一行小字）
    - `队列：2 个运行中 / 5 个排队中 / 23 个已完成`
    - 每 2 秒轮询 `GET /api/jobs?status=running,pending`

**v1 简化**：所有行**共享**左栏选中的同一张卡（不每行独立选卡）。
**API 设计**：批量接口（`POST /api/synthesize/batch`）设计上**支持**每项独立 `card_id`（v2 用），v1 前端调用时所有 `items[*].card_id` 填同一值。

**按钮语义**：
- `▶ 试听` / `⬇ 音频/字幕/参数`：仅当 `status=done` 可见可点
- `❌ 取消`：仅当 `status=pending` 可见可点；点击 = `DELETE /api/jobs/{id}` 取消任务（DB 记录保留为 `canceled`）；`running` 状态**不**提供取消入口
- `🔄 重试`：仅当 `status=failed` 可见可点；点击 = `POST /api/synthesize`，**用 `job.params`** 创建一个**新** job（旧 job 保留为 `failed`，**不**复用 id）
- `🙈 隐藏`：仅前端 UI 行为，从任务列表里移除显示；**不调后端 API，不删 DB 记录**——和"取消"是不同概念

**数据加载**：
- **首次打开**：调 `GET /api/jobs?limit=100`（`status=` 不传 = 全部），拉最近 100 条作为本会话基线
- **轮询**：每 2 秒拉一次 `GET /api/jobs?status=running,pending&limit=50`（仅活跃任务）
- **前端列表**：内存合并"首次加载" + "轮询" + "新提交"；`done` 的任务**保留在 UI** 不消失
- 离开合成页（路由切换）时停止轮询

### 9.5 导入 modal

- 触发：列表页 `📥 导入` 按钮
- 弹层：拖拽 / 选 JSON 文件 → 解析预览（前 3 条）→ 确认导入
- 导入后：弹层显示"成功导入 N 张"，关闭弹层，列表刷新

### 9.6 JSON 导入格式

```json
{
  "cards": [
    {
      "name": "温柔的男声 #1",
      "params": {
        "seed": 12345,
        "temperature": 0.3,
        "top_p": 0.7,
        "top_k": 20,
        "speaker": "<base64>",
        "refiner_text": null
      },
      "demo_text": "你好，这是一段声音测试。",
      "is_favorited": false
    }
  ]
}
```

### 9.7 错误处理

`static/js/api.js` 统一 `fetch` 封装：

```js
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`/api${path}`, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '请求失败', code: 'UNKNOWN' }));
    toast(err.detail, 'error');
    throw Object.assign(new Error(err.detail), { code: err.code, status: res.status });
  }
  return res.json();
}
```

- 4xx → toast 后端 `detail`
- 5xx → toast "服务器开小差了"
- 网络错误 → toast "网络错误，请检查后端是否启动"

### 9.8 交互细节

- 试听播放器：原生 `<audio controls>`，无第三方库
- 收藏状态：`⭐` 实心 = 已收藏，`☆` 空心 = 未收藏
- 删除二次确认：modal 弹"确认删除？此操作不可撤销"
- 长时间操作（抽卡 / 合成）：按钮 spinner + 禁止重复点击

---

## 10. 测试策略

### 10.1 分层

| 层级 | 范围 | 工具 |
|---|---|---|
| 单元 | Pydantic 模型 / SRT 生成 / 业务工具 | `pytest` |
| 数据层 | SQLite 查询 | `pytest` + 临时 sqlite 文件 |
| 业务层（mock TTS） | 抽卡 / 合成 / 导入 | `pytest` + `monkeypatch` |
| API 集成 | 所有路由 happy path + 错误 path | FastAPI `TestClient` |
| 端到端 | 浏览器流程 | 手动（不写自动化） |

### 10.2 测试文件

```
tests/
├── conftest.py             # tmp_db / client / mock_chat_tts
├── test_params.py          # TtsParams / DrawRequest / SynthesizeRequest 校验
├── test_subtitle.py        # SRT 时间戳生成
├── test_queries.py         # cards / jobs 表的 SQL
├── test_chat_tts.py        # mock 后测试 draw_one / synthesize_with_progress
├── test_api_draw.py        # POST /api/draw
├── test_api_synthesize.py  # POST /api/synthesize / /api/synthesize/batch
├── test_api_jobs.py        # GET/PATCH/DELETE /api/jobs
├── test_api_cards.py       # GET/PATCH/DELETE /api/cards
├── test_api_import.py      # POST /api/cards/import
└── test_api_errors.py      # 错误码映射
```

### 10.3 关键 Fixture

```python
# tests/conftest.py 概要

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """临时 SQLite 文件。"""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    init_schema(db_path)
    yield db_path
    db_path.unlink(missing_ok=True)

@pytest.fixture
def client(tmp_db):
    """FastAPI TestClient。"""
    from app.main import app
    return TestClient(app)

@pytest.fixture
def mock_chat_tts(monkeypatch):
    """替换 ChatTTS 真实调用，避免加载模型。"""
    monkeypatch.setattr("app.core.chat_tts.draw_one",
                        lambda **kw: TtsParams(seed=42, ...))
    monkeypatch.setattr("app.core.chat_tts.synthesize_with_progress",
                        fake_synthesize)
```

### 10.4 运行

```bash
pytest tests/ -v
```

不接 CI（单用户本地项目，手工跑即可）。`README.md` 写明"提交前必跑测试"。

---

## 11. 部署 + 启动

### 11.1 启动

```bash
# 1. 装依赖（首次）
pip install -r requirements.txt

# 2. 启后端（开发模式）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器开 `http://127.0.0.1:8000` —— 直接进抽卡页。

### 11.2 首次启动

- ChatTTS 自动下载模型到 `~/.cache/chat-tts/`（约几百 MB）
- 加载 5-15 秒
- 期间 `GET /api/health` 返回 `503 MODEL_NOT_LOADED`
- 前端按钮 disabled + "模型加载中…"

### 11.3 数据目录

`data/`（gitignore），含 `gen-audio.db` 和 `audio/{card_id}/`

### 11.4 `requirements.txt`

```
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
ChatTTS>=0.1
torch>=2.0
numpy
```

### 11.5 `run.bat`（Windows 便利脚本，可选）

```bat
@echo off
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 11.6 `.gitignore`

```
data/
__pycache__/
*.pyc
.superpowers/
.pytest_cache/
*.egg-info/
.venv/
venv/
```

---

## 12. V1 范围

### In Scope（V1 必做）

- ✅ 抽卡（返回完整参数包 + 自动合 demo 试听）
- ✅ 试听 demo（音频 + 字幕）
- ✅ 收藏 / 取消收藏
- ✅ 列表页（tab 全部 / 收藏）+ 改名 + 删除
- ✅ 批量合成（动态多行列表 + 共享左栏卡）
- ✅ Job 队列（默认 2 worker + 显存探测 + 自动降级）
- ✅ Job 状态轮询 / 取消 / 重试 / 下载
- ✅ 参数导入（JSON）
- ✅ 错误处理（统一响应 + 前端 toast + 不吞错）
- ✅ 单元 + API 集成测试
- ✅ `README.md`（安装 / 启动 / 使用）

### Out of Scope（V1 不做）

- ❌ 多用户 / 鉴权 / 登录（纯本机单用户）
- ❌ 声音库（保存 / 复用 speaker embedding）
- ❌ 字幕编辑 / 调整
- ❌ 部署到远程服务器
- ❌ GPU 配置 UI（启动自动探测 + 自动降级）
- ❌ 端到端自动化测试（手动测）
- ❌ 每行独立选卡（v2）—— 批量 API 已支持，前端 v2 接上
- ❌ 按 card 维度的合成历史浏览（v2）—— 任务列表全局看，不按 card 过滤

---

## 13. 后续演进（V2+ 候选）

按可能性排序：

1. 每行独立选卡（合成页多卡矩阵）
2. 声音库（保存 / 复用 speaker embedding）
3. 批量上传文本文件（`.txt` / `.csv`）
4. 字幕编辑 / 调整
5. 合成历史浏览页（按 card 维度看历史 job 列表）
6. 部署到远程服务器 + 鉴权
7. GPU 加速配置 UI
8. 端到端自动化测试（playwright）

---

## 14. 关键设计决策汇总

| 决策 | 选择 | 理由 |
|---|---|---|
| TTS 库 | 2noise/ChatTTS | 用户指定 |
| 收藏对象 | 参数卡（含 demo 试听） | 用户指定 |
| 部署形态 | 本机 Web（FastAPI + 浏览器 SPA） | 用户指定 |
| 用户体系 | 单用户无鉴权 | 用户指定 |
| 前端技术栈 | 原生 HTML + JS | 单用户本地，零打包即用 |
| 抽卡粒度 | 完整可合成参数包 | 用户指定 |
| 任务队列 | `asyncio.Queue` + worker | 保护 GPU 显存 |
| 队列并发 | 默认 2，启动显存探测 | 4GB 显存安全 |
| 任务持久化 | 只持久化 done/failed/canceled | 简单可靠；重启丢未完成 |
| 字幕生成 | 服务端手写 SRT | ChatTTS 内部可输出 token 时间戳 |
| 错误处理 | AppError 体系 + 统一 handler | 不吞错原则 |
