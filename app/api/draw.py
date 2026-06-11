"""/api/draw 路由。"""
from __future__ import annotations

from fastapi import APIRouter

# 故意用 `from .. import config` 而非 `from ..config import DB_PATH, DATA_ROOT`：
# 后者会在 import 时把路径绑定到模块内，测试 monkeypatch `config.DB_PATH` 不生效。
from .. import config
from ..core.chat_tts import draw_one, synthesize_to_wav_bytes
from ..core.params import DEFAULT_DEMO_TEXT, DrawRequest, DrawnCard, TtsParams
from ..core.subtitle import build_srt
from ..db import queries
from ..db.database import get_connection
from ..storage.files import write_demo_files


router = APIRouter(prefix="/api", tags=["draw"])


@router.post("/draw", response_model=DrawnCard)
def draw(req: DrawRequest) -> DrawnCard:
    """抽卡：随机生成参数 + 合 demo 试听 + 写 DB。

    流程：
    1) 抽参数；
    2) 先 `insert_card` 拿 id（路径占空 NULL）；
    3) 写文件到 `audio/<id>/`；
    4) update DB 写回路径。

    Returns:
        DrawnCard：含 `card_id`、参数、`demo_text`、试听音频/字幕 URL。
    """
    # 1) 抽参数
    params: TtsParams = draw_one(refiner_text=req.refiner_text)

    # 2) 合 demo
    audio_bytes, segments = synthesize_to_wav_bytes(
        params=params, text=DEFAULT_DEMO_TEXT,
    )
    srt = build_srt(
        [(DEFAULT_DEMO_TEXT, segments[0][0], segments[0][1])]
    ) if segments else ""

    # 3) 先 insert（路径占空）
    card_id = queries.insert_card(
        config.DB_PATH,
        name=None,
        params=params,
        demo_text=DEFAULT_DEMO_TEXT,
        demo_audio_path=None,
        demo_subtitle_path=None,
    )

    # 4) 写文件到正确目录
    paths = write_demo_files(
        data_root=config.DATA_ROOT,
        card_id=card_id,
        demo_text=DEFAULT_DEMO_TEXT,
        demo_wav_bytes=audio_bytes,
        demo_srt=srt,
        params=params,
    )

    # 5) update 路径
    with get_connection(config.DB_PATH) as conn:
        conn.execute(
            "UPDATE cards SET demo_audio_path = ?, demo_subtitle_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (paths["demo_audio_path"], paths["demo_subtitle_path"], card_id),
        )

    return DrawnCard(
        card_id=card_id,
        params=params,
        demo_text=DEFAULT_DEMO_TEXT,
        demo_audio_url=f"/api/cards/{card_id}/audio",
        demo_subtitle_url=f"/api/cards/{card_id}/subtitle",
    )
