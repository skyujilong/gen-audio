"""长文本切分：标点分级 + 字数上限兜底 + 短段合并。

本模块在 ChatTTS 的 `split_text=True` 之外，做应用层智能切分：
- 标点分级：强（。！？…\\n） → 中（；：） → 弱（，、） → 硬切（无标点超长串）
- 长度门限：`soft_max` 是日常目标，`hard_max` 是无标点长串才会触发的兜底
- 控制 token 保护：复用 `text_utils.replace_tokens` 把 `[oral_X]` 等替换成 PUA 占位符，
  占位符不算字数（real length 只数非占位符字符）
- 空 / 纯标点段过滤：含 CJK / 字母 / 数字才保留
- 短段合并：`< min_chars` 的段尝试和后一段合并（合并后超 `soft_max` 则不合并）
"""
from __future__ import annotations

import re

from .text_utils import replace_tokens, restore_tokens


# === 标点分级 ===
# 强切：必切（句末标点 + 换行）
_STRONG_PUNCT: frozenset[str] = frozenset("。！？!?.\n")
# 中切：分句符
_MEDIUM_PUNCT: frozenset[str] = frozenset("；：;:")
# 弱切：分项符
_WEAK_PUNCT: frozenset[str] = frozenset("，、,")

# 私有区占位符范围（`text_utils.replace_tokens` 用 U+E000 起）
_PUA_LO = 0xE000
_PUA_HI = 0xF8FF

# 「有内容」检查：含 CJK / 字母 / 数字才保留（占位符落在 PUA 区，不会命中）
_CONTENT_RE = re.compile(r"[一-鿿a-zA-Z0-9]")


def _is_placeholder(ch: str) -> bool:
    """判断一个字符是不是 `replace_tokens` 生成的私有区占位符。"""
    return _PUA_LO <= ord(ch) <= _PUA_HI


def _real_len(s: str) -> int:
    """真实字数：占位符（控制 token）不计入。"""
    return sum(1 for ch in s if not _is_placeholder(ch))


def _has_content(s: str) -> bool:
    """段内是否含字母 / 数字 / 中文（用于过滤纯标点 / 纯空白 / 纯控制 token 段）。

    注意必须传 **safe**（未还原）文本：占位符在 PUA 区，不会匹配本正则；
    若传还原后的文本，`[oral_3]` 里的 `3` 会被正则命中，绕过过滤。
    """
    return bool(_CONTENT_RE.search(s))


def _split_keep_delim(text: str, delims: frozenset[str]) -> list[str]:
    """按 delims 切分，分隔符留在前段末尾。空段过滤掉。"""
    pieces: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in delims:
            pieces.append("".join(buf))
            buf = []
    if buf:
        pieces.append("".join(buf))
    return [p for p in pieces if p]


def _refine_long(segs: list[str], delims: frozenset[str], soft_max: int) -> list[str]:
    """对超过 soft_max 的段再用更弱一档的标点切。短段不动。"""
    out: list[str] = []
    for s in segs:
        if _real_len(s) <= soft_max:
            out.append(s)
        else:
            out.extend(_split_keep_delim(s, delims))
    return out


def _hard_cut(text: str, hard_max: int) -> list[str]:
    """硬切兜底：按真实字数（不算占位符）每 hard_max 个砍一刀。"""
    pieces: list[str] = []
    buf: list[str] = []
    cnt = 0
    for ch in text:
        buf.append(ch)
        if not _is_placeholder(ch):
            cnt += 1
        if cnt >= hard_max:
            pieces.append("".join(buf))
            buf = []
            cnt = 0
    if buf:
        pieces.append("".join(buf))
    return pieces


def _refine_hard(segs: list[str], hard_max: int) -> list[str]:
    """只对仍超过 hard_max 的段做硬切。"""
    out: list[str] = []
    for s in segs:
        if _real_len(s) <= hard_max:
            out.append(s)
        else:
            out.extend(_hard_cut(s, hard_max))
    return out


def _merge_short(
    segs: list[str], min_chars: int, soft_max: int
) -> list[str]:
    """短段合并：< min_chars 的段尝试与后一段合并；合并后超 soft_max 则不合并。"""
    out: list[str] = []
    i = 0
    while i < len(segs):
        cur = segs[i]
        # 反复尝试合并，直到不再短或合并会超 soft_max
        while _real_len(cur) < min_chars and i + 1 < len(segs):
            combined = cur + segs[i + 1]
            if _real_len(combined) <= soft_max:
                cur = combined
                i += 1
            else:
                break
        out.append(cur)
        i += 1
    return out


def split_text(
    text: str,
    *,
    soft_max: int = 20,
    hard_max: int = 35,
    min_chars: int = 4,
) -> list[str]:
    """把长文本按标点 + 字数切成短段，准备喂给 ChatTTS 单段合成。

    Args:
        text: 待切分的原文（可含 ChatTTS 控制 token，如 `[oral_3]`）。
        soft_max: 日常目标段长（中文 15-20 字最稳）。超过此值会用更弱的标点再切。
        hard_max: 兜底上限。仅当一段无标点且超过此值时硬切。
        min_chars: 短段阈值。短于此值的段尝试和后一段合并。

    Returns:
        切分后的段列表，每段已 `restore_tokens` 还原 + `strip()` 收尾空白；
        全空 / 纯标点的段会被过滤掉。

    切分顺序（每步只处理上一步剩下的「过长」段）：
      1. 强切：`[。！？!?.]` + `\\n`
      2. 中切：`[；：;:]`
      3. 弱切：`[，、,]`
      4. 硬切：仍 > hard_max 的无标点长串按字数砍
      5. 过滤：纯空白 / 纯标点 / 纯控制 token 段丢掉
      6. 合并：< min_chars 的段并入后一段（不超 soft_max 的前提下）
    """
    if not text:
        return []

    # 控制 token 替换成 PUA 单字符占位符，后续算长度时不计入
    safe, pairs = replace_tokens(text)

    segs = _split_keep_delim(safe, _STRONG_PUNCT)
    # 强切完仍空（输入是纯标点 / 空白）→ 直接走后续流程，过滤步会清掉
    if not segs:
        segs = [safe]
    segs = _refine_long(segs, _MEDIUM_PUNCT, soft_max)
    segs = _refine_long(segs, _WEAK_PUNCT, soft_max)
    segs = _refine_hard(segs, hard_max)

    # 过滤：基于 safe 文本判断「有内容」（占位符不算内容）
    segs = [s for s in segs if _has_content(s)]

    # 短段合并
    segs = _merge_short(segs, min_chars, soft_max)

    # 还原控制 token + 收尾空白
    out: list[str] = []
    for s in segs:
        restored = restore_tokens(s, pairs).strip()
        if restored:
            out.append(restored)
    return out
