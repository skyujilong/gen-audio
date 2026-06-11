from app.core.subtitle import build_srt, slice_by_punctuation


def test_build_srt_single_segment_full_duration():
    srt = build_srt([("你好世界", 0.0, 3.0)])
    assert "00:00:00,000 --> 00:00:03,000" in srt
    assert "你好世界" in srt
    assert srt.startswith("1\n")  # 序号从 1 开始


def test_build_srt_multiple_segments_numbered():
    srt = build_srt([
        ("第一句。", 0.0, 1.5),
        ("第二句。", 1.5, 3.0),
        ("第三句", 3.0, 5.0),
    ])
    assert "1\n" in srt
    assert "2\n" in srt
    assert "3\n" in srt
    assert "第一句" in srt
    assert "第二句" in srt
    assert "第三句" in srt


def test_build_srt_empty_list_returns_empty_string():
    assert build_srt([]) == ""


def test_build_srt_time_format():
    srt = build_srt([("x", 3661.500, 3662.750)])
    # 3661.5s = 1h 1m 1.5s → 01:01:01,500
    # 3662.75s = 1h 1m 2.75s → 01:01:02,750
    assert "01:01:01,500 --> 01:01:02,750" in srt


def test_slice_by_punctuation_chinese():
    text = "你好，世界。今天天气不错。"
    pieces = slice_by_punctuation(text)
    assert pieces == ["你好，世界。", "今天天气不错。"]


def test_slice_by_punctuation_no_punct():
    text = "没有标点的长句子"
    assert slice_by_punctuation(text) == ["没有标点的长句子"]


def test_slice_by_punctuation_keeps_punctuation():
    text = "第一句。第二句！第三句？第四句"
    pieces = slice_by_punctuation(text)
    assert pieces == ["第一句。", "第二句！", "第三句？", "第四句"]
