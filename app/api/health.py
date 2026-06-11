"""/api/health 路由。"""
from __future__ import annotations

from fastapi import APIRouter

from ..core import chat_tts
from ..core.params import HealthResponse
from ..core.queue import get_queue_size


router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """健康检查 + 模型 + 队列状态。

    Returns:
        - `status="loading"` + `model_loaded=False`：模型还在加载。
        - `status="ok"` + `model_loaded=True` + `queue_size`：正常态。
    """
    if not chat_tts.is_model_loaded():
        return HealthResponse(status="loading", model_loaded=False, queue_size=0)
    return HealthResponse(
        status="ok",
        model_loaded=True,
        queue_size=get_queue_size(),
    )
