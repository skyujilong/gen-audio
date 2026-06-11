"""FastAPI 应用入口。

职责：
- 创建 FastAPI app
- 注册 `AppError` 异常 handler（统一转为 JSON 响应）
- 启动事件：初始化 DB + 异步加载 ChatTTS 模型 + 初始化合成队列
- 挂载 `/api/*` 路由
- 挂载 `static/` 为 `/`（前端）
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import card_import, cards, draw, health, jobs, synthesize
from .config import DB_PATH, DATA_ROOT, STATIC_DIR
from .core import chat_tts
from .core.exceptions import AppError
from .core.queue import init_queue
from .core.queue import shutdown as queue_shutdown
from .db.database import init_schema


# === 日志配置 ===

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


# === 生命周期 ===

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 / 关闭钩子。"""
    # 启动：建 DB + 异步加载模型 + 启动队列
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    init_schema(DB_PATH)
    log.info("数据库已就绪：%s", DB_PATH)

    async def _load_model_in_background() -> None:
        """后台加载 ChatTTS 模型：失败不影响应用启动。"""
        try:
            await asyncio.to_thread(chat_tts.load_model)
            log.info("ChatTTS 模型加载完成")
        except Exception as e:
            # 不吞错：记日志，由 /api/health 暴露 loading 状态
            log.error("ChatTTS 模型加载失败：%s", e)

    asyncio.create_task(_load_model_in_background())

    # 初始化合成队列（启动 worker + 清理脏数据）
    init_queue(DB_PATH)

    yield

    # 关闭：停 worker
    await queue_shutdown()
    log.info("应用关闭")


app = FastAPI(title="gen-audio", lifespan=lifespan)


# === AppError 异常 handler ===

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """业务异常统一转 `{detail, code}` JSON 响应。"""
    return JSONResponse(
        status_code=exc.status,
        content={"detail": exc.detail, "code": exc.code},
    )


# === API 路由 ===

app.include_router(health.router)
app.include_router(draw.router)
app.include_router(cards.router)
app.include_router(card_import.router)
app.include_router(synthesize.router)
app.include_router(jobs.router)


# === 静态文件（前端） ===

# 干净 URL 路由：/draw → draw.html、/favorites → favorites.html、/synthesize → synthesize.html
# Starlette StaticFiles 的 html=True 不支持无后缀查找，这里显式注册。
_PAGE_ROUTES = {
    "/draw": "draw.html",
    "/favorites": "favorites.html",
    "/synthesize": "synthesize.html",
}


@app.get("/draw", include_in_schema=False)
def _page_draw() -> FileResponse:
    """干净 URL：/draw → draw.html。"""
    return FileResponse(STATIC_DIR / "draw.html", media_type="text/html")


@app.get("/favorites", include_in_schema=False)
def _page_favorites() -> FileResponse:
    """干净 URL：/favorites → favorites.html。"""
    return FileResponse(STATIC_DIR / "favorites.html", media_type="text/html")


@app.get("/synthesize", include_in_schema=False)
def _page_synthesize() -> FileResponse:
    """干净 URL：/synthesize → synthesize.html。"""
    return FileResponse(STATIC_DIR / "synthesize.html", media_type="text/html")


# 只有在已经有前端文件（index.html）时才挂载为根路径；
# 仅含 .gitkeep 的空目录不能挂载，否则会拦截所有非 API 请求。
if STATIC_DIR.exists() and (STATIC_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
