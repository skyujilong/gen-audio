"""文本规范化（Text Normalization）：把数字 / 日期 / 单位等转成中文念法。

封装 WeTextProcessing 的 `Normalizer`：
- `1998` → `一千九百九十八` / `一九九八`（库自带规则决定）
- `3.14` → `三点一四`
- `30°C` → `三十摄氏度`
- `2024年3月15日` → 中文日期念法

设计要点：
- **可选依赖**：`WeTextProcessing` 在 macOS 上需要 `brew install openfst`，可能装不上。
  本模块顶部 try-import，未装时 `ZhNormalizer = None`，`is_loaded()` 永远返回 False，
  `normalize_text()` 直接 return 原文（fallback），不抛。
- **lifespan 预热**：`load_normalizer()` 编译 FST 5–30s，由 `app/main.py` 的 lifespan
  和 ChatTTS 模型加载并行后台跑（`asyncio.gather` + `to_thread`）。
- **失败永远 fallback**：TN 是优化项不是必需项；任何异常 → 返回原文，记 WARNING。
- **控制 token 保护**：复用 `text_utils.replace_tokens` 把 `[oral_X]` 等替换成 PUA
  占位符，TN 跑完后再 `restore_tokens` 还原。
- **TEXT_NORM_ENABLED=false** → 跳过 TN。

测试 monkeypatch 提示：用 `from .. import config; config.TEXT_NORM_ENABLED`，
不要 `from ..config import TEXT_NORM_ENABLED`，否则测试无法 patch。
"""
from __future__ import annotations

import logging
from typing import Any

from .text_utils import replace_tokens, restore_tokens
from .. import config

logger = logging.getLogger(__name__)


# === 可选依赖 ===

try:
    from tn.chinese.normalizer import Normalizer as ZhNormalizer  # type: ignore[import-not-found]
except Exception:  # ImportError / 也兼容 init 阶段抛错
    ZhNormalizer = None  # type: ignore[assignment, misc]


# === 单例状态 ===

_normalizer: Any = None
_status: str = "disabled"
"""'disabled' | 'loading' | 'ok' | 'error' —— 由 `/api/health` 暴露。"""


def status() -> str:
    """返回当前 TN 状态字符串（给 health 接口用）。"""
    return _status


def is_loaded() -> bool:
    """是否已加载完成、可以 normalize。"""
    return _normalizer is not None and _status == "ok"


def load_normalizer() -> None:
    """加载并预热 normalizer。**同步阻塞**调用（5–30s 编译 FST），
    由 `lifespan` 用 `asyncio.to_thread` 后台跑。

    失败时不抛，把状态置 `error`，`normalize_text` 仍可调（直接返回原文）。
    `TEXT_NORM_ENABLED=false` 时直接置 `disabled`，跳过加载。
    """
    global _normalizer, _status

    if not config.TEXT_NORM_ENABLED:
        _status = "disabled"
        logger.info("[text_norm] TEXT_NORM_ENABLED=false, skipped")
        return

    if ZhNormalizer is None:
        _status = "disabled"
        logger.warning(
            "[text_norm] WeTextProcessing 未安装，TN 不可用（数字 / 日期 / 单位仍走 ChatTTS 默认行为）"
        )
        return

    if _normalizer is not None:
        return  # 已加载

    _status = "loading"
    cache_dir = config.TEXT_NORM_CACHE_DIR
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "[text_norm] 开始编译 FST normalizer (cache_dir=%s remove_erhua=%s)，可能耗时数十秒…",
            cache_dir, config.TEXT_NORM_REMOVE_ERHUA,
        )
        # WeTextProcessing 的 Normalizer 接受 cache_dir / remove_erhua / overwrite_cache 等参数
        _normalizer = ZhNormalizer(
            remove_erhua=config.TEXT_NORM_REMOVE_ERHUA,
            cache_dir=str(cache_dir),
            overwrite_cache=False,
        )
        _status = "ok"
        logger.info("[text_norm] normalizer 加载完成")
    except Exception as e:
        _normalizer = None
        _status = "error"
        logger.warning("[text_norm] normalizer 加载失败，将 fallback 到原文：%s", e)


def normalize_text(text: str) -> str:
    """把文本规范化（数字 / 日期 / 单位 → 中文念法）。

    Args:
        text: 原始文本，可含 ChatTTS 控制 token（如 `[oral_3]`）。

    Returns:
        规范化后的文本；任何异常 / 未加载 → 原样返回。

    流程：
        1. `TEXT_NORM_ENABLED=false` 或 normalizer 未加载 → return text
        2. 控制 token 替换成 PUA 占位符（不被 normalizer 破坏）
        3. 跑 normalizer.normalize(safe_text)
        4. 恢复占位符
        5. 任意步骤抛错 → 记 WARNING，return 原文
    """
    if not config.TEXT_NORM_ENABLED:
        return text
    if not is_loaded():
        return text

    try:
        safe, pairs = replace_tokens(text)
        normalized_safe = _normalizer.normalize(safe)
        return restore_tokens(normalized_safe, pairs)
    except Exception as e:
        logger.warning(
            "[text_norm] normalize 失败，fallback 原文：text=%r err=%s",
            text[:60], e,
        )
        return text
