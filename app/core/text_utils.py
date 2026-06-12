"""文本预处理工具：保护 ChatTTS 控制 token 不被 refine 阶段破坏。

ChatTTS 0.2.5 的 `infer(..., params_refine_text=..., skip_refine_text=False)` 会先跑
`refine_text` 阶段（加标点、改口语化），但会破坏我们注入的 `[oral_X]` / `[laugh_X]` /
`[break_X]` / `[speed_X]` / `[uv_break]` 等控制 token。

解法：refine 前用占位符替换 → refine 后恢复原 token。

参考：ChatTTS-Enhanced-main 的 `utils/text_utils.py:236-252`。
"""
from __future__ import annotations

import re
from typing import NamedTuple


# 匹配 ChatTTS 控制 token：oral_X / laugh_X / break_X / speed_X (X 是 0-9) + uv_break
_TOKEN_PATTERN = re.compile(
    r"\[(?:oral|laugh|break|speed)_\d+\]|\[uv_break\]"
)


class _TokenPair(NamedTuple):
    placeholder: str
    original: str


def replace_tokens(text: str) -> tuple[str, list[tuple[str, str]]]:
    """把 ChatTTS 控制 token 替换成占位符，返回 (新文本, [(占位符, 原 token), ...])。

    替换策略：用 0-200 Unicode 私有区字符（U+E000–U+E0C8）作占位符——这些字符
    ChatTTS 训练数据里几乎不会出现，refine 阶段不会动它们。

    Args:
        text: 含 ChatTTS token 的原文。

    Returns:
        `(new_text, pairs)` —— 用 `new_text` 去 refine，再 `restore_tokens` 恢复。
    """
    pairs: list[tuple[str, str]] = []

    def _sub(match: re.Match) -> str:
        original = match.group(0)
        # 顺序生成唯一占位符（U+E000 起）
        placeholder = chr(0xE000 + len(pairs))
        pairs.append((placeholder, original))
        return placeholder

    new_text = _TOKEN_PATTERN.sub(_sub, text)
    return new_text, pairs


def restore_tokens(text: str, pairs: list[tuple[str, str]]) -> str:
    """把占位符恢复成原 ChatTTS token。"""
    for placeholder, original in pairs:
        text = text.replace(placeholder, original)
    return text
