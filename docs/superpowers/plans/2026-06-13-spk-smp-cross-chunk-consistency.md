# Phase 8：首段参考音频（spk_smp 二段法）—— 强化跨段音色一致性

> **状态**：Phase 7（长文本切分流水线）已合入 master（commit `a649437`）；本计划是 Phase 7 之后的增量计划，在已落地的基础上追加。
>
> **前置阅读**：[2026-06-13-long-text-chunking.md](./2026-06-13-long-text-chunking.md)（Phase 7 主计划）。
>
> **不重复 Phase 7 已确定的内容**（速读：normalize → chunk → 单段重试 → concat 主干 / 单 job 串行 / 强制 `skip_refine_text=True` / `split_text=False` / `seed=0` 提前固定 / 重试改 `seed = base_seed + attempt` / 段间 0.12s 静音 / 控制 token PUA 保护 / 单段失败 = 整 job FAILED）。

## Context

Phase 7 落地后跑长文本，"音色一致性"仍有可感漂移。原决策表里写过 "**先不实现** ChatTTS 的 `spk_smp` 二段法（B 方案）"，现在按用户反馈打开。

**根因**：即使 `spk_emb`（即 `params.speaker`）+ `manual_seed` 两段全程不变，ChatTTS 0.2.5 的 GPT 解码仍是采样过程，不同段语义会让 timbre / prosody 微变 —— 多段拼起来听感漂移。

**ChatTTS 的解决路径**（`InferCodeParams` 字段）：

| 字段 | 来源 API | 语义 |
|---|---|---|
| `spk_emb` | `Chat.sample_random_speaker() -> str` | 音色嵌入（描述"谁在说"） |
| `spk_smp` | `Chat.sample_audio_speaker(wav) -> str` | 参考音频样本（描述"听起来像这段"） |
| `txt_smp` | 直接传字符串 | 与 `spk_smp` 配对的原文 |

`sample_audio_speaker` 内部 = `dvae.sample_audio(wav)` → `speaker.encode_prompt(...)` → `b14.encode_to_string(lzma.compress(uint16 bytes))`，**返回 base14 字符串**（与 `spk_emb` 同种文本格式，可直接放进 `InferCodeParams.spk_smp`）。

**目标**：第 1 段正常合成 → 编码为参考样本 → 第 2..N 段 infer 时把它注入 `spk_smp` + 把第 1 段文本注入 `txt_smp`，让后续段"模仿第 1 段"。**只在多段任务启用**，单段任务零开销。

## 已确认的设计决策

| 决策 | 内容 |
|---|---|
| 启用条件 | `len(chunks) > 1` 且 `TEXT_CHUNK_USE_FIRST_AS_REF=true` 且**用户没传 `params.spk_smp`**（用户优先） |
| 参考音频提取时机 | 第 1 段 `_synthesize_one_chunk` 成功返回后 → `_MODEL.sample_audio_speaker(wav_0)` |
| **第 1 段太短回退** | 若 `len(chunks[0]) < TEXT_CHUNK_REF_MIN_CHARS`（默认 `8`）→ 跳到 chunk[1]（若也 < min 则 chunk[2]，依此类推），**用第一个达标段**做参考。该段 wav 已经合成在 audios 列表里，不重新 infer。若所有段都短于阈值（极罕见，多见于碎句堆叠），不启用 ref，全程裸 spk_emb 兜底（同 Phase 7 行为） |
| 参考样本文本配对 | `txt_smp` 必须是**喂给 `_MODEL.infer` 的那段文本**（即 normalize + chunk 之后的 chunk_text，不是用户原始输入），保证 audio/text 严格对齐 |
| 参考段塌缩防御 | `_synthesize_one_chunk` 内部已有重试 + 塌缩检测，return 的 wav 一定 active ≥ `TEXT_CHUNK_COLLAPSE_RATIO`，可放心当参考。**无需**额外二次塌缩检测 |
| 编码失败兜底 | `sample_audio_speaker(wav)` 抛异常（如 dvae OOM）→ 记 WARNING + 不启用 ref，后续段全裸跑（不影响整 job 成功） |
| 用户已传 `spk_smp` | 完全跳过本机制，全程沿用 `params.spk_smp`（声音克隆场景：用户最知道想模仿啥） |
| 配置开关 | `TEXT_CHUNK_USE_FIRST_AS_REF: bool = True`（默认开），`TEXT_CHUNK_REF_MIN_CHARS: int = 8`（最短参考段字数） |
| 进度回调 | 不变。提取参考样本 < 100ms，不单独算阶段 |

## 文件改动清单

### 修改文件

