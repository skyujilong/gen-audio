# 长文本切分 + 文本规范化（TN）+ 单段重试 + 拼接落地

## Context

**触发问题**：合成长文本时 ChatTTS 内部抛 `ValueError: need at least one array to concatenate`（`.venv/.../ChatTTS/core.py:269` 处 `np.concatenate(stripped_wavs)`）。

**根因**：ChatTTS 0.2.5 的 `infer(split_text=True)` 默认按 `\n` / `(?<=。)|(?<=\.\s)` 切片，每 `max_split_batch=4` 段并行 GPT decode，再 concat。任何一段 GPT 塌成 `|x|<1e-5` 全静音 → `stripped_wavs` 为空 → concat 报错；整个 task 失败，前面已生成的段全丢。

**ChatTTS 切分逻辑的盲区**（不动其代码情况下需我们自己绕开）：
- 切分边界单一（仅 `\n` `。` `. `），`，！？；：` 都不切
- 无长度上限兜底，单段过长强进 GPT 必塌
- `\n` 优先级覆盖 `。`，碰到带换行的长段不会再细切
- 任意一段塌缩 → 整 job 失败，无重试粒度
- 数字 / 日期 / 单位（如 `1998` `3.14` `30°C`）依赖 ChatTTS 内置 normalizer，效果不稳

**目标**：在应用层做"文本规范化 → 智能切分 → 单段独立合成（带重试 + 塌缩检测）→ 静音拼接"的完整流水线，封装在 **同一个 job** 里走现有队列。可调参全部通过 `.env.local` 暴露。

---

## 整体流水线

**任务粒度模型**（关键概念，避免误解）：

```
1 次用户提交（一段大文本）
        ↓
   1 条 job 入队列（synthesis_jobs 表 + asyncio.Queue 各 1 条）  ← 队列层面不变
        ↓
   worker 从队列取出这条 job 后，在 worker 内部：
        ↓
   ① text_norm.normalize_text(大文本)       规整化整段文本（1998→一九九八 等）
        ↓
   ② text_chunker.split_text(大文本)        切成 chunks 数组：[c1, c2, c3, ..., c8]
        ↓
   ③ for chunk in chunks:                    ← 串行循环这个数组
         一次完整的 ChatTTS infer(chunk)     ← 数组里每一项 = 一次完整音频转化
         独立重试 / 独立塌缩检测             重试粒度 = 单个 chunk
         得到一段独立的 wav
        ↓
   ④ audio_concat 拼接 8 段 wav              段间插 0.12s 静音
        ↓
   ⑤ 写 1 个 wav + 1 个 srt                  这条 job 的最终产出
```

**关键概念**：

- **队列层「job」粒度不变**：仍然是 1 个用户提交 = 1 条 `synthesis_jobs` 记录 = 队列里 1 个 task。`submit_job` / `_worker_loop` 签名都不动。
- **chunks 数组只活在 worker 内部**：DB 不存、队列不知道、API 也看不到。
- **「数组里每一项 = 一次完整的音频转化任务」**：每个 chunk 走一次完整的 `_MODEL.infer(...)` 调用，拿一段独立 wav；这是重试 / 塌缩检测 / seed 切换的最小粒度。
- **不拆成"每个 chunk 一个子 job"**：worker 间调度复杂、共享 GPU 显存抢占、跨 job 拼接落盘协调困难。**单 job 内部串行**够用了。

**其他强约束**：

- 必须给 `_MODEL.infer` 显式传 `split_text=False`，否则两层切分会让字幕时间戳错乱、停顿插入失效。
- TN 必须在切分**之前**：FST 需要完整数字/日期上下文；切完再 normalize 会切碎语义单元。
- TN + 切分都要复用 `app/core/text_utils.py:replace_tokens` 保护 `[oral_X]` `[laugh_X]` `[break_X]` `[speed_X]` `[uv_break]` 控制 token。
- **禁用 refine**：ChatTTS 的 refine 阶段即使在切段后仍有较大概率导致音色漂移，本流水线**强制 `skip_refine_text=True`**，忽略用户传入的 `refiner_text`（不再走 `_refine_text`）。
- **单段失败 = 整 job 失败**：单段重试 N 次仍失败（塌缩或异常）→ **抛异常中止整个 job**，不做静音兜底。错误信息写明"第几段 / 哪句话 / 什么错误"返回调用方，由 worker 标 FAILED。

