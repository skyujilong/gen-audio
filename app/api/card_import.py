"""/api/cards/import 路由：批量导入参数卡。"""
from __future__ import annotations

from fastapi import APIRouter

from .. import config
from ..core.params import DEFAULT_DEMO_TEXT, ImportRequest, TtsParams
from ..db import queries
from ..db.database import get_connection
from ..storage.files import write_demo_files


router = APIRouter(prefix="/api/cards", tags=["import"])


@router.post("/import")
def import_cards(req: ImportRequest) -> dict[str, int]:
    """批量导入参数卡。返回 `{imported: N}`。

    每张卡：
    - `demo_text` 为空时填 `DEFAULT_DEMO_TEXT`；
    - 写空 demo 文件占位（音频 0 字节，字幕空文本）—— V1 简化。
    """
    imported = 0
    for item in req.cards:
        demo_text = item.demo_text or DEFAULT_DEMO_TEXT
        params: TtsParams = item.params

        # 先 insert 拿 id
        card_id = queries.insert_card(
            config.DB_PATH,
            name=item.name,
            params=params,
            demo_text=demo_text,
            demo_audio_path=None,
            demo_subtitle_path=None,
        )

        # 写占位文件
        paths = write_demo_files(
            data_root=config.DATA_ROOT,
            card_id=card_id,
            demo_text=demo_text,
            demo_wav_bytes=b"",  # 占位：0 字节
            demo_srt="",          # 占位
            params=params,
        )

        # update 路径
        with get_connection(config.DB_PATH) as conn:
            conn.execute(
                "UPDATE cards SET demo_audio_path=?, demo_subtitle_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (paths["demo_audio_path"], paths["demo_subtitle_path"], card_id),
            )
        if item.is_favorited:
            queries.update_card(config.DB_PATH, card_id, is_favorited=True)

        imported += 1

    return {"imported": imported}