- **`app/config.py`** — +2 配置：`TEXT_CHUNK_USE_FIRST_AS_REF`、`TEXT_CHUNK_REF_MIN_CHARS`
- **`app/core/chat_tts.py`** — `synthesize_to_wav_bytes` 在主循环后追加 ref 切换逻辑；`_synthesize_one_chunk` 不动（它只看 `params.spk_smp` / `txt_smp`，由调用方控制）
- **`tests/test_chat_tts_chunked.py`** — 新增覆盖：
  - 多段任务第 1 段满足长度 → chunk[1..n] 收到的 `infer_kwargs["spk_smp"]` 等于 mock `sample_audio_speaker(wav_0)`
  - 第 1 段太短 → 用第一个达标段做参考；该段之后的所有段才注入 spk_smp
  - 单段任务 → 不调 `sample_audio_speaker`
  - 用户传了 `params.spk_smp` → 不调 `sample_audio_speaker`，全段使用用户传的值
  - `TEXT_CHUNK_USE_FIRST_AS_REF=False` → 不调 `sample_audio_speaker`
  - `sample_audio_speaker` 抛异常 → WARNING + 不启用 ref，整 job 仍成功
  - `txt_smp` 配对的是 normalize + chunk 后的文本（断言对齐）
- **`CLAUDE.md`** — 在「长文本切分流水线」章节末尾追加一句 spk_smp 二段法说明
- **`.env.example`** — 加两行配置注释

### 不改

- `params.py`（`TtsParams.spk_smp` / `txt_smp` 字段已存在）
- `_build_infer_code_params`（已正确把 `params.spk_smp` / `txt_smp` 传给 `InferCodeParams`）
- `queue.py` / `synthesize.py` / DB schema

## 关键代码设计

```python
# app/core/chat_tts.py — synthesize_to_wav_bytes 主循环改动（在 Step 4 内部）

# Step 4-pre: 决策是否启用首段参考机制
use_ref = (
    len(chunks) > 1
    and config.TEXT_CHUNK_USE_FIRST_AS_REF
    and not (params.spk_smp or "").strip()  # 用户已传 spk_smp 则让位
)
ref_smp: str | None = None     # 编码后的参考样本，注入 spk_smp
ref_txt: str | None = None     # 配对原文，注入 txt_smp
ref_pending = use_ref          # 还没找到合格的参考段

# Step 4: 串行合成 + 在第一个长度达标段提取参考
audios: list[np.ndarray] = []
n = len(chunks)
for i, chunk in enumerate(chunks):
    # 注入：从 ref_pending=False 那一刻起，后面所有段都带 spk_smp
    chunk_params = params if ref_smp is None else params.model_copy(
        update={"spk_smp": ref_smp, "txt_smp": ref_txt}
    )
    wav = _synthesize_one_chunk(
        params=chunk_params,
        chunk_text=chunk,
        max_retries=config.TEXT_CHUNK_MAX_RETRIES,
        collapse_ratio=config.TEXT_CHUNK_COLLAPSE_RATIO,
        chunk_idx=i, total_chunks=n,
    )
    audios.append(wav)

    # 提取参考样本：碰到第一个长度达标的段就编码
    if ref_pending and len(chunk) >= config.TEXT_CHUNK_REF_MIN_CHARS:
        try:
            ref_smp = _MODEL.sample_audio_speaker(wav)
            ref_txt = chunk
            ref_pending = False
            logger.info(
                "[chat_tts] ref-smp from chunk %d/%d (len=%d): %d chars encoded",
                i + 1, n, len(chunk), len(ref_smp),
            )
        except Exception as e:
            logger.warning(
                "[chat_tts] sample_audio_speaker failed on chunk %d: %s. "
                "Falling back to bare spk_emb for remaining chunks.",
                i + 1, e,
            )
            ref_pending = False  # 放弃，但整 job 继续

    if on_progress:
        on_progress((i + 1) / n * 0.6)
```

**注意点**：

1. **`txt_smp` 用 chunk 后的文本**：因为 `_synthesize_one_chunk` 喂给 `_MODEL.infer` 的就是这段文本，audio/text 必须严格对齐。Phase 7 的 normalize 已在切分前完成 → `chunks[i]` 已是 normalize 后文本。
2. **第 1 段就是参考段（最常见路径）**：`chunks[0]` 长度达标时，`audios[0]` 用裸 `spk_emb` 合成，`audios[1..n-1]` 注入 ref。symmetric 漂移最小化（不是最理想的"全段都对齐第 1 段"，但避免重合 chunk[0]）。
3. **第 1 段不达标 + 第 2 段达标**：`audios[0..1]` 都裸跑，`audios[2..]` 注入 ref（参考来自 chunk[1]）。
4. **重试时的 spk_smp 一致性**：`_synthesize_one_chunk` 内部重试改的是 seed，`spk_smp` 来自外层 params 不变。重试不影响参考机制。

