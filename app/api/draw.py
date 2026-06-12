"""/api/draw 路由（生成参数 + 试听）。"""
from __future__ import annotations

from fastapi import APIRouter

# 故意用 `from .. import config` 而非 `from ..config import DB_PATH, DATA_ROOT`：
# 后者会在 import 时把路径绑定到模块内，测试 monkeypatch `config.DB_PATH` 不生效。
from .. import config
from ..core.chat_tts import draw_one_from_params, synthesize_to_wav_bytes
from ..core.params import DrawRequest, DrawnCard, TtsParams
from ..core.subtitle import build_srt
from ..db import queries
from ..db.database import get_connection
from ..storage.files import write_demo_files
# Phase 4.1：复用 speakers 路由的随机音色实现 + 错误码。
from .speakers import SpeakerNotFoundError, random_speaker_payload


router = APIRouter(prefix="/api", tags=["draw"])


@router.post("/draw", response_model=DrawnCard)
def draw(req: DrawRequest) -> DrawnCard:
    """生成：随机/自定义参数 + 合 demo 试听 + 写 DB。

    流程：
    1) 若 `speaker_id` 传入：从 speakers 库读出 `tensor_base64` 作为 `speaker` 字符串
       （**双轨引用**：本卡 params.speaker = 字符串快照，cards.speaker_id = FK）；
    2) 生成参数（seed/speaker 为 None 时随机，其余使用传入值）；
    3) 先 `insert_card` 拿 id（路径占空 NULL，同时把 `speaker_id` 写入）；
    4) 写文件到 `audio/<id>/`；
    5) update DB 写回路径。

    Returns:
        DrawnCard：含 `card_id`、参数、`demo_text`、试听音频/字幕 URL。
    """
    # 0) Phase 4.1: 解析 speaker_id → 字符串快照（优先级：speaker_id > 显式 speaker）
    resolved_speaker_id: int | None = req.speaker_id
    resolved_speaker: str | None = req.speaker
    if resolved_speaker_id is not None:
        spk_row = queries.get_speaker(config.DB_PATH, resolved_speaker_id)
        if spk_row is None:
            raise SpeakerNotFoundError(f"音色 {resolved_speaker_id} 不存在")
        # 用库里的字符串覆盖（保证一致性），但允许调用方仍能传 speaker 覆写
        resolved_speaker = spk_row["tensor_base64"]

    # 1) 生成参数
    params: TtsParams = draw_one_from_params(
        seed=req.seed,
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        speaker=resolved_speaker,
        refiner_text=req.refiner_text,
        repetition_penalty=req.repetition_penalty,
        speed=req.speed,
        skip_refine_text=req.skip_refine_text,
        max_new_token=req.max_new_token,
        spk_smp=req.spk_smp,
        txt_smp=req.txt_smp,
        oral=req.oral,
        laugh=req.laugh,
        break_=req.break_,
        enhance_audio=req.enhance_audio,
        denoise_audio=req.denoise_audio,
        solver=req.solver,
        nfe=req.nfe,
        tau=req.tau,
    )

    demo_text = req.demo_text

    # 2) 合 demo（**试听强制不增强**——抽卡需快速反馈，不走 enhance 拖慢节奏）
    # 即便前端带了 enhance/denoise 参数，draw 路由也强制关闭。
    params_试听 = params.model_copy(update={"enhance_audio": False, "denoise_audio": False})
    audio_bytes, segments = synthesize_to_wav_bytes(
        params=params_试听, text=demo_text,
    )
    srt = build_srt(
        [(demo_text, segments[0][0], segments[0][1])]
    ) if segments else ""

    # 3) 先 insert（路径占空）——Phase 4.1: 把 resolved_speaker_id 也写入
    card_id = queries.insert_card(
        config.DB_PATH,
        name=None,
        params=params,
        demo_text=demo_text,
        demo_audio_path=None,
        demo_subtitle_path=None,
        speaker_id=resolved_speaker_id,
    )

    # 4) 写文件到正确目录
    paths = write_demo_files(
        data_root=config.DATA_ROOT,
        card_id=card_id,
        demo_text=demo_text,
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
        demo_text=demo_text,
        demo_audio_url=f"/api/cards/{card_id}/audio",
        demo_subtitle_url=f"/api/cards/{card_id}/subtitle",
    )


@router.get("/draw/random_speaker")
def random_speaker() -> dict:
    """返回一个随机 speaker embedding，供前端预览音色。

    Phase 4.1: 与 /api/speakers/random 共用同一份实现（同一形状
    `{speaker_id: null, tensor_base64: ...}`），由前端按需决定如何处理
    speaker_id 字段。
    """
    return random_speaker_payload()