---

## 文件改动清单

### 新增文件

- **`app/core/text_norm.py`** — WeTextProcessing 封装（全局单例 + lifespan 预热 + 失败 fallback）
- **`app/core/text_chunker.py`** — 标点分级 + 字数上限切分 + 空段过滤
- **`app/core/audio_concat.py`** — 多 wav 数组拼接 + 段间静音 + 时间戳记录
- **`tests/test_text_norm.py`** — TN 单元测试
- **`tests/test_text_chunker.py`** — 切分单元测试
- **`tests/test_audio_concat.py`** — 拼接 + 时间戳单元测试

### 修改文件

- **`app/config.py`** — 新增 8 个 `os.getenv` 配置项
- **`app/core/chat_tts.py`** — 重构 `synthesize_to_wav_bytes`：从"单次 infer"改为"normalize → chunk → 循环 infer + 重试 + 塌缩检测 → concat"。`_synthesize_audio` / `_infer_audio` 加 `split_text=False` 参数透传。
- **`app/main.py`** — `lifespan` 里和 ChatTTS 模型并行预热 TN normalizer（用 `asyncio.gather`）
- **`requirements.txt`** — 加 `WeTextProcessing` + macOS pynini 安装注释
- **`.env.example`** — 新增配置示例（如有此文件）
- **`.gitignore`** — 加 `data/wetext_cache/`
- **`CLAUDE.md`** — 新增"长文本切分流水线"章节描述

### 不改

- `app/api/synthesize.py`（API 层 zero-change，全在 worker 里处理）
- `app/core/queue.py`（worker 仍调 `synthesize_to_wav_bytes` 签名不变；但 SRT 构建需调整以支持多段字幕）
- `app/core/subtitle.py:build_srt`（接受 `[(text, start, end)]`，逻辑不变；调用方传新算的 segments）
- `app/storage/files.py`（写盘逻辑不变）
- DB schema（无字段变化）

---

## 关键模块设计

### 1. `app/core/text_norm.py`

```python
from tn.chinese.normalizer import Normalizer as ZhNormalizer
from .text_utils import replace_tokens, restore_tokens

_normalizer: ZhNormalizer | None = None

def load_normalizer() -> None:
    """lifespan 启动时调一次（编译 FST，可能 5-30s）。"""
def is_loaded() -> bool: ...
def normalize_text(text: str) -> str:
    """1. replace_tokens 保护 control token
       2. 跑 ZhNormalizer 做 1998→一九九八/3.14→三点一四 等
       3. restore_tokens 还原
       异常 / 未加载 → 返回原文（fallback）"""
```

**配置**：
- `TEXT_NORM_ENABLED`（默认 `true`，可关）
- `TEXT_NORM_CACHE_DIR`（默认 `<DATA_ROOT>/wetext_cache`，FST 编译缓存）
- `TEXT_NORM_REMOVE_ERHUA`（默认 `false`）

**失败策略**：永远 fallback 到原文，不抛——TN 是优化项不是必需项。

### 2. `app/core/text_chunker.py`

```python
def split_text(
    text: str,
    *,
    soft_max: int = 20,         # 目标段长：按强/中/弱标点切，中文 15-20 字最佳
    hard_max: int = 35,         # 硬上限：用尽标点后仍超长则硬切兜底（一般不用）
    min_chars: int = 4,         # 短于此值的段尝试与邻段合并（避免短段塌缩）
) -> list[str]:
```

**切分算法**（保护控制 token，控制 token 不计入字数）：

