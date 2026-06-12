"""音色库文件存储：speaker embedding 的 .pt 文件 I/O。

ChatTTS 的 speaker embedding 是一个 `torch.Tensor`，可以序列化为 `.pt` 文件
（用 `torch.save` / `torch.load`）。本模块：
- `SpeakerStorage` 类封装一个目录的 .pt 文件读写
- `dir()`：返回 `speakers/` 目录路径（自动创建）
- `save_tensor(speaker_id, tensor)`：存为 `speakers/{id}.pt`
- `load_tensor(speaker_id)`：读出 tensor
- `load_tensor_bytes(speaker_id)`：读出原始字节（前端直接传 base64 时用）
- `delete(speaker_id)`：删文件
- `exists(speaker_id)`：判存在

参考 ChatTTS-Enhanced-main 的 `processors/config_processor.py`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import torch


class SpeakerStorage:
    """音色库 .pt 文件存储，绑一个目录（一般是 `DATA_ROOT/speakers`）。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def dir(self) -> Path:
        """返回 speakers 根目录（不存在则创建）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def _path_for(self, speaker_id: int) -> Path:
        return self.root / f"{int(speaker_id)}.pt"

    def exists(self, speaker_id: int) -> bool:
        return self._path_for(speaker_id).exists()

    def save_tensor(self, speaker_id: int, tensor: torch.Tensor) -> Path:
        """存 torch.Tensor 为 .pt 文件，返回写入路径。"""
        path = self._path_for(speaker_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(tensor, path)
        return path

    def load_tensor(self, speaker_id: int) -> torch.Tensor:
        """读出 torch.Tensor。文件不存在 → FileNotFoundError。"""
        path = self._path_for(speaker_id)
        if not path.exists():
            raise FileNotFoundError(f"speaker {speaker_id} 不存在: {path}")
        return torch.load(path, map_location="cpu", weights_only=False)

    def load_tensor_bytes(self, speaker_id: int) -> bytes:
        """读出原始 .pt 字节（前端下载或迁移用）。"""
        path = self._path_for(speaker_id)
        if not path.exists():
            raise FileNotFoundError(f"speaker {speaker_id} 不存在: {path}")
        return path.read_bytes()

    def delete(self, speaker_id: int) -> bool:
        """删 .pt 文件。返回 True=删了，False=本来就不存在。"""
        path = self._path_for(speaker_id)
        if path.exists():
            path.unlink()
            return True
        return False
