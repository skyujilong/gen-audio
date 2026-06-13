# Phase 8 执行计划：首段参考音频（spk_smp 二段法）

> **关系**：本文件是 [`2026-06-13-spk-smp-cross-chunk-consistency.md`](./2026-06-13-spk-smp-cross-chunk-consistency.md)（spec/设计）的**执行计划**。spec 描述"做什么 / 为什么"；本文描述"怎么落地、按什么顺序、每步 commit 什么"。
>
> **前置**：Phase 7（长文本切分流水线）已合入 master（`a649437` + `da3e108`）。

## Context

Phase 7 落地后跑长文本，跨段音色仍有可感漂移：即使 `spk_emb` + `manual_seed` 全程不变，ChatTTS 0.2.5 的 GPT 解码是采样过程，不同段语义会让 timbre / prosody 微变。

**Phase 8 目标**：第 1 段正常合成 → 用 `_MODEL.sample_audio_speaker(wav)` 编码为参考样本 → 第 2..N 段 infer 时把它注入 `spk_smp` + 把第 1 段文本注入 `txt_smp`，让后续段"模仿第 1 段"。**只在多段任务启用，单段任务零开销**。

设计已在 spec 文档定稿（启用条件、短段回退、用户优先、编码失败兜底等），本文只负责落地执行。

## 工作树

按项目惯例（Phase 7 也是这样跑的），用 native `EnterWorktree` 起一个隔离工作树 `spk-smp-ref`（已起好，分支 `worktree-spk-smp-ref`）。失败 fallback 到主目录。每步独立 commit。

## 执行步骤

按 spec 文档「实现顺序建议」分 5 个落地步骤 + 验证 + 收尾，每步 1 个独立 commit。

### Step 0：先把 Phase 8 的计划文档本身提交

进入工作树时已带入：

- `docs/superpowers/plans/2026-06-13-long-text-chunking.md`（modified；末尾追加了"后续增量"链接）
- `docs/superpowers/plans/2026-06-13-spk-smp-cross-chunk-consistency.md`（untracked；新建的 Phase 8 spec）
- `docs/superpowers/plans/2026-06-13-spk-smp-execution.md`（本文件）

先入仓再开始改代码。

```bash
git add docs/superpowers/plans/2026-06-13-long-text-chunking.md \
        docs/superpowers/plans/2026-06-13-spk-smp-cross-chunk-consistency.md \
        docs/superpowers/plans/2026-06-13-spk-smp-execution.md
git commit -m "docs: Phase 8 计划（spk_smp 二段法跨段音色一致性）独立成文 + 执行计划

- 从 Phase 7 长文本切分计划末尾抽出 spk_smp 二段法增量到独立 spec 文件
- 原文件改为只含 Phase 7，末尾加'后续增量'链接到 Phase 8
- 新增 spk-smp-execution.md 描述 6 步落地路线"
```

### Step 1：`app/config.py` 加 2 个配置项

在 Phase 7 配置块（`TEXT_CHUNK_*`）末尾追加：

```python
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
```

> 配置不需要专门的单测；Step 3 的 chat_tts 测试会通过 `monkeypatch.setattr(config, ...)` 间接覆盖。

```bash
git add app/config.py
git commit -m "feat(config): Phase 8 spk_smp 二段法 2 个配置项"
```

### Step 2：`app/core/chat_tts.py` 改 `synthesize_to_wav_bytes` Step 4 循环

**改动位置**：`chat_tts.py:575-590`（"=== Step 4：串行合成每段，带重试 + 塌缩检测 ==="）。

在循环开始前插入决策块；循环内根据 `ref_smp` 是否就绪决定本段用裸 `params` 还是注入 ref 的 copy；循环内每段后追加"提取参考样本"分支。

```python
# Step 4-pre: 决策是否启用首段参考机制
use_ref = (
    len(chunks) > 1
    and config.TEXT_CHUNK_USE_FIRST_AS_REF
    and not (params.spk_smp or "").strip()
)
ref_smp: str | None = None
ref_txt: str | None = None
ref_pending = use_ref

# Step 4: 串行合成（沿用现有重试 + 塌缩检测）
audios: list[np.ndarray] = []
n = len(chunks)
for i, chunk_text in enumerate(chunks):
    chunk_params = params if ref_smp is None else params.model_copy(
        update={"spk_smp": ref_smp, "txt_smp": ref_txt}
    )
    wav = _synthesize_one_chunk(
        params=chunk_params,
        chunk_text=chunk_text,
        max_retries=config.TEXT_CHUNK_MAX_RETRIES,
        collapse_ratio=config.TEXT_CHUNK_COLLAPSE_RATIO,
        chunk_idx=i, total_chunks=n,
    )
    audios.append(wav)

    # 在第一个长度达标段之后提取参考样本（编码失败 → WARNING + 后续段裸跑）
    if ref_pending and len(chunk_text) >= config.TEXT_CHUNK_REF_MIN_CHARS:
        try:
            ref_smp = _MODEL.sample_audio_speaker(wav)
            ref_txt = chunk_text
            ref_pending = False
            logger.info("[chat_tts] ref-smp from chunk %d/%d ...", i + 1, n)
        except Exception as e:
            logger.warning("[chat_tts] sample_audio_speaker failed ...: %s", e)
            ref_pending = False

    if on_progress:
        on_progress((i + 1) / n * 0.6)
```

