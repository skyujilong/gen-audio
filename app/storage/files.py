"""音频 / 字幕 / 参数快照的文件存储。

约定：
- 所有路径都以"相对于 data_root 的 POSIX 字符串"形式存进 DB（如 `audio/1/demo.wav`）。
- 函数内部用 `data_root / rel_path` 拼绝对路径。
- 文件系统操作失败时抛 `OSError`，由调用方决定是否转为 `AppError`。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..core.params import TtsParams


# === 目录工具 ===

def card_dir(data_root: Path, card_id: int) -> Path:
    """获取 / 创建单张卡的目录。"""
    p = data_root / "audio" / str(card_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def job_dir(data_root: Path, card_id: int, job_id: str) -> Path:
    """获取 / 创建单个合成任务的目录。"""
    p = data_root / "audio" / str(card_id) / "jobs" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# === 写入 ===

def write_demo_files(
    *,
    data_root: Path,
    card_id: int,
    demo_text: str,
    demo_wav_bytes: bytes,
    demo_srt: str,
    params: TtsParams,
) -> dict[str, str]:
    """写入一张新卡的三件套（demo 音频 / 字幕 / 参数快照）。

    Args:
        data_root: 数据根目录。
        card_id: 卡 id。
        demo_text: demo 文本。
        demo_wav_bytes: 音频字节。
        demo_srt: 字幕内容。
        params: 卡片参数。

    Returns:
        三个文件的相对路径字典，键为 `demo_audio_path` / `demo_subtitle_path` / `params_path`。
    """
    base = card_dir(data_root, card_id)
    audio_full = base / "demo.wav"
    srt_full = base / "demo.srt"
    params_full = base / "params.json"

    audio_full.write_bytes(demo_wav_bytes)
    srt_full.write_text(demo_srt, encoding="utf-8")
    params_full.write_text(
        json.dumps(params.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "demo_audio_path": "audio/{cid}/demo.wav".format(cid=card_id),
        "demo_subtitle_path": "audio/{cid}/demo.srt".format(cid=card_id),
        "params_path": "audio/{cid}/params.json".format(cid=card_id),
    }


def write_synthesis_files(
    *,
    data_root: Path,
    card_id: int,
    job_id: str,
    audio_bytes: bytes,
    srt: str,
    params: TtsParams,
) -> dict[str, str]:
    """写入一次合成产物的三件套（音频 / 字幕 / 参数快照）。"""
    base = job_dir(data_root, card_id, job_id)
    audio_full = base / "audio.wav"
    srt_full = base / "subtitle.srt"
    params_full = base / "params.json"

    audio_full.write_bytes(audio_bytes)
    srt_full.write_text(srt, encoding="utf-8")
    params_full.write_text(
        json.dumps(params.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "audio_path": f"audio/{card_id}/jobs/{job_id}/audio.wav",
        "subtitle_path": f"audio/{card_id}/jobs/{job_id}/subtitle.srt",
        "params_path": f"audio/{card_id}/jobs/{job_id}/params.json",
    }


# === 删除 ===

def safe_delete(data_root: Path, rel_path: str) -> None:
    """按相对路径删文件 / 目录；路径不存在不报错。

    如果父目录变空，顺手把空父目录也清掉，避免 `data/audio/<id>/jobs/<id>/` 残留空目录。
    """
    target = data_root / rel_path
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    elif target.exists():
        target.unlink()
    # 顺手上溯删空目录
    for parent in target.parents:
        if parent == data_root:
            break
        if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
            try:
                parent.rmdir()
            except OSError:
                break