## 验证方案

### 单元测试（`tests/test_chat_tts_chunked.py` 增量）

```python
def test_first_chunk_used_as_ref(monkeypatch, fake_model_long):
    """5 段任务，每段都达标 → chunk[1..4] 收到 spk_smp = mock 输出"""
    captured = []
    fake_model_long.sample_audio_speaker = lambda wav: f"REF<{wav.sum():.2f}>"
    monkeypatch.setattr(chat_tts, "_MODEL", fake_model_long)
    # ... mock _MODEL.infer 记录 InferCodeParams.spk_smp / txt_smp
    chat_tts.synthesize_to_wav_bytes(params, "段1。段2。段3。段4。段5。", on_progress=None)
    assert captured[0]["spk_smp"] is None
    assert captured[1]["spk_smp"] == "REF<...>"
    assert captured[1]["txt_smp"] == "段1"
    # ... 2..4 同


def test_short_first_chunk_uses_second(...):
    """chunk[0]='好'（< 8 字）→ 用 chunk[1] 当 ref；chunk[0..1] 裸跑，chunk[2..] 带 ref"""


def test_user_spk_smp_takes_precedence(...):
    """params.spk_smp 已传 → 全程用用户的，sample_audio_speaker 一次都不调"""


def test_disable_ref_via_config(monkeypatch):
    """TEXT_CHUNK_USE_FIRST_AS_REF=False → 全段裸跑"""


def test_sample_audio_speaker_failure_fallback(...):
    """sample_audio_speaker 抛异常 → WARNING + 后续段裸跑 + 整 job 成功"""


def test_single_chunk_no_ref(...):
    """单段任务 → sample_audio_speaker 不调用"""


def test_txt_smp_uses_chunked_text_not_raw(...):
    """喂 '我出生于1998年。' → normalize + chunk 后是 '我出生于一九九八年' →
       txt_smp 必须是 '我出生于一九九八年' 而不是 '我出生于1998年'"""
```

### 端到端验证

1. **听感对比**（同一长文本同一 seed）：
   - `TEXT_CHUNK_USE_FIRST_AS_REF=False` 跑一次 → 拷贝 wav A
   - `TEXT_CHUNK_USE_FIRST_AS_REF=True` 跑一次 → 拷贝 wav B
   - 主观对比：B 在中后段的音色 / 性别 / 音高应该比 A 更稳
2. **第 1 段短回退**：构造 `"好。今天天气真的很好阳光也很温暖。..."`（首段 1 字）→ log 应出现 "ref-smp from chunk 2/N"
3. **配置回退**：`TEXT_CHUNK_USE_FIRST_AS_REF=False` → 行为完全等同 Phase 7（用作回归基线）
4. **回归**：`pytest tests/ -v` 仍 312 passed + 新增 ~7 用例

## 风险与边界

### 1. 每段开销 +10–15%

`spk_smp` 编码会让 InferCodeParams 多带一段 prompt prefix → GPT 步数变多。代价确定；用户可关 `TEXT_CHUNK_USE_FIRST_AS_REF=false` 快速回退。

### 2. 极短段堆叠场景

文本全是碎句（每段 < `TEXT_CHUNK_REF_MIN_CHARS`），所有段都不当参考 → 退化到 Phase 7 全裸行为。日志记 INFO 提示用户。

### 3. `sample_audio_speaker` 在 mac CPU 慢路径

实测 24k Hz × 10s wav 编码 < 200ms，可接受。Linux GPU 更快。

### 4. 与 enhance / denoise 的关系

ref 提取在 Step 4 chunk 循环内（已 enhance 之前），喂给 ChatTTS 的是干净 wav；enhance 在 Step 6 拼接后跑。ref 不受 enhance 影响。

### 5. `txt_smp` 可能含 PUA 占位符

text_chunker 的 `restore_tokens` 已经在 chunk 出来前还原了控制 token，`chunks[i]` 是干净文本。无问题。

## 实现顺序建议

1. `config.py` 加 2 个配置项 + 测试
2. `chat_tts.py` 改 `synthesize_to_wav_bytes` Step 4（约 20 行）
3. `tests/test_chat_tts_chunked.py` 加 7 个用例
4. `CLAUDE.md` + `.env.example` 文档
5. 听感端到端 + 回归

每步独立 commit。