**关键点**：

- `_build_infer_code_params` 已经读 `params.spk_smp` / `params.txt_smp`（`chat_tts.py:180-183`），无需改。
- `_synthesize_one_chunk` 不动 —— 它内部重试改 seed，外层 `chunk_params` 含的 spk_smp/txt_smp 是稳定的。
- 现有 `_FakeModel`（测试用）目前没有 `sample_audio_speaker`；Step 3 给它补上。

```bash
git add app/core/chat_tts.py
git commit -m "feat(chat_tts): Phase 8 首段参考音频（spk_smp 二段法）"
```

### Step 3：`tests/test_chat_tts_chunked.py` 加 7 个用例 + 扩 `_FakeModel`

先扩 `_FakeModel`：

```python
class _FakeModel:
    def __init__(self):
        self.has_loaded_value = True
        self.infer_calls: list[dict] = []
        self._random_spk_returns = "FAKE_SAMPLED_SPEAKER"
        # Phase 8: spk_smp 二段法相关
        self.sample_audio_calls: list[np.ndarray] = []
        self.sample_audio_returns = "REF_SMP_FAKE"   # 可被赋值或 callable
        self.sample_audio_raises: Exception | None = None

    def sample_audio_speaker(self, wav):
        self.sample_audio_calls.append(wav)
        if self.sample_audio_raises is not None:
            raise self.sample_audio_raises
        if callable(self.sample_audio_returns):
            return self.sample_audio_returns(wav)
        return self.sample_audio_returns
```

然后加 7 个测试。**关键**：既有测试 mock `_synthesize_audio`（绕过 `_build_infer_code_params`）；Phase 8 验证 `params.spk_smp` 注入时同样在 `_synthesize_audio` 入参 `params` 上断言即可，不需要触达真实 `InferCodeParams`：

```python
# === 12. Phase 8: spk_smp 二段法 ===

def test_first_chunk_used_as_ref(fake_model, monkeypatch):
    """首段达标 → 后续段 params.spk_smp = sample_audio_speaker(wav_0)，
    txt_smp = chunks[0]。第 1 段裸跑。"""
    ...

def test_short_first_chunk_uses_first_qualifying(fake_model, monkeypatch):
    """chunks[0] < min_chars → 用第一个达标段；之前都裸跑，之后才注入。"""
    ...

def test_user_spk_smp_takes_precedence(fake_model, monkeypatch):
    """params.spk_smp 已传 → sample_audio_speaker 0 次调用。"""
    ...

def test_disable_ref_via_config(fake_model, monkeypatch):
    """TEXT_CHUNK_USE_FIRST_AS_REF=False → 全段裸跑，sample_audio_speaker 0 次。"""
    ...

def test_sample_audio_speaker_failure_fallback(fake_model, monkeypatch, caplog):
    """sample_audio_speaker 抛异常 → WARNING + 后续段裸跑 + 整 job 仍成功。"""
    ...

def test_single_chunk_no_ref(fake_model, monkeypatch):
    """单段任务 → sample_audio_speaker 0 次调用。"""
    ...

def test_txt_smp_uses_chunked_text_not_raw(fake_model, monkeypatch):
    """txt_smp 必须是切分后（含 normalize 后）的 chunk[0] 文本，不是用户原文。"""
    ...
```

跑测试：

```bash
source .venv/bin/activate
pytest tests/test_chat_tts_chunked.py -v       # 新 7 个 + 旧 17 个全过
```

```bash
git add tests/test_chat_tts_chunked.py
git commit -m "test(chat_tts): Phase 8 spk_smp 二段法 7 用例 + 扩 _FakeModel"
```

### Step 4：`.env.example` + `CLAUDE.md` 文档

**`.env.example`** 在「长文本切分」配置块末尾追加：

