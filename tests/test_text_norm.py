"""text_norm 单元测试。

WeTextProcessing 在 macOS 上需要 brew install openfst，未必装得上。本测试文件设计成：
- WeTextProcessing 装好时 → 跑真实归一化（数字 / 日期 / 单位 → 中文念法）
- 没装时 → 跑 fallback 路径（is_loaded=False → return 原文不抛）
- TEXT_NORM_ENABLED=false 时 → 跳过 TN

`monkeypatch` 方式遵循 CLAUDE.md 的提示：用 `setattr(config, "X", ...)` 改 config，
用 `setattr(text_norm, "_normalizer", ...)` 注入假的 normalizer。
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app import config
from app.core import text_norm


# === Fixture：在每个测试前重置 text_norm 模块状态 ===

@pytest.fixture(autouse=True)
def _reset_text_norm():
    """每个测试前后把 module-level state 清干净，避免相互污染。"""
    yield
    text_norm._normalizer = None
    text_norm._status = "disabled"


# === 基础行为：未加载 / 关闭时 fallback ===

def test_normalize_returns_original_when_not_loaded():
    """`_normalizer is None` → 直接 return 原文。"""
    text_norm._normalizer = None
    text_norm._status = "disabled"
    assert text_norm.normalize_text("1998年") == "1998年"


def test_normalize_returns_original_when_disabled(monkeypatch):
    """TEXT_NORM_ENABLED=False → 直接 return 原文（即使 normalizer 已加载）。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", False)
    text_norm._normalizer = MagicMock()
    text_norm._normalizer.normalize.return_value = "should-not-be-used"
    text_norm._status = "ok"
    assert text_norm.normalize_text("1998年") == "1998年"


def test_is_loaded_only_true_when_normalizer_and_status_ok():
    """is_loaded() 只在 normalizer 非空且 status='ok' 时返回 True。"""
    text_norm._normalizer = None
    text_norm._status = "disabled"
    assert text_norm.is_loaded() is False

    text_norm._normalizer = MagicMock()
    text_norm._status = "loading"
    assert text_norm.is_loaded() is False

    text_norm._status = "ok"
    assert text_norm.is_loaded() is True

    text_norm._status = "error"
    assert text_norm.is_loaded() is False


def test_status_returns_current_status():
    """status() 返回 module-level _status。"""
    text_norm._status = "ok"
    assert text_norm.status() == "ok"

    text_norm._status = "loading"
    assert text_norm.status() == "loading"


# === Fake normalizer 注入：测 normalize 流程不依赖真实库 ===

def test_normalize_calls_underlying_normalizer():
    """normalize_text 应调底层 normalizer.normalize。"""
    fake = MagicMock()
    fake.normalize.return_value = "一千九百九十八年"
    text_norm._normalizer = fake
    text_norm._status = "ok"

    out = text_norm.normalize_text("1998年")
    assert out == "一千九百九十八年"
    fake.normalize.assert_called_once()


def test_normalize_protects_control_tokens():
    """`[oral_3]` 等控制 token 不应被传给 normalizer，应该用占位符替换 + 还原。"""
    received = []

    def fake_normalize(text):
        received.append(text)
        # 假装 normalizer 把 1998 变成中文念法（占位符不动）
        return text.replace("1998", "一九九八")

    fake = MagicMock()
    fake.normalize.side_effect = fake_normalize
    text_norm._normalizer = fake
    text_norm._status = "ok"

    out = text_norm.normalize_text("[oral_3]今天1998年[uv_break]")
    # 输入给 normalizer 的应是占位符版（无 [oral_3] / [uv_break] 字面量）
    assert "[oral_3]" not in received[0]
    assert "[uv_break]" not in received[0]
    # 还原后 token 完整保留
    assert "[oral_3]" in out
    assert "[uv_break]" in out
    # 数字念法被改了
    assert "一九九八" in out


def test_normalize_falls_back_on_exception(caplog):
    """normalizer.normalize 抛错 → 记 WARNING + return 原文。"""
    fake = MagicMock()
    fake.normalize.side_effect = RuntimeError("FST broken")
    text_norm._normalizer = fake
    text_norm._status = "ok"

    with caplog.at_level(logging.WARNING):
        out = text_norm.normalize_text("3.14")
    assert out == "3.14"
    assert any("normalize 失败" in r.message for r in caplog.records)


# === load_normalizer：状态机 + fallback ===

def test_load_normalizer_disabled_when_flag_off(monkeypatch):
    """TEXT_NORM_ENABLED=False → status='disabled'，不尝试加载。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", False)
    text_norm._normalizer = None
    text_norm._status = "loading"
    text_norm.load_normalizer()
    assert text_norm._status == "disabled"
    assert text_norm._normalizer is None


def test_load_normalizer_disabled_when_lib_missing(monkeypatch):
    """WeTextProcessing 未装（ZhNormalizer is None）→ status='disabled'。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(text_norm, "ZhNormalizer", None)
    text_norm._normalizer = None
    text_norm._status = "loading"
    text_norm.load_normalizer()
    assert text_norm._status == "disabled"
    assert text_norm._normalizer is None