1. `replace_tokens(text)` → 用 PUA 占位（U+E000+），它们字数算 0
2. **控制 token 边界保护**：切分时遇到 `\[\w+_\d+\]` 或 `\[uv_break\]`，**优先在其后切分**，避免把 `[oral_3]` 等截断到两段
3. **强切**：按 `[。！？…\.\!\?]` + `\n` 切（这些必切）
4. 对每段长度 > `soft_max` 的：**中切**按 `[；：;:]` 再切
5. 还 > `soft_max` 的：**弱切**按 `[，、,]` 再切
6. **硬切兜底**：用尽所有标点后还 > `hard_max`（无标点长串），按字数硬砍
7. **空段 / 纯标点过滤**：跳过 `re.search(r'[一-鿿a-zA-Z0-9]', segment)` 为 None 的段
8. **短段合并**：< `min_chars` 的段尝试和后一段合并；合并后超 `soft_max` 则不合并
9. 每段 `restore_tokens` 还原

**关键理解**：
- `soft_max`（默认 20）是**日常目标值**：中文 15-20 字是 ChatTTS 最稳定的甜点区，绝大多数段落在此范围内
- `hard_max`（默认 35）是**兜底上限**：只有遇到超长无标点串时才会触发硬切，正常文本不会用到

**测试用例必覆盖**：
- 纯中文 / 纯英文 / 混排
- 多种标点：`今天天气好。明天呢？后天！还有，等等；最后：完。`
- 控制 token 嵌入：`[oral_3]今天1998年[uv_break]。`
- 无标点长串："今天天气真的很好阳光也很温暖空气特别清新让人心情愉悦"（25字）
- 纯标点段：`。。。、、，` → 空列表
- emoji / 数字 / 字母：`OK 123 😀。` → 保留（含字母数字算有内容）
- 控制 token 单独成段：`[oral_3][laugh_2]` → 跳过（无字母/数字/中文）
- 短段合并：`好。今天。`（每段 1 字）应合并

### 3. `app/core/audio_concat.py`

```python
def concat_with_pauses(
    audios: list[np.ndarray],
    *,
    sample_rate: int = 24000,
    pause_sec: float = 0.12,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """把多段 wav 拼成一段，段间插静音。

    Returns:
        (merged_audio, segment_times)
        segment_times[i] = (chunk_i 在最终 wav 里的 start, end)
        —— 静音区间不在 segment_times 里，方便 SRT 直接用 chunk 索引对齐
    """
```

**实现**：
- 24000 Hz × 0.12s = 2880 个 `np.float32(0.0)` 样本
- **最后一段后不加**静音
- segments 按 `accumulated_offset` 累加
- 空 audios → 返回 `(np.zeros(sample_rate), [])`（1s 占位）

### 4. `app/core/chat_tts.py` 重构 `synthesize_to_wav_bytes`