```bash
# === 首段参考音频（Phase 8：跨段音色一致性，spk_smp 二段法）===
# 多段任务时，把第一个长度达标段合成出的 wav 作为参考音频喂给后续段，
# 让后续段"模仿第 1 段"，跨段音色更稳。会让每段 +10–15% 推理开销；
# 用户已传 params.spk_smp 时自动让位。
TEXT_CHUNK_USE_FIRST_AS_REF=true

# 最短参考段字数。短于此值的段不当参考（防 1-2 字短句信息量不足）。
TEXT_CHUNK_REF_MIN_CHARS=8
```

**`CLAUDE.md`** 在「长文本切分流水线」段（搜 "Phase 7"）末尾追加一段：

> Phase 8（spk_smp 二段法）：多段任务时把第一个达标段（≥ `TEXT_CHUNK_REF_MIN_CHARS` 字）的 wav 编码成 `spk_smp` 注入后续段，强化跨段音色一致性。开关 `TEXT_CHUNK_USE_FIRST_AS_REF` 默认 true；用户已传 `params.spk_smp` 时自动让位；编码失败 / 单段任务零开销。详见 `docs/superpowers/plans/2026-06-13-spk-smp-cross-chunk-consistency.md`。

```bash
git add .env.example CLAUDE.md
git commit -m "docs: Phase 8 spk_smp 二段法（.env.example + CLAUDE.md）"
```

### Step 5：端到端验证

参考 spec 文档「验证方案 § 端到端验证」：

1. **回归**：`pytest tests/ -v` 应在原 240+ 基础上 +7 = 全过
2. **听感对比**（手动，可选）：同一长文本同一 seed
   - `TEXT_CHUNK_USE_FIRST_AS_REF=false ./run.sh` → wav A
   - `TEXT_CHUNK_USE_FIRST_AS_REF=true ./run.sh` → wav B
   - 主观听 B 中后段音色是否更稳
3. **配置回退**：`TEXT_CHUNK_USE_FIRST_AS_REF=false` 应等同 Phase 7 行为
4. **短首段 log**：`"好。今天天气真的非常好阳光也很温暖。..."` → 启动 dev 服跑，日志应出现 `ref-smp from chunk 2/N`

### Step 6：finishing-a-development-branch

按 `superpowers:finishing-a-development-branch` skill 收尾：

- 跑 `pytest tests/ -v` 确认全过
- 探测环境（worktree vs 主仓）
- 询问用户：1 合并 / 2 PR / 3 保留 / 4 丢弃

## 关键文件汇总

| 文件 | 改动类型 | 内容 |
|---|---|---|
| `app/config.py` | 修改 | +2 配置项（Step 1） |
| `app/core/chat_tts.py` | 修改 | `synthesize_to_wav_bytes` Step 4 循环（Step 2，~25 行） |
| `tests/test_chat_tts_chunked.py` | 修改 | `_FakeModel` 加 `sample_audio_speaker`，加 7 测试（Step 3） |
| `.env.example` | 修改 | +2 行配置注释（Step 4） |
| `CLAUDE.md` | 修改 | 「长文本切分流水线」末尾追加一段（Step 4） |
| `docs/.../*-long-text-chunking.md` | 修改（已就绪） | Step 0 提交 |
| `docs/.../*-spk-smp-cross-chunk-consistency.md` | 新增（已就绪） | Step 0 提交 |
| `docs/.../*-spk-smp-execution.md` | 新增（本文件） | Step 0 提交 |

## 不改

- `app/core/params.py` —— `TtsParams.spk_smp` / `txt_smp` 字段已存在
- `app/core/chat_tts.py:_build_infer_code_params` —— 已正确透传 spk_smp / txt_smp
- `app/core/chat_tts.py:_synthesize_one_chunk` —— 重试 seed 切换不影响外层 spk_smp
- `app/core/queue.py` / `app/api/synthesize.py` / DB schema / 前端

## 风险点

1. **每段 +10–15% 开销**：`spk_smp` 让 InferCodeParams 多带 prompt prefix。关 `TEXT_CHUNK_USE_FIRST_AS_REF=false` 可立即回退。
2. **极短段堆叠**：所有段都 < 8 字 → 全程裸跑（同 Phase 7）。INFO log 提示。
3. **Mac CPU 慢路径**：`sample_audio_speaker` 编码 24kHz × 10s wav ~200ms，可接受。
4. **既有测试稳定性**：`_FakeModel` 加 `sample_audio_speaker` 默认返 string 不抛错；旧测试不受影响（旧测试要么不进入 chunk pipeline，要么 `_synthesize_audio` 被全 mock 走不到 sample_audio 分支）。
