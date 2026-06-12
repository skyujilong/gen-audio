"""Pydantic v2 模型：生成 / 合成 / 任务 / 卡片 / 导入 / 健康检查。

所有 API 的请求和响应都用这些模型。前端通过 OpenAPI 自动生成的 schema 也能看到。
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


# === 参数 ===

class TtsParams(BaseModel):
    """完整可合成参数包。

    Attributes:
        seed: 随机种子，决定 ChatTTS 输出的稳定性。
        temperature: 采样温度，越高越发散。
        top_p: nucleus sampling 阈值。
        top_k: top-k sampling 阈值。
        speaker: ChatTTS speaker embedding（base64 字符串）。
        refiner_text: 风格 prompt（可选），如 [oral_2][laugh_1]。
        repetition_penalty: 重复惩罚，>1 抑制重复。
        speed: 语速控制（整数 0–10），后端拼成 `[speed_X]` 给 ChatTTS。
        skip_refine_text: 跳过文本精炼，可加速推理。
        max_new_token: 最大生成 token 数。
        spk_smp: 参考音频 speaker（声音克隆），base64 字符串。
        txt_smp: 参考音频对应文本。
        oral: 口语化程度（0–9，对应 refine prompt 中的 [oral_X]）。
        laugh: 笑声强度（0–9，对应 refine prompt 中的 [laugh_X]）。
        break_: 停顿强度（0–9，对应 refine prompt 中的 [break_X]）。
    """

    model_config = ConfigDict(extra="forbid")

    seed: int
    temperature: float = 0.3
    top_p: float = 0.7
    top_k: int = 20
    speaker: str
    # Phase 4.2：音色库 FK 引用。提交合成时若此字段非空，
    # synthesize 路由层会从 speakers 库读出 tensor_base64 覆盖上面的 `speaker` 字符串。
    speaker_id: int | None = None
    refiner_text: str | None = None
    repetition_penalty: float = 1.05
    speed: int = 5
    skip_refine_text: bool = False
    max_new_token: int = 2048
    spk_smp: str | None = None
    txt_smp: str | None = None
    # Phase 1.2: 新增 3 个整数字段，refine prompt 用
    oral: int = 0
    laugh: int = 0
    break_: int = 0
    # Phase 2.6.1: 增强 / 降噪字段（仅 synthesize 生效；draw 试听强制跳过）
    enhance_audio: bool = False
    denoise_audio: bool = False
    solver: str = "midpoint"  # midpoint | rk4 | euler
    nfe: int = 64              # 1-128
    tau: float = 0.5           # 0-1

    @field_validator("speed", mode="before")
    @classmethod
    def _coerce_speed(cls, v: Any) -> Any:
        """兼容老数据：老 speed 字段是形如 "[speed_5]" 的字符串，自动提数字。

        新数据传整数；老数据传字符串也能 work。失败原样上抛由 Pydantic 报错。
        """
        if isinstance(v, str):
            m = re.search(r"\[speed_(\d+)\]", v)
            if m:
                return int(m.group(1))
        return v

    @field_validator("solver")
    @classmethod
    def _check_solver(cls, v: str) -> str:
        if v not in ("midpoint", "rk4", "euler"):
            raise ValueError(f"solver 必须是 midpoint / rk4 / euler 之一, got {v!r}")
        return v

    @field_validator("nfe")
    @classmethod
    def _check_nfe(cls, v: int) -> int:
        if not (1 <= v <= 128):
            raise ValueError(f"nfe 必须在 1-128 范围内, got {v}")
        return v

    @field_validator("tau")
    @classmethod
    def _check_tau(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"tau 必须在 0-1 范围内, got {v}")
        return v


# === 常量 ===

DEFAULT_DEMO_TEXT = "你好，这是一段声音测试。"
"""导入卡片时若 `demo_text` 为空，后端填这个默认值。"""


# === 生成 ===

class DrawRequest(BaseModel):
    """生成请求。seed/speaker 缺省时后端随机；其余字段缺省用默认值。

    Phase 4.1: 新增 `speaker_id`（音色库 FK，可选），用于绑定已命名音色到本卡。
    若提供：后端会按 id 读出 speaker 的 `tensor_base64` 喂给推理，同时把
    `speaker_id` 写入 `cards.speaker_id`（**双轨引用**：字符串快照 `params.speaker`
    + FK `speaker_id`），删除音色时 FK 自动 SET NULL 而字符串快照保留。
    """
    seed: int | None = None          # None → 随机
    temperature: float = 0.3
    top_p: float = 0.7
    top_k: int = 20
    speaker: str | None = None       # None → 随机
    speaker_id: int | None = None    # Phase 4.1: 音色库 FK 优先于 speaker
    refiner_text: str | None = None
    demo_text: str = DEFAULT_DEMO_TEXT  # 试听文本
    repetition_penalty: float = 1.05
    speed: int = 5
    skip_refine_text: bool = False
    max_new_token: int = 2048
    spk_smp: str | None = None
    txt_smp: str | None = None
    oral: int = 0
    laugh: int = 0
    break_: int = 0
    # Phase 2.6.1: 同 TtsParams
    enhance_audio: bool = False
    denoise_audio: bool = False
    solver: str = "midpoint"
    nfe: int = 64
    tau: float = 0.5

    @field_validator("speed", mode="before")
    @classmethod
    def _coerce_speed(cls, v: Any) -> Any:
        """同 TtsParams.speed：兼容老字符串。"""
        if isinstance(v, str):
            m = re.search(r"\[speed_(\d+)\]", v)
            if m:
                return int(m.group(1))
        return v

    @field_validator("solver")
    @classmethod
    def _check_solver(cls, v: str) -> str:
        if v not in ("midpoint", "rk4", "euler"):
            raise ValueError(f"solver 必须是 midpoint / rk4 / euler 之一, got {v!r}")
        return v

    @field_validator("nfe")
    @classmethod
    def _check_nfe(cls, v: int) -> int:
        if not (1 <= v <= 128):
            raise ValueError(f"nfe 必须在 1-128 范围内, got {v}")
        return v

    @field_validator("tau")
    @classmethod
    def _check_tau(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"tau 必须在 0-1 范围内, got {v}")
        return v


class DrawnCard(BaseModel):
    """生成响应。"""
    card_id: int
    params: TtsParams
    demo_text: str
    demo_audio_url: str
    demo_subtitle_url: str


# === 合成 ===

class SynthesizeRequest(BaseModel):
    """单条合成请求。

    `params` 必须由前端传入（从选中卡的 params 复制或从已有 job 的 params 复制），
    后端**不**再从 card 重新读取 —— 这样合成任务的参数是凝固的快照。
    """
    card_id: int
    params: TtsParams
    text: str


class BatchSynthesizeRequest(BaseModel):
    """批量合成请求。v1 前端所有项共享同一 card_id + params；v2 可每项独立。"""
    items: list[SynthesizeRequest]


# === 卡片列表 / 更新 ===

class CardListItem(BaseModel):
    """卡片列表 / 详情通用响应。"""
    id: int
    name: str | None
    is_favorited: bool
    demo_text: str
    params: TtsParams
    # Phase 4.3：音色库引用 FK（可空）。删除音色库项时被应用层 SET NULL，
    # 字符串快照（`params.speaker`）仍保留。
    speaker_id: int | None = None
    created_at: str
    updated_at: str


class CardUpdate(BaseModel):
    """卡片更新请求（改名 / 切收藏），字段均可选。"""
    name: str | None = None
    is_favorited: bool | None = None


# === 导入 ===

class ImportCardItem(BaseModel):
    """单张卡的导入项。`demo_text` 可空，导入时后端填 `DEFAULT_DEMO_TEXT`。"""
    name: str | None = None
    params: TtsParams
    demo_text: str | None = None
    is_favorited: bool = False


class ImportRequest(BaseModel):
    """批量导入请求。"""
    cards: list[ImportCardItem]


# === 健康检查 ===

class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str              # "ok" | "loading" | "error"
    model_loaded: bool
    queue_size: int          # 当前内存中 pending + running 数


# === 任务状态机 ===

class JobStatus(str, Enum):
    """合成任务状态。"""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


# === 任务 ===

class Job(BaseModel):
    """合成任务。

    `params` 是**提交时的快照**，不随 `cards.params` 后续变化。
    重试时直接用 `job.params` 即可复现这次合成的设置。
    """
    id: str                       # UUID
    card_id: int
    params: TtsParams
    text: str
    status: JobStatus
    progress: float
    error: str | None = None
    duration_sec: float | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


# === 音色库（Phase 1.3） ===

class SpeakerBase(BaseModel):
    """音色库共享字段。"""
    model_config = ConfigDict(extra="forbid")

    name: str
    tensor_base64: str
    tags: list[str] = []
    is_favorited: bool = False


class SpeakerCreate(SpeakerBase):
    """创建音色请求。`name` + `tensor_base64` 必填。"""
    pass


class SpeakerUpdate(BaseModel):
    """更新音色请求（改名 / 改 tags / 切收藏），字段均可选。"""
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    tags: list[str] | None = None
    is_favorited: bool | None = None


class SpeakerOut(SpeakerBase):
    """音色详情响应（含 id / 时间戳）。"""
    id: int
    created_at: str
    updated_at: str


class SpeakerListItem(BaseModel):
    """音色库列表项（**不**带 tensor，节省带宽）。"""
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    tags: list[str] = []
    is_favorited: bool = False
    created_at: str
    updated_at: str