```python
def synthesize_to_wav_bytes(params, text, on_progress=None):
    # Step 0: 禁用 refine（音色漂移），强制忽略 refiner_text
    params = params.model_copy(update={"skip_refine_text": True, "refiner_text": None})

    # Step 1: 文本规范化
    normalized = text_norm.normalize_text(text)

    # Step 2: 切分
    chunks = text_chunker.split_text(
        normalized,
        soft_max=config.TEXT_CHUNK_SOFT_MAX,
        hard_max=config.TEXT_CHUNK_HARD_MAX,
        min_chars=config.TEXT_CHUNK_MIN_CHARS,
    )
    if not chunks:
        # 全是标点 / 空 → 1s 静音占位（沿用现有 _synthesize_audio 行为）
        return _numpy_to_wav_bytes(np.zeros(24000, dtype=np.float32)), []

    # 段数上限保护：防超长文本独占 worker
    if len(chunks) > config.TEXT_CHUNK_MAX_SEGMENTS:
        raise ValueError(
            f"文本过长，切分后 {len(chunks)} 段超过最大限制 "
            f"{config.TEXT_CHUNK_MAX_SEGMENTS}，请缩短文本或调大限制"
        )

    # Step 3: 固定 speaker + 固定 seed（防多段音色 / 韵律漂移）
    if not (params.speaker or "").strip() and params.speaker_id is None:
        params = params.model_copy(update={"speaker": _MODEL.sample_random_speaker()})
    # seed=0 → ChatTTS 内部每次随机，会让各 chunk 韵律漂移；提前固定一个非 0 seed
    if params.seed == 0:
        params = params.model_copy(update={"seed": _random_int(1, 2**31 - 1)})

    # Step 4: 串行合成每段，带重试 + 塌缩检测；任一段彻底失败 → 抛异常中止整 job
    audios: list[np.ndarray] = []
    sample_rate = 24000
    n = len(chunks)
    for i, chunk in enumerate(chunks):
        wav = _synthesize_one_chunk(
            params=params,
            chunk_text=chunk,
            max_retries=config.TEXT_CHUNK_MAX_RETRIES,
            collapse_ratio=config.TEXT_CHUNK_COLLAPSE_RATIO,
            chunk_idx=i,
            total_chunks=n,
        )
        # _synthesize_one_chunk 全失败时已抛 ChunkSynthesisError，这里 wav 必非 None
        audios.append(wav)
        if on_progress:
            on_progress((i + 1) / n * 0.6)  # 合成占 0→0.6，enhance 留 0.6→1.0

    # Step 5: 拼接
    merged, segment_times = audio_concat.concat_with_pauses(
        audios,
        sample_rate=sample_rate,
        pause_sec=config.TEXT_CHUNK_PAUSE_SEC,
    )

    # Step 6: enhance / denoise（沿用现有逻辑，注意 enhance 后 sr 变 44100）
    if params.enhance_audio or params.denoise_audio:
        merged, sample_rate = run_enhance(
            merged, sr=sample_rate,
            denoise=params.denoise_audio, enhance=params.enhance_audio,
            solver=params.solver, nfe=params.nfe, tau=params.tau,
        )  # segment_times 用「秒」为单位，resample 不改时长，无需重算
    if on_progress:
        on_progress(0.95)

    # Step 7: 字幕段（每个 chunk 一个 SRT entry，时间戳来自 segment_times）
    srt_segments = list(zip(chunks, [t[0] for t in segment_times], [t[1] for t in segment_times]))
    # ↑ 调用方（queue.synthesize_with_progress）拿到 segments 后调 build_srt

    return _numpy_to_wav_bytes(merged, sample_rate=sample_rate), srt_segments
```

**注意签名调整**：现在 `synthesize_to_wav_bytes` 返回的 segments 含 text，原来只含 `(start, end)`。`queue.synthesize_with_progress` 里拼 SRT 的代码相应改成 `build_srt(segments)` 直接传过来——逻辑反而更简洁。

```python
class ChunkSynthesisError(RuntimeError):
    """单段合成彻底失败（重试耗尽）。携带段索引 + 段文本 + 末次错误。"""


def _synthesize_one_chunk(params, chunk_text, max_retries, collapse_ratio,
                          chunk_idx, total_chunks) -> np.ndarray:
    """单段合成 + 重试。成功返回 wav；重试耗尽 → 抛 ChunkSynthesisError（整 job 失败）。

    每次重试改 seed = base_seed + attempt（避开同一塌缩路径）。
    """
    base_seed = params.seed
    last_reason = "unknown"
    for attempt in range(max_retries + 1):
        try:
            attempt_params = params.model_copy(update={"seed": base_seed + attempt})
            audio, _ = _synthesize_audio_single(attempt_params, chunk_text)
            if not _is_collapsed(audio, collapse_ratio):
                return audio
            last_reason = "collapse"
            logger.warning("[chat_tts] chunk collapsed (attempt %d/%d): %r",
                           attempt + 1, max_retries + 1, chunk_text[:40])
        except Exception as e:
            last_reason = f"infer error: {e}"
            logger.warning("[chat_tts] chunk infer failed (attempt %d/%d): %r — %s",
                           attempt + 1, max_retries + 1, chunk_text[:40], e)
    # 重试耗尽 → 中止整 job，错误写明第几段 / 哪句话 / 什么错误
    raise ChunkSynthesisError(
        f"第 {chunk_idx + 1}/{total_chunks} 段合成失败（重试 {max_retries + 1} 次）："
        f"原因={last_reason}，文本={chunk_text[:60]!r}"
    )


def _synthesize_audio_single(params, text):
    """复用 _synthesize_audio 但强制 split_text=False（关键！）。
    可以加一个内部参数控制，或拆成新函数。"""
    # 在 _MODEL.infer 调用处加 split_text=False
    ...


def _is_collapsed(audio: np.ndarray, threshold_ratio: float, eps: float = 1e-5) -> bool:
    """active sample 占比 < threshold_ratio → 视为塌缩。"""
    if len(audio) == 0:
        return True
    active = int(np.sum(np.abs(audio) > eps))
    return active / len(audio) < threshold_ratio
```

