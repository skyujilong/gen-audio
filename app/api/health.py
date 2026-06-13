"""/api/health 路由。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import chat_tts, text_norm
from ..core.params import HealthResponse
from ..core.queue import get_queue_size


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """健康检查 + 模型 + 队列状态 + TN 状态。

    Returns:
        - `status="loading"` + `model_loaded=False`：ChatTTS 模型还在加载。
        - `status="ok"` + `model_loaded=True` + `queue_size`：正常态。
        - `tn_status` ∈ {`loading`, `ok`, `error`, `disabled`}：TN 是优化项，
          `error` / `disabled` 时合成仍可用（数字念法走 ChatTTS 默认行为）。
    """
    if not chat_tts.is_model_loaded():
        return HealthResponse(
            status="loading", model_loaded=False, queue_size=0,
            tn_status=text_norm.status(),
        )
    return HealthResponse(
        status="ok",
        model_loaded=True,
        queue_size=get_queue_size(),
        tn_status=text_norm.status(),
    )
