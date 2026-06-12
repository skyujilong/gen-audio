"""Pydantic v2 模型：生成 / 合成 / 任务 / 卡片 / 导入 / 健康检查。

所有 API 的请求和响应都用这些模型。前端通过 OpenAPI 自动生成的 schema 也能看到。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


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
        speed: 语速控制 token，默认 [speed_5]。
        skip_refine_text: 跳过文本精炼，可加速推理。
        max_new_token: 最大生成 token 数。
        spk_smp: 参考音频 speaker（声音克隆），base64 字符串。
        txt_smp: 参考音频对应文本。
    """

    model_config = ConfigDict(extra="forbid")

    seed: int
    temperature: float = 0.3
    top_p: float = 0.7
    top_k: int = 20
    speaker: str
    refiner_text: str | None = None
    repetition_penalty: float = 1.05
    speed: str = "[speed_5]"
    skip_refine_text: bool = False
    max_new_token: int = 2048
    spk_smp: str | None = None
    txt_smp: str | None = None


# === 常量 ===

DEFAULT_DEMO_TEXT = "你好，这是一段声音测试。"
"""导入卡片时若 `demo_text` 为空，后端填这个默认值。"""


# === 生成 ===

class DrawRequest(BaseModel):
    """生成请求。seed/speaker 缺省时后端随机；其余字段缺省用默认值。"""
    seed: int | None = None          # None → 随机
    temperature: float = 0.3
    top_p: float = 0.7
    top_k: int = 20
    speaker: str | None = None       # None → 随机
    refiner_text: str | None = None
    demo_text: str = DEFAULT_DEMO_TEXT  # 试听文本
    repetition_penalty: float = 1.05
    speed: str = "[speed_5]"
    skip_refine_text: bool = False
    max_new_token: int = 2048
    spk_smp: str | None = None
    txt_smp: str | None = None


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
