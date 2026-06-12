"""测试 SpeakerStorage：.pt 文件 I/O。"""
from pathlib import Path

import pytest
import torch

from app.storage.speakers import SpeakerStorage


@pytest.fixture
def storage(tmp_path: Path) -> SpeakerStorage:
    return SpeakerStorage(tmp_path / "speakers")


def test_dir_creates_directory(tmp_path: Path):
    s = SpeakerStorage(tmp_path / "sub" / "speakers")
    d = s.dir()
    assert d.exists()
    assert d.is_dir()


def test_save_and_load_tensor_roundtrip(storage: SpeakerStorage):
    tensor = torch.randn(768, 32)  # 类似 ChatTTS speaker embedding shape
    storage.save_tensor(1, tensor)
    loaded = storage.load_tensor(1)
    assert torch.allclose(tensor, loaded)


def test_exists(storage: SpeakerStorage):
    assert storage.exists(1) is False
    storage.save_tensor(1, torch.randn(4, 4))
    assert storage.exists(1) is True


def test_load_tensor_raises_for_missing(storage: SpeakerStorage):
    with pytest.raises(FileNotFoundError):
        storage.load_tensor(999)


def test_load_tensor_bytes_roundtrip(storage: SpeakerStorage):
    tensor = torch.randn(100, 100)
    storage.save_tensor(1, tensor)
    raw = storage.load_tensor_bytes(1)
    assert isinstance(raw, bytes)
    assert len(raw) > 0
    # 重新 load 能恢复
    import io
    reloaded = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=False)
    assert torch.allclose(tensor, reloaded)


def test_delete_removes_file(storage: SpeakerStorage):
    storage.save_tensor(1, torch.randn(4, 4))
    assert storage.delete(1) is True
    assert storage.exists(1) is False


def test_delete_missing_returns_false(storage: SpeakerStorage):
    assert storage.delete(999) is False


def test_save_overwrites(storage: SpeakerStorage):
    storage.save_tensor(1, torch.tensor([1.0, 2.0]))
    storage.save_tensor(1, torch.tensor([3.0, 4.0]))
    loaded = storage.load_tensor(1)
    assert torch.allclose(loaded, torch.tensor([3.0, 4.0]))


def test_multiple_speakers_isolated(storage: SpeakerStorage):
    t1 = torch.tensor([1.0])
    t2 = torch.tensor([2.0])
    storage.save_tensor(1, t1)
    storage.save_tensor(2, t2)
    assert torch.allclose(storage.load_tensor(1), t1)
    assert torch.allclose(storage.load_tensor(2), t2)
