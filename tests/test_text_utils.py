"""测试 text_utils 的 token 替换 / 恢复功能。

ChatTTS 0.2.5 的 refine_text 阶段会修改文本（加标点、改口语化），
但会破坏我们注入的控制 token（如 [oral_2]）。
解决：refine 前用占位符替换 → refine 后恢复原 token。
"""
from app.core.text_utils import replace_tokens, restore_tokens


def test_replace_tokens_no_tokens_returns_unchanged():
    """无 token 时 text 不变，pairs 为空。"""
    text = "你好，这是一段普通文本。"
    new_text, pairs = replace_tokens(text)
    assert new_text == text
    assert pairs == []


def test_replace_tokens_single_oral():
    text = "你好[oral_2]世界"
    new_text, pairs = replace_tokens(text)
    assert "[oral_2]" not in new_text
    assert len(pairs) == 1
    assert pairs[0][1] == "[oral_2]"
    # 包含占位符
    assert pairs[0][0] in new_text


def test_replace_tokens_multiple_oral_laugh_break():
    text = "[oral_2]你好[laugh_1]世界[break_3]。"
    new_text, pairs = replace_tokens(text)
    assert "[oral_2]" not in new_text
    assert "[laugh_1]" not in new_text
    assert "[break_3]" not in new_text
    assert len(pairs) == 3
    # 恢复
    restored = restore_tokens(new_text, pairs)
    assert restored == text


def test_replace_tokens_speed_token():
    text = "[speed_5]你好"
    new_text, pairs = replace_tokens(text)
    assert "[speed_5]" not in new_text
    assert len(pairs) == 1
    assert pairs[0][1] == "[speed_5]"
    restored = restore_tokens(new_text, pairs)
    assert restored == text


def test_replace_tokens_uv_break():
    """[uv_break] 是 ChatTTS 的停顿 token。"""
    text = "你好[uv_break]世界"
    new_text, pairs = replace_tokens(text)
    assert "[uv_break]" not in new_text
    assert len(pairs) == 1
    assert pairs[0][1] == "[uv_break]"
    restored = restore_tokens(new_text, pairs)
    assert restored == text


def test_replace_tokens_unique_placeholders():
    """多个 token 的占位符应互不相同。"""
    text = "[oral_1]x[oral_2]y[oral_3]z"
    new_text, pairs = replace_tokens(text)
    placeholders = [p[0] for p in pairs]
    assert len(set(placeholders)) == 3  # 唯一


def test_replace_tokens_roundtrip_preserves_chinese_text():
    """替换 → 恢复 应保持中文原貌。"""
    text = "[oral_2]亲爱的朋友们，大家好[laugh_1]！今天[break_3]我们来聊聊[speed_5]这个话题。"
    new_text, pairs = replace_tokens(text)
    # 中文（不在 token pattern 里的部分）应保持
    assert "亲爱的朋友们" in new_text
    assert "今天" in new_text
    assert "我们来聊聊" in new_text
    assert "这个话题。" in new_text
    # 恢复
    restored = restore_tokens(new_text, pairs)
    assert restored == text


def test_restore_tokens_with_empty_pairs_returns_unchanged():
    text = "普通文本"
    assert restore_tokens(text, []) == text
