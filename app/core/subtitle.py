"""SRT 字幕生成工具。

提供两个公开函数：
- `build_srt(segments)`: 把 `[(text, start_sec, end_sec), ...]` 拼成 SRT 字符串。
- `slice_by_punctuation(text)`: 按中英文标点（。！？.!?；;）把长文本切成短句。
"""
from __future__ import annotations

import re

# 中英文常见句末标点（含半角和全角）
_PUNCT_PATTERN = re.compile(r"[。！？\.!\?；;]+")


def slice_by_punctuation(text: str) -> list[str]:
    """按句末标点切分文本，每段保留末尾标点。

    Args:
        text: 输入长文本。

    Returns:
        切分后的句子列表。如果文本里没有标点，整段作为一项返回。
    """
    pieces: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if _PUNCT_PATTERN.fullmatch(ch):
            pieces.append("".join(buf).strip())
            buf = []
    if buf:
        tail = "".join(buf).strip()
        if tail:
            pieces.append(tail)
    return [p for p in pieces if p]


def _format_timestamp(seconds: float) -> str:
    """把秒数格式化成 SRT 时间戳 `HH:MM:SS,mmm`。"""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3600 * 1000)
    minutes, rem = divmod(rem, 60 * 1000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(segments: list[tuple[str, float, float]]) -> str:
    """把段列表拼成 SRT 格式字符串。

    Args:
        segments: `[(text, start_sec, end_sec), ...]`，时间单位为秒。

    Returns:
        完整 SRT 文本（多段用 `\\n\\n` 分隔），空列表返回空字符串。

    SRT 格式示例：
        1
        00:00:00,000 --> 00:00:02,500
        你好世界
    """
    if not segments:
        return ""

    blocks: list[str] = []
    for idx, (text, start, end) in enumerate(segments, start=1):
        start_ts = _format_timestamp(start)
        end_ts = _format_timestamp(end)
        blocks.append(f"{idx}\n{start_ts} --> {end_ts}\n{text.strip()}")

    return "\n\n".join(blocks) + "\n"