**`_synthesize_audio` 改造**：在 `_MODEL.infer(...)` 调用处加 `split_text=False`（chat_tts.py:342–346 附近）。同时**保留** `skip_refine_text=True`（refine 已外层做过）。

### 5. `app/config.py` 新增配置

```python
# === 长文本切分 ===
TEXT_NORM_ENABLED = os.getenv("TEXT_NORM_ENABLED", "true").lower() == "true"
TEXT_NORM_CACHE_DIR = Path(os.getenv("TEXT_NORM_CACHE_DIR", str(DATA_ROOT / "wetext_cache")))
TEXT_NORM_REMOVE_ERHUA = os.getenv("TEXT_NORM_REMOVE_ERHUA", "false").lower() == "true"

TEXT_CHUNK_SOFT_MAX = int(os.getenv("TEXT_CHUNK_SOFT_MAX", "20"))  # 日常目标值，中文 15-20 字效果最佳
TEXT_CHUNK_HARD_MAX = int(os.getenv("TEXT_CHUNK_HARD_MAX", "35"))   # 兜底上限，无标点长串才会触发硬切
TEXT_CHUNK_MIN_CHARS = int(os.getenv("TEXT_CHUNK_MIN_CHARS", "4"))
TEXT_CHUNK_PAUSE_SEC = float(os.getenv("TEXT_CHUNK_PAUSE_SEC", "0.12"))
TEXT_CHUNK_MAX_RETRIES = int(os.getenv("TEXT_CHUNK_MAX_RETRIES", "2"))
TEXT_CHUNK_COLLAPSE_RATIO = float(os.getenv("TEXT_CHUNK_COLLAPSE_RATIO", "0.05"))
TEXT_CHUNK_MAX_SEGMENTS = int(os.getenv("TEXT_CHUNK_MAX_SEGMENTS", "50"))  # 单 job 最大段数限制，防超长文本独占 worker
```

**测试 monkeypatch 注意**：和现有 `DB_PATH` 一样，路由层 / chat_tts 要用 `from .. import config; config.TEXT_CHUNK_SOFT_MAX`，不要 `from ..config import TEXT_CHUNK_SOFT_MAX`，否则测试无法 patch（CLAUDE.md 已记录这条）。

### 6. `app/main.py` lifespan 预热

`load_normalizer()` 编译 FST 可能 5–30s，不能阻塞启动。**和 ChatTTS 模型加载一样后台跑**：

```python
async def _load_models_bg():
    await asyncio.gather(
        asyncio.to_thread(chat_tts.load_model),
        asyncio.to_thread(text_norm.load_normalizer) if config.TEXT_NORM_ENABLED else asyncio.sleep(0),
        return_exceptions=True,  # TN 失败不阻塞 ChatTTS
    )
```

**`/api/health` 暴露**：增加 `tn_status: loading|ok|error|disabled`，前端能感知。

### 7. `requirements.txt` 加包

```text
# WeTextProcessing：文本规范化（数字/日期/单位 → 中文念法）
# 依赖 pynini（依赖 OpenFST C++ 库）：
#   - Linux: 通常 pip 直接装
#   - macOS: 需 brew install openfst（或 conda install -c conda-forge pynini）
#   - Windows: 推荐 conda
# 安装失败时不阻断启动 —— text_norm.py 会 fallback 到原文（仍能合成，只是没数字读法优化）。
WeTextProcessing
```

`text_norm.py` 顶部 `try: from tn.chinese.normalizer import Normalizer as ZhNormalizer; except ImportError: ZhNormalizer = None`，未安装时 `is_loaded()` 永远返回 False，`normalize_text` 直接返回原文。

---

## 已确认的设计决策（不再讨论）

