import json
from pathlib import Path

import pytest

from app.storage.files import (
    card_dir,
    job_dir,
    write_demo_files,
    write_synthesis_files,
    safe_delete,
)
from app.core.params import TtsParams


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "data"


def test_card_dir_creates_subdir(data_root: Path):
    p = card_dir(data_root, 42)
    assert p.exists()
    assert p == data_root / "audio" / "42"


def test_job_dir_creates_nested_subdir(data_root: Path):
    p = job_dir(data_root, card_id=42, job_id="abc-uuid")
    assert p.exists()
    assert p == data_root / "audio" / "42" / "jobs" / "abc-uuid"


def test_write_demo_files_writes_three_files(data_root: Path):
    params = TtsParams(seed=1, speaker="x")
    paths = write_demo_files(
        data_root=data_root,
        card_id=1,
        demo_text="hello",
        demo_wav_bytes=b"FAKEWAV",
        demo_srt="1\n00:00:00,000 --> 00:00:01,000\nhello\n",
        params=params,
    )
    assert paths["demo_audio_path"] == "audio/1/demo.wav"
    assert paths["demo_subtitle_path"] == "audio/1/demo.srt"
    assert paths["params_path"] == "audio/1/params.json"

    # 实际文件存在且内容正确
    full = data_root / "audio" / "1" / "demo.wav"
    assert full.read_bytes() == b"FAKEWAV"
    srt_full = data_root / "audio" / "1" / "demo.srt"
    assert "hello" in srt_full.read_text(encoding="utf-8")
    params_full = data_root / "audio" / "1" / "params.json"
    assert json.loads(params_full.read_text(encoding="utf-8"))["seed"] == 1


def test_write_synthesis_files_writes_three_files(data_root: Path):
    params = TtsParams(seed=2, speaker="y")
    paths = write_synthesis_files(
        data_root=data_root,
        card_id=1,
        job_id="jid",
        audio_bytes=b"XYZ",
        srt="srt-content",
        params=params,
    )
    assert paths["audio_path"] == "audio/1/jobs/jid/audio.wav"
    assert paths["subtitle_path"] == "audio/1/jobs/jid/subtitle.srt"
    assert paths["params_path"] == "audio/1/jobs/jid/params.json"

    assert (data_root / "audio" / "1" / "jobs" / "jid" / "audio.wav").read_bytes() == b"XYZ"


def test_safe_delete_removes_file_and_empty_parents(data_root: Path):
    target = data_root / "audio" / "1" / "jobs" / "jid" / "audio.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")

    safe_delete(data_root, "audio/1/jobs/jid/audio.wav")

    assert not target.exists()
    # 父目录会被清空也清掉（rmdir 链）
    assert not (data_root / "audio" / "1" / "jobs" / "jid").exists()
    assert not (data_root / "audio" / "1" / "jobs").exists()


def test_safe_delete_missing_file_is_noop(data_root: Path):
    # 不存在的路径不抛错
    safe_delete(data_root, "audio/999/missing.wav")
