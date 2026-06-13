"""text_chunker.split_text 的单元测试。

覆盖：
- 纯中文 / 纯英文 / 中英混排
- 标点分级（强 / 中 / 弱 / 硬切）
- 控制 token 不被截断 + 字数不算 token
- 空段 / 纯标点段过滤
- 短段合并
- 无标点超长串硬切
- 边界：空字符串、纯空白、纯控制 token
"""
from __future__ import annotations

from app.core.text_chunker import split_text


def test_empty_string_returns_empty_list():
    assert split_text("") == []


def test_pure_whitespace_returns_empty_list():
    assert split_text("   \n\t  ") == []


def test_pure_punctuation_filtered():
    """全是标点 / 没有有效内容 → 空列表。"""
    assert split_text("。。。、、，") == []


def test_single_short_chinese_sentence():
    """短句不切，原样返回。"""
    out = split_text("你好世界。")
    assert out == ["你好世界。"]


def test_strong_punct_splits():
    """强切：句号 / 问号 / 感叹号 都必切。"""
    out = split_text("第一句。第二句？第三句！")
    assert out == ["第一句。", "第二句？", "第三句！"]


def test_newline_is_strong_split():
    """换行算强切。"""
    out = split_text("第一行\n第二行\n第三行")
    assert out == ["第一行", "第二行", "第三行"]


def test_medium_split_when_over_soft_max():
    """长段用中切（；：）继续切。"""
    text = "前半部分内容很长很长有很多字；后半部分内容也很长很长很多字。"
    out = split_text(text, soft_max=15, hard_max=40, min_chars=1)
    # 应被分号切开，每段都 ≤ soft_max
    assert len(out) >= 2
    assert all(len(seg) <= 40 for seg in out)


def test_weak_split_when_over_soft_max_no_medium():
    """长段无中切标点 → 用弱切（，）继续切。"""
    text = "前面一部分内容比较长，后面一部分内容也比较长。"
    out = split_text(text, soft_max=12, hard_max=40, min_chars=1)
    # 应被逗号切开
    assert len(out) >= 2


def test_short_segments_filtered_when_only_punct():
    """只有标点的段被过滤。"""
    out = split_text("好。。。、，今天。")
    # "好。"、"。"、"。"、"、，今天。"... 实际依赖切分逻辑，但纯标点段必须被滤掉
    # 最关键：每个返回段都必须含字母 / 数字 / 中文
    assert all(any(c.isalnum() or "一" <= c <= "鿿" for c in seg) for seg in out)


def test_short_segments_merge_with_next():
    """< min_chars 的段并入后一段。"""
    text = "好。今天天气真不错。"
    out = split_text(text, soft_max=20, hard_max=35, min_chars=4)
    # "好。" 仅 1 个有效字 < 4，应并入下一段
    assert len(out) == 1
    assert out[0] == "好。今天天气真不错。"


def test_short_merge_does_not_exceed_soft_max():
    """合并后会超过 soft_max → 不合并。"""
    text = "你。今天我们出门去看了一场电影非常精彩值得推荐给所有人观看。"
    # "你。" 长 1 字，下一段长 ~28 字；soft_max=20 → 1+28=29 > 20，不合并
    out = split_text(text, soft_max=20, hard_max=40, min_chars=4)
    # 第一段「你。」应保留独立
    assert out[0] == "你。"


def test_no_punct_long_string_within_hard_max_kept_whole():
    """无标点但长度 ≤ hard_max → 保持整段不切（仅当 > hard_max 才硬切）。"""
    text = "今天天气真的很好阳光也很温暖空气特别清新让人心情愉悦"  # 26 字
    out = split_text(text, soft_max=20, hard_max=35, min_chars=4)
    # 26 ≤ 35，不硬切
    assert out == [text]


def test_no_punct_long_string_hard_cut():
    """无标点且 > hard_max → 硬切兜底。"""
    text = "啊" * 50  # 50 个字，无标点
    out = split_text(text, soft_max=20, hard_max=20, min_chars=1)
    assert len(out) > 1
    # 每段不超过 hard_max
    assert all(len(seg) <= 20 for seg in out)
    # 拼起来还是原文
    assert "".join(out) == text


def test_english_sentence_splits():
    """纯英文按 . ? ! 切。"""
    out = split_text("Hello world. How are you? I am fine!", soft_max=20, hard_max=40, min_chars=2)
    assert len(out) >= 2  # 至少切成多段