| 决策 | 内容 |
|---|---|
| 单 job | 一段文本 = 一个 job，子段不拆。worker 串行跑所有 chunk。超长文本受 `TEXT_CHUNK_MAX_SEGMENTS` 限制（默认 50 段）。 |
| TN 时机 | 切分**之前**做。FST 需要完整数字/日期上下文。 |
| 控制 token 保护 | 复用 `text_utils.replace_tokens`，TN 和切分都先替换再还原。 |
| ChatTTS 内部切分关闭 | `_MODEL.infer(..., split_text=False)`。 |
| 双层 normalizer | WeTextProcessing（语言学规范化）+ ChatTTS 内置（同音字 + 全半角归并）**互补**，不绕开 ChatTTS 的。 |
| 音色稳定 | task 入口处把 `speaker` 固定（空则提前 sample 一次），全程所有 chunk 共用。**先不实现** ChatTTS 的 `spk_smp` 二段法（B 方案）。 |
| refine | **禁用**。refine 即使切段后仍易音色漂移，强制 `skip_refine_text=True` + 忽略 `refiner_text`。 |
| 重试策略 | 单段 N 次（默认 2），每次 `seed = base_seed + attempt`。重试粒度 = 单 chunk。 |
| 塌缩检测 | `np.abs(wav) > 1e-5` 占比 < `TEXT_CHUNK_COLLAPSE_RATIO`（默认 0.05）即视为塌缩。 |
| 单段失败处理 | **一段失败 = 整 job 失败**。重试耗尽 → 抛 `ChunkSynthesisError`（含第几段 / 哪句话 / 什么错误）→ worker 标 FAILED，错误写 DB error 字段。**不静音兜底**。 |
| seed 一致性 | `seed=0` 时提前固定一个非 0 seed，避免各 chunk 韵律漂移。 |
| 段间停顿 | 0.12s 固定，最后一段后不加。**先不分级**（强切/弱切统一），按用户反馈再调。 |
| SRT 时间戳 | 按拼接后真实累加时间，不按字符数估算。 |

---

## 验证方案

### 单元测试

- `tests/test_text_chunker.py`：
  - 标点分级覆盖（强/中/弱/硬切）
  - 控制 token 不被切到中间，字数计算不算 token
  - 空段 / 纯标点段过滤
  - 短段合并
  - 无标点长串硬切兜底
  - 中英混排

- `tests/test_text_norm.py`（**TN 装好时跑，否则 skip**）：
  - `1998` → `一九九八` / `一千九百九十八`
  - `3.14` → `三点一四`
  - `30°C` → `三十摄氏度`
  - `2024年3月15日` → 中文日期
  - 控制 token 不被破坏（`[oral_3]今天1998` → `[oral_3]今天一九九八`）
  - TN 失败时 fallback 到原文不抛
  - `TEXT_NORM_ENABLED=false` 时 normalize_text 直接 return text

- `tests/test_audio_concat.py`：
  - 段间静音长度 = 24000 × 0.12 = 2880 样本
  - 最后一段后不加静音
  - segment_times 累加正确
  - 空输入返回 1s 静音

- `tests/test_chat_tts_chunked.py`（mock `_MODEL.infer`）：
  - 多段串行调 `_MODEL.infer(split_text=False)`
  - 强制 `skip_refine_text=True`：即使传了 `refiner_text` 也不走 refine
  - 第 N 段失败 → 重试 → 下一次成功 → 整体成功
  - 某段重试耗尽 → 抛 `ChunkSynthesisError`（含段索引 / 段文本 / 原因）→ 整 job 失败
  - seed=0 时被提前固定为非 0
  - 进度回调按 `i/total*0.6` 触发
  - SRT 时间戳和拼接时间对齐

### 端到端验证

