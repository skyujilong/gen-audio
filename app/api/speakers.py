"""/api/speakers 路由：音色库 CRUD + 上传 + 搜索 + 收藏 + 随机音色。

8 个端点：
- GET    /                       列表（不含 tensor，支持 favorited/search 过滤）
- GET    /random                 随机返回一个 speaker embedding（不写库）
- GET    /{id}                   详情（含 tensor_base64）
- POST   /                       从 tensor_base64 创建（同步写 .pt 文件）
- POST   /upload                 从上传的 .pt 文件创建
- PATCH  /{id}                   更新 name / tags / is_favorited
- DELETE /{id}                   删除（cards.speaker_id 自动 SET NULL + 删 .pt 文件）
- POST   /{id}/favorite          切收藏
"""
from __future__ import annotations

import base64
import io
import json
from typing import Any

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response

from .. import config
from ..core.chat_tts import _random_speaker
from ..core.exceptions import AppError
from ..core.params import (
    SpeakerCreate,
    SpeakerListItem,
    SpeakerOut,
    SpeakerUpdate,
)
from ..db import queries
from ..storage.speakers import SpeakerStorage


router = APIRouter(prefix="/api/speakers", tags=["speakers"])


# === 业务异常 ===

class SpeakerNotFoundError(AppError):
    """音色库项不存在。"""
    code = "SPEAKER_NOT_FOUND"
    status = 404


class SpeakerUploadError(AppError):
    """上传的 .pt 文件无法解析或 base64 损坏。"""
    code = "SPEAKER_UPLOAD_INVALID"
    status = 400


# === 行 → Pydantic 转换 ===