def test_mixed_chinese_english():
    """中英混排，标点都识别。"""
    out = split_text("Hello, 你好。OK 123 ok。", soft_max=30, hard_max=40, min_chars=2)
    # 应切成两段（按句号）
    assert len(out) == 2


def test_emoji_kept_with_alphanum():
    """含字母 / 数字的段（即使有 emoji）应保留。"""
    out = split_text("OK 123 😀。", soft_max=20, hard_max=40, min_chars=2)
    assert len(out) == 1
    assert "OK" in out[0]


def test_control_token_preserved_in_output():
    """控制 token 不被切碎、还原后完整保留。"""
    text = "[oral_3]今天天气真好。[laugh_2]明天也不错。"
    out = split_text(text, soft_max=30, hard_max=40, min_chars=1)
    # token 完整出现在某一段里
    joined = "".join(out)
    assert "[oral_3]" in joined
    assert "[laugh_2]" in joined


def test_control_token_does_not_count_toward_length():
    """控制 token 不计字数：含 token 的段不会因 token 字符多而被强切。

    `[oral_3]今天1998年[uv_break]` 的可见字符是 8 个（今,天,1,9,9,8,年），
    实际加上 token 是 28 字符，但应被算成 ~7 字，不会触发硬切。
    """
    text = "[oral_3]今天1998年[uv_break]"
    out = split_text(text, soft_max=10, hard_max=15, min_chars=1)
    # 只产生 1 段，token 完整保留
    assert len(out) == 1
    assert "[oral_3]" in out[0]
    assert "[uv_break]" in out[0]
    assert "1998" in out[0]


def test_only_control_tokens_filtered():
    """全是控制 token、无内容 → 跳过。"""
    out = split_text("[oral_3][laugh_2][uv_break]")
    assert out == []


def test_uv_break_preserved():
    """[uv_break] 控制 token 在切分后仍完整。"""
    out = split_text("第一段[uv_break]第二段。第三段[uv_break]结束。")
    joined = "".join(out)
    assert joined.count("[uv_break]") == 2


def test_uv_break_not_split_inside():
    """[uv_break] 内部的 _ break 等字符不应被解读成切分点。"""
    text = "[uv_break]"
    out = split_text(text)
    # 全是 token 没有内容，过滤后为空
    assert out == []


def test_repeated_strong_punct_no_empty_seg():
    """连续句末标点不产生空段。"""
    out = split_text("好！！！真好。。。")
    # 每段都有内容
    for seg in out:
        assert seg.strip()


def test_large_paragraph_chunked_under_soft_max_when_possible():
    """长段落按标点切，绝大多数段在 soft_max 范围内。"""
    text = (
        "今天阳光明媚，我和朋友去了公园散步；"
        "我们看到很多孩子在玩耍，有的在滑滑梯，有的在荡秋千。"
        "公园的花开得正好，红的、黄的、紫的，五颜六色非常漂亮。"
        "我们走累了就找了个长椅坐下，聊了很久家常。"
    )
    out = split_text(text, soft_max=20, hard_max=35, min_chars=4)
    # 切完拼起来内容不丢（除空白可能 strip）
    joined = "".join(out)
    # 主要内容字符都还在
    for fragment in ["阳光明媚", "公园散步", "滑滑梯", "荡秋千", "五颜六色", "长椅", "聊了很久"]:
        assert fragment in joined


def test_segments_strip_trailing_whitespace():
    """段尾空白应被 strip 掉。"""
    out = split_text("第一段。   第二段。")
    assert all(seg == seg.strip() for seg in out)


def test_default_soft_max_20_chars_chinese():
    """默认 soft_max=20 时，含分号的长段会被中切。"""
    text = "这是一段非常非常长的文字内容用来测试中切；后面还有更多内容继续测试。"
    out = split_text(text)  # 全部默认
    # 至少切成 2 段（；切了 + 。切了）
    assert len(out) >= 2


def test_punct_at_start_does_not_create_empty_seg():
    """开头是标点也不产生空段。"""
    out = split_text("，你好。")
    # 开头那个「，」会留在第一段末尾，但 strip + 内容判断后保留有效段
    assert all(seg for seg in out)
    joined = "".join(out)
    assert "你好" in joined


def test_chunker_does_not_drop_content():
    """切分不能丢字（除了被过滤的纯标点段）。"""
    text = "今天天气真好，我们一起去公园散步吧；顺便买点东西。"
    out = split_text(text, soft_max=15, hard_max=30, min_chars=1)
    joined = "".join(out)
    # 所有有效字都保留
    for c in "今天天气真好我们一起去公园散步吧顺便买点东西":
        assert c in joined