1. **复现原 bug 不再触发**：用之前会塌的长文本提交 `/api/synthesize`，job 应 success；wav 时长合理（不是 1s 静音占位）。若某段真的重试耗尽，job 应 FAILED 且 error 字段写明哪句话出错。
2. **TN 生效**：合成 `"我出生于1998年，体重60kg"`，听感上应该读"一九九八年" + "六十千克"，不是"一九九八" + "60kg"。
3. **音色稳定**：长文本（多段）合成后听感上音色无漂移。
4. **塌缩注入测试**：mock `_synthesize_audio` 让某段始终返回全 0 → 应触发重试 → 重试耗尽后抛 `ChunkSynthesisError` → 整 job FAILED，DB error 字段写明第几段 / 哪句话。
5. **配置生效**：`TEXT_CHUNK_SOFT_MAX=10` 后切分变细；`TEXT_CHUNK_PAUSE_SEC=0.5` 后段间停顿明显变长。
6. **WeTextProcessing 未装时**：`pip uninstall WeTextProcessing` 后重启，`/api/health` 显示 `tn_status: disabled`，合成仍可用，只是数字按 ChatTTS 默认行为念。
7. **回归**：`pytest tests/ -v` 全过（240+ 用例 + 新增的 ~30 用例）。

---

## 风险与注意事项

### 1. 进度回调是 chunk 数估算，非真实时间

```python
on_progress((i + 1) / n * 0.6)   # 合成 0→0.6
on_progress(0.95)                # enhance 后
```

进度按 chunk 数平均分配，若某 chunk 重试多次，实际耗时远超估算，进度会显得"卡住"。**不修复**：真实时间需要每段合成完才知道，无法预先估算。

### 2. 单 job 可能长时间独占 worker

长文本切成 50 段 × 每段 8 秒 = 400 秒，此 worker 被独占。`TEXT_CHUNK_MAX_SEGMENTS=50` 是防护线，超长文本应在前端拒绝或拆成多次提交。

### 3. 段数限制后的处理策略

若切完发现 `len(chunks) > TEXT_CHUNK_MAX_SEGMENTS`：
- **方案 A**：报错拒绝（简单明确）
- **方案 B**：只处理前 N 段，后面丢弃（用户不知）
- **选择**：方案 A，抛 `ValueError(f"文本过长，切分后 {len(chunks)} 段超过最大限制 {max_segments}，请缩短文本或调大限制")`

### 4. 控制 token 跨段问题已处理

切分算法第 2 步已加入边界保护，优先在 `[oral_X]` 等 token **之后**切分，避免截断。若整段只剩控制 token（如 `"[oral_3][laugh_2]"`），会被空段过滤跳过。

### 5. 错误记录格式（单段失败 = 整 job 失败）

某段重试耗尽 → `_synthesize_one_chunk` 抛 `ChunkSynthesisError` → 上抛到 `synthesize_to_wav_bytes` → `queue._worker_loop` 的 `except` 分支捕获 → `update_job_status(FAILED, error=str(e))`。

错误信息是可读字符串，写明哪句话出错：
```
第 3/8 段合成失败（重试 3 次）：原因=collapse，文本='今天天气真的很好阳光也很温暖...'
```

**无需改 queue.py 的错误处理**：现有 `except Exception as e: update_job_status(FAILED, error=str(e))` 直接复用（`@app/core/queue.py:163-169`）。也无需第 3 个返回值 / 不改 DB schema。

### 6. queue.py SRT 构建调整

`queue.synthesize_with_progress` 里从：
```python
srt = build_srt([(text, segments[0][0], segments[0][1])]) if segments else ""
```
改为：
```python
srt = build_srt(segments) if segments else ""  # segments 现在是 [(text, start, end), ...]
```

签名不变，但调用方式需配合新返回格式。

---

## 实现顺序建议

1. `text_chunker.py` + 测试（纯函数，最容易 TDD）
2. `audio_concat.py` + 测试（同上）
3. `text_norm.py` + 测试（独立模块，依赖外部库）
4. `config.py` 新增配置项
5. `chat_tts.py` 重构 `synthesize_to_wav_bytes` + `_synthesize_one_chunk` + 改 `_synthesize_audio` 加 `split_text=False`
6. `main.py` lifespan 加 TN 预热 + `/api/health` 加字段
7. `requirements.txt` + `.gitignore` + `CLAUDE.md` 文档
8. 端到端验证 + 回归

每步独立 commit，便于回滚。