def _row_to_list_item(row: dict) -> SpeakerListItem:
    return SpeakerListItem(
        id=row["id"],
        name=row["name"],
        tags=row["tags"],
        is_favorited=row["is_favorited"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_out(row: dict) -> SpeakerOut:
    return SpeakerOut(
        id=row["id"],
        name=row["name"],
        tensor_base64=row["tensor_base64"],
        tags=row["tags"],
        is_favorited=row["is_favorited"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _storage() -> SpeakerStorage:
    return SpeakerStorage(config.SPEAKERS_DIR)


def _load_tensor_from_bytes(raw: bytes) -> Any:
    """解析 .pt 字节为 torch.Tensor。失败抛 SpeakerUploadError。"""
    try:
        import torch
        return torch.load(io.BytesIO(raw), map_location="cpu", weights_only=False)
    except Exception as e:
        raise SpeakerUploadError(f".pt 文件解析失败：{e}")


def _parse_tags_form(raw: str | None) -> list[str]:
    """Form 字段 tags 是 JSON 字符串；None/空 → []。"""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return v
    except Exception:
        pass
    raise SpeakerUploadError(f"tags 必须是 JSON 字符串数组，got {raw!r}")


def _parse_favorited_form(raw: str | None) -> bool:
    """Form 字段 is_favorited 是 "true"/"false"/"1"/"0"。None → False。"""
    if raw is None:
        return False
    return raw.strip().lower() in ("true", "1", "yes")


# === 路由 ===

@router.get("", response_model=list[SpeakerListItem])
def list_speakers(
    favorited: bool | None = Query(default=None),
    search: str | None = Query(default=None),
) -> list[SpeakerListItem]:
    """列音色库（**不**带 tensor，节省带宽）。

    Args:
        favorited: True=只列收藏；None=全部。
        search: 可选 name 模糊匹配。
    """
    rows = queries.list_speakers(config.DB_PATH, favorited=favorited, search=search)
    return [_row_to_list_item(r) for r in rows]


@router.get("/random")
def random_speaker() -> dict:
    """返回一个随机 speaker embedding（**不**写库），用于前端预览音色。

    Returns:
        `{speaker_id: null, tensor_base64: "..."}`
    """
    return {
        "speaker_id": None,
        "tensor_base64": _random_speaker(),
    }


@router.get("/{speaker_id}", response_model=SpeakerOut)
def get_speaker(speaker_id: int) -> SpeakerOut:
    """取音色详情（含 tensor_base64）。"""
    row = queries.get_speaker(config.DB_PATH, speaker_id)
    if row is None:
        raise SpeakerNotFoundError(f"音色 {speaker_id} 不存在")
    return _row_to_out(row)


@router.post("", response_model=SpeakerOut)
def create_speaker(body: SpeakerCreate) -> SpeakerOut:
    """从 tensor_base64 创建一个音色，同时把 .pt 写到 SPEAKERS_DIR/{id}.pt。"""
    # 1) 校验 base64 能解出 tensor
    try:
        raw = base64.b64decode(body.tensor_base64)
    except Exception as e:
        raise SpeakerUploadError(f"tensor_base64 不是合法 base64：{e}")
    tensor = _load_tensor_from_bytes(raw)

    # 2) 插库拿 id
    sid = queries.insert_speaker(
        config.DB_PATH,
        name=body.name,
        tensor_base64=body.tensor_base64,
        tags=body.tags,
        is_favorited=body.is_favorited,
    )
    # 3) 写 .pt（失败回滚：删 row）
    try:
        _storage().save_tensor(sid, tensor)
    except Exception as e:
        queries.delete_speaker(config.DB_PATH, sid)
        raise SpeakerUploadError(f".pt 文件写入失败：{e}")

    row = queries.get_speaker(config.DB_PATH, sid)
    return _row_to_out(row)


@router.post("/upload", response_model=SpeakerOut)
def upload_speaker(
    file: UploadFile = File(...),
    name: str = Form(...),
    tags: str | None = Form(default=None),
    is_favorited: str | None = Form(default=None),
) -> SpeakerOut:
    """从上传的 .pt 文件创建一个音色。

    Multipart form：
    - `file`: .pt 文件
    - `name`: 音色名
    - `tags`: JSON 字符串数组（可选）
    - `is_favorited`: "true"/"false"（可选）
    """
    tags_list = _parse_tags_form(tags)
    fav_bool = _parse_favorited_form(is_favorited)

    # 1) 读上传文件 → tensor
    raw = file.file.read()
    tensor = _load_tensor_from_bytes(raw)

    # 2) 插库（拿 id）
    tensor_base64 = base64.b64encode(raw).decode("ascii")
    sid = queries.insert_speaker(
        config.DB_PATH,
        name=name,
        tensor_base64=tensor_base64,
        tags=tags_list,
        is_favorited=fav_bool,
    )
    # 3) 写 .pt（失败回滚）
    try:
        _storage().save_tensor(sid, tensor)
    except Exception as e:
        queries.delete_speaker(config.DB_PATH, sid)
        raise SpeakerUploadError(f".pt 文件写入失败：{e}")

    row = queries.get_speaker(config.DB_PATH, sid)
    return _row_to_out(row)


@router.patch("/{speaker_id}", response_model=SpeakerOut)
def update_speaker(speaker_id: int, body: SpeakerUpdate) -> SpeakerOut:
    """更新音色 name / tags / is_favorited（PATCH 语义，字段都可空）。"""
    row = queries.get_speaker(config.DB_PATH, speaker_id)
    if row is None:
        raise SpeakerNotFoundError(f"音色 {speaker_id} 不存在")
    queries.update_speaker(
        config.DB_PATH,
        speaker_id,
        name=body.name,
        tags=body.tags,
        is_favorited=body.is_favorited,
    )
    updated = queries.get_speaker(config.DB_PATH, speaker_id)
    return _row_to_out(updated)


@router.delete("/{speaker_id}", status_code=204)
def delete_speaker(speaker_id: int) -> Response:
    """删除音色：cards.speaker_id 已被应用层 SET NULL（`queries.delete_speaker` 负责），同时删 .pt 文件。"""
    row = queries.get_speaker(config.DB_PATH, speaker_id)
    if row is None:
        raise SpeakerNotFoundError(f"音色 {speaker_id} 不存在")
    # 先删 DB 行（应用层先把 cards.speaker_id 置 NULL）
    queries.delete_speaker(config.DB_PATH, speaker_id)
    # 再删 .pt 文件（找不到也 OK，幂等）
    _storage().delete(speaker_id)
    return Response(status_code=204)


@router.post("/{speaker_id}/favorite", response_model=SpeakerOut)
def toggle_favorite(speaker_id: int) -> SpeakerOut:
    """切收藏状态。"""
    new_val = queries.toggle_speaker_favorite(config.DB_PATH, speaker_id)
    if new_val is None:
        raise SpeakerNotFoundError(f"音色 {speaker_id} 不存在")
    row = queries.get_speaker(config.DB_PATH, speaker_id)
    return _row_to_out(row)
