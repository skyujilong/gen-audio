"""/api/cards 路由：CRUD + 收藏切换 + 试听文件流。"""
from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, Response

# 故意不用 `from ..config import DB_PATH`：那样会把路径在 import 时绑定，
# 测试 monkeypatch `config.DB_PATH` 不生效。这里用 `config.DB_PATH` 惰性访问。
from .. import config
from ..core.exceptions import AudioFileNotFoundError, CardNotFoundError
from ..core.params import CardListItem, CardUpdate, TtsParams
from ..db import queries


router = APIRouter(prefix="/api/cards", tags=["cards"])


def _row_to_list_item(row: dict) -> CardListItem:
    """DB row dict → CardListItem Pydantic 模型。

    `row["params"]` 是 JSON 字符串（queries 层不预解析），需要先 `json.loads`。
    """
    params_raw = row["params"]
    if isinstance(params_raw, str):
        params_raw = json.loads(params_raw)
    return CardListItem(
        id=row["id"],
        name=row["name"],
        is_favorited=row["is_favorited"],
        demo_text=row["demo_text"],
        params=TtsParams.model_validate(params_raw),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[CardListItem])
def list_cards(favorited: bool | None = Query(default=None)) -> list[CardListItem]:
    """列卡。

    Args:
        favorited: True=只列收藏；False=只列未收藏；None=全部。
    """
    rows = queries.list_cards(config.DB_PATH, favorited=favorited)
    return [_row_to_list_item(r) for r in rows]


@router.get("/{card_id}", response_model=CardListItem)
def get_card(card_id: int) -> CardListItem:
    """取单卡详情。"""
    row = queries.get_card(config.DB_PATH, card_id)
    if row is None:
        raise CardNotFoundError(f"参数卡 {card_id} 不存在")
    return _row_to_list_item(row)


@router.patch("/{card_id}", response_model=CardListItem)
def update_card(card_id: int, body: CardUpdate) -> CardListItem:
    """更新卡的 name 和/或 is_favorited（PATCH 语义，字段都可空）。"""
    row = queries.get_card(config.DB_PATH, card_id)
    if row is None:
        raise CardNotFoundError(f"参数卡 {card_id} 不存在")
    queries.update_card(config.DB_PATH, card_id, name=body.name, is_favorited=body.is_favorited)
    updated = queries.get_card(config.DB_PATH, card_id)
    return _row_to_list_item(updated)


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: int) -> Response:
    """删卡：DB 行（连带 jobs CASCADE 删）+ 磁盘音频目录。"""
    row = queries.get_card(config.DB_PATH, card_id)
    if row is None:
        raise CardNotFoundError(f"参数卡 {card_id} 不存在")

    # 先删 DB 行（CASCADE 自动删对应 jobs）
    queries.delete_card(config.DB_PATH, card_id)

    # 再删磁盘目录
    card_audio_dir = config.DATA_ROOT / "audio" / str(card_id)
    if card_audio_dir.exists():
        shutil.rmtree(card_audio_dir, ignore_errors=True)

    return Response(status_code=204)


@router.get("/{card_id}/audio")
def get_card_audio(card_id: int) -> FileResponse:
    """试听音频流。"""
    row = queries.get_card(config.DB_PATH, card_id)
    if row is None or row["demo_audio_path"] is None:
        raise AudioFileNotFoundError(f"卡 {card_id} 没有音频文件")
    full = config.DATA_ROOT / row["demo_audio_path"]
    if not full.exists():
        raise AudioFileNotFoundError(f"音频文件不存在：{full}")
    return FileResponse(full, media_type="audio/wav")


@router.get("/{card_id}/subtitle")
def get_card_subtitle(card_id: int) -> str:
    """试听字幕（SRT 文本）。"""
    row = queries.get_card(config.DB_PATH, card_id)
    if row is None or row["demo_subtitle_path"] is None:
        raise AudioFileNotFoundError(f"卡 {card_id} 没有字幕文件")
    full = config.DATA_ROOT / row["demo_subtitle_path"]
    if not full.exists():
        raise AudioFileNotFoundError(f"字幕文件不存在：{full}")
    return full.read_text(encoding="utf-8")