def test_load_normalizer_error_when_init_raises(monkeypatch, tmp_path, caplog):
    """ZhNormalizer 初始化抛错 → status='error'，不抛。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(config, "TEXT_NORM_CACHE_DIR", tmp_path / "wt_cache")

    def boom(**kwargs):
        raise RuntimeError("openfst 找不到")

    monkeypatch.setattr(text_norm, "ZhNormalizer", boom)
    text_norm._normalizer = None
    text_norm._status = "disabled"

    with caplog.at_level(logging.WARNING):
        text_norm.load_normalizer()  # 不抛
    assert text_norm._status == "error"
    assert text_norm._normalizer is None
    assert any("normalizer 加载失败" in r.message for r in caplog.records)


def test_load_normalizer_ok_when_init_succeeds(monkeypatch, tmp_path):
    """ZhNormalizer 初始化成功 → status='ok'，_normalizer 非空。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(config, "TEXT_NORM_CACHE_DIR", tmp_path / "wt_cache")

    fake_instance = MagicMock()
    fake_cls = MagicMock(return_value=fake_instance)
    monkeypatch.setattr(text_norm, "ZhNormalizer", fake_cls)
    text_norm._normalizer = None
    text_norm._status = "disabled"

    text_norm.load_normalizer()
    assert text_norm._status == "ok"
    assert text_norm._normalizer is fake_instance
    # 缓存目录应被传给 ZhNormalizer
    assert fake_cls.called
    kwargs = fake_cls.call_args.kwargs
    assert kwargs.get("remove_erhua") is False  # 默认值


def test_load_normalizer_passes_remove_erhua_flag(monkeypatch, tmp_path):
    """TEXT_NORM_REMOVE_ERHUA=True 应传给 ZhNormalizer。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(config, "TEXT_NORM_REMOVE_ERHUA", True)
    monkeypatch.setattr(config, "TEXT_NORM_CACHE_DIR", tmp_path / "wt_cache")

    fake_cls = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(text_norm, "ZhNormalizer", fake_cls)

    text_norm.load_normalizer()
    assert fake_cls.call_args.kwargs.get("remove_erhua") is True


def test_load_normalizer_creates_cache_dir(monkeypatch, tmp_path):
    """缓存目录不存在时应被创建。"""
    cache = tmp_path / "wt_cache_nested" / "subdir"
    assert not cache.exists()

    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(config, "TEXT_NORM_CACHE_DIR", cache)
    monkeypatch.setattr(text_norm, "ZhNormalizer", MagicMock(return_value=MagicMock()))

    text_norm.load_normalizer()
    assert cache.exists()


def test_load_normalizer_idempotent(monkeypatch, tmp_path):
    """已加载时再调 load_normalizer 不应重复初始化。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(config, "TEXT_NORM_CACHE_DIR", tmp_path / "wt_cache")

    fake_cls = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(text_norm, "ZhNormalizer", fake_cls)

    text_norm.load_normalizer()
    text_norm.load_normalizer()
    text_norm.load_normalizer()

    assert fake_cls.call_count == 1  # 只 init 一次


# === 真实库测试（装了 WeTextProcessing 时跑，否则 skip）===

_HAS_WT = True
try:
    from tn.chinese.normalizer import Normalizer  # noqa: F401
except Exception:
    _HAS_WT = False


@pytest.mark.skipif(not _HAS_WT, reason="WeTextProcessing 未安装")
def test_real_normalize_digits_to_chinese(monkeypatch, tmp_path):
    """真实库：1998 应被转成中文念法。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(config, "TEXT_NORM_CACHE_DIR", tmp_path / "wt_cache")
    text_norm._normalizer = None
    text_norm._status = "disabled"
    text_norm.load_normalizer()
    if text_norm._status != "ok":
        pytest.skip("normalizer 加载失败（环境依赖问题）")

    out = text_norm.normalize_text("1998年")
    # 输出里不应再有阿拉伯数字
    assert all(c not in out for c in "0123456789"), f"out={out!r}"


@pytest.mark.skipif(not _HAS_WT, reason="WeTextProcessing 未安装")
def test_real_normalize_does_not_break_control_tokens(monkeypatch, tmp_path):
    """真实库：控制 token 必须被保护。"""
    monkeypatch.setattr(config, "TEXT_NORM_ENABLED", True)
    monkeypatch.setattr(config, "TEXT_NORM_CACHE_DIR", tmp_path / "wt_cache")
    text_norm._normalizer = None
    text_norm._status = "disabled"
    text_norm.load_normalizer()
    if text_norm._status != "ok":
        pytest.skip("normalizer 加载失败（环境依赖问题）")

    out = text_norm.normalize_text("[oral_3]今天1998年[uv_break]")
    assert "[oral_3]" in out
    assert "[uv_break]" in out
