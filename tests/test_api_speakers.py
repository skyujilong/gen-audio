"""测试 /api/speakers 路由：CRUD + 上传 + 搜索 + 收藏 + 级联删除 + 随机音色。"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient

from app import config
from app.db.database import init_schema
from app.db.queries import insert_card
from app.main import app
from app.core.params import TtsParams


# === 公共 setup ===

def _setup(tmp_path: Path) -> tuple[TestClient, Path]:
    """建空 DB + 指向 tmp 的 DATA_ROOT + TestClient。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    config.SPEAKERS_DIR = config.DATA_ROOT / "speakers"
    init_schema(config.DB_PATH)
    return TestClient(app), config.SPEAKERS_DIR


def _sample_base64() -> str:
    """生成一段真实 tensor 的 base64。"""
    import base64
    buf = io.BytesIO()
    torch.save(torch.tensor([1.0, 2.0, 3.0]), buf)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _sample_card_params() -> TtsParams:
    return TtsParams(seed=1, speaker="x")


# === 列表 / 详情 ===

def test_list_empty(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.get("/api/speakers")
    assert res.status_code == 200
    assert res.json() == []


def test_list_returns_no_tensor_field(tmp_path: Path):
    """列表项不应带 tensor（节省带宽）。"""
    client, _ = _setup(tmp_path)
    client.post("/api/speakers", json={
        "name": "alice",
        "tensor_base64": _sample_base64(),
    })
    res = client.get("/api/speakers")
    arr = res.json()
    assert len(arr) == 1
    assert "tensor_base64" not in arr[0]
    assert arr[0]["name"] == "alice"
    assert arr[0]["is_favorited"] is False


def test_get_speaker_returns_tensor(tmp_path: Path):
    client, _ = _setup(tmp_path)
    create = client.post("/api/speakers", json={
        "name": "bob",
        "tensor_base64": _sample_base64(),
        "tags": ["male", "calm"],
    })
    sid = create.json()["id"]
    res = client.get(f"/api/speakers/{sid}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == sid
    assert body["name"] == "bob"
    assert body["tags"] == ["male", "calm"]
    assert "tensor_base64" in body
    assert isinstance(body["tensor_base64"], str)


def test_get_speaker_404(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.get("/api/speakers/9999")
    assert res.status_code == 404
    assert res.json()["code"] == "SPEAKER_NOT_FOUND"


# === 创建 ===

def test_create_speaker_returns_id(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.post("/api/speakers", json={
        "name": "carol",
        "tensor_base64": _sample_base64(),
    })
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["id"], int)
    assert body["id"] >= 1
    assert body["name"] == "carol"


def test_create_speaker_missing_name(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.post("/api/speakers", json={"tensor_base64": _sample_base64()})
    # Pydantic 422 → FastAPI 默认行为
    assert res.status_code == 422


def test_create_speaker_missing_tensor(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.post("/api/speakers", json={"name": "x"})
    assert res.status_code == 422


def test_create_writes_pt_file(tmp_path: Path):
    """创建后应该把 .pt 写到 SPEAKERS_DIR。"""
    client, speakers_dir = _setup(tmp_path)
    res = client.post("/api/speakers", json={
        "name": "dave",
        "tensor_base64": _sample_base64(),
    })
    sid = res.json()["id"]
    assert (speakers_dir / f"{sid}.pt").exists()


# === 列表过滤 / 搜索 ===

def test_list_filter_favorited(tmp_path: Path):
    client, _ = _setup(tmp_path)
    a = client.post("/api/speakers", json={"name": "a", "tensor_base64": _sample_base64()}).json()
    b = client.post("/api/speakers", json={"name": "b", "tensor_base64": _sample_base64()}).json()
    # 收藏 a
    client.post(f"/api/speakers/{a['id']}/favorite")
    res = client.get("/api/speakers?favorited=true")
    arr = res.json()
    assert len(arr) == 1
    assert arr[0]["id"] == a["id"]


def test_list_search_by_name(tmp_path: Path):
    client, _ = _setup(tmp_path)
    client.post("/api/speakers", json={"name": "男声A", "tensor_base64": _sample_base64()})
    client.post("/api/speakers", json={"name": "女声B", "tensor_base64": _sample_base64()})
    res = client.get("/api/speakers?search=男声")
    arr = res.json()
    assert len(arr) == 1
    assert arr[0]["name"] == "男声A"


# === 更新 ===

def test_update_speaker_name(tmp_path: Path):
    client, _ = _setup(tmp_path)
    sid = client.post("/api/speakers", json={
        "name": "old", "tensor_base64": _sample_base64()
    }).json()["id"]
    res = client.patch(f"/api/speakers/{sid}", json={"name": "new"})
    assert res.status_code == 200
    assert res.json()["name"] == "new"


def test_update_speaker_tags(tmp_path: Path):
    client, _ = _setup(tmp_path)
    sid = client.post("/api/speakers", json={
        "name": "x", "tensor_base64": _sample_base64()
    }).json()["id"]
    res = client.patch(f"/api/speakers/{sid}", json={"tags": ["a", "b"]})
    assert res.json()["tags"] == ["a", "b"]


def test_update_speaker_favorite(tmp_path: Path):
    client, _ = _setup(tmp_path)
    sid = client.post("/api/speakers", json={
        "name": "x", "tensor_base64": _sample_base64()
    }).json()["id"]
    res = client.patch(f"/api/speakers/{sid}", json={"is_favorited": True})
    assert res.json()["is_favorited"] is True


def test_update_speaker_404(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.patch("/api/speakers/9999", json={"name": "x"})
    assert res.status_code == 404


# === 删除 ===

def test_delete_speaker(tmp_path: Path):
    client, speakers_dir = _setup(tmp_path)
    sid = client.post("/api/speakers", json={
        "name": "x", "tensor_base64": _sample_base64()
    }).json()["id"]
    res = client.delete(f"/api/speakers/{sid}")
    assert res.status_code == 204
    assert client.get(f"/api/speakers/{sid}").status_code == 404
    assert not (speakers_dir / f"{sid}.pt").exists()


def test_delete_speaker_404(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.delete("/api/speakers/9999")
    assert res.status_code == 404


def test_delete_speaker_clears_card_speaker_id(tmp_path: Path):
    """删音色库某项后，引用它的 card.speaker_id 应被置 NULL。"""
    client, _ = _setup(tmp_path)
    sid = client.post("/api/speakers", json={
        "name": "x", "tensor_base64": _sample_base64()
    }).json()["id"]
    # 插一张绑定该音色的 card
    cid = insert_card(
        config.DB_PATH,
        name="my-card",
        params=_sample_card_params(),
        demo_text="hi",
        demo_audio_path="audio/x/demo.wav",
        demo_subtitle_path="audio/x/demo.srt",
        speaker_id=sid,
    )
    # 删音色
    res = client.delete(f"/api/speakers/{sid}")
    assert res.status_code == 204
    # card.speaker_id 应为 NULL
    res = client.get(f"/api/cards/{cid}")
    assert res.status_code == 200
    assert res.json()["params"]["speaker"] == "x"  # 字符串快照保留
    # speaker_id 不在响应里（CardListItem 不带），但 DB 查询可确认
    from app.db.database import get_connection
    with get_connection(config.DB_PATH) as conn:
        row = conn.execute("SELECT speaker_id FROM cards WHERE id = ?", (cid,)).fetchone()
        assert row["speaker_id"] is None


# === 收藏切换 ===

def test_toggle_favorite(tmp_path: Path):
    client, _ = _setup(tmp_path)
    sid = client.post("/api/speakers", json={
        "name": "x", "tensor_base64": _sample_base64()
    }).json()["id"]
    r1 = client.post(f"/api/speakers/{sid}/favorite")
    assert r1.status_code == 200
    assert r1.json()["is_favorited"] is True
    r2 = client.post(f"/api/speakers/{sid}/favorite")
    assert r2.json()["is_favorited"] is False


def test_toggle_favorite_404(tmp_path: Path):
    client, _ = _setup(tmp_path)
    res = client.post("/api/speakers/9999/favorite")
    assert res.status_code == 404


# === 随机音色 ===

def test_random_speaker_returns_tensor_base64(tmp_path: Path, monkeypatch):
    """GET /api/speakers/random 返回 {speaker_id, tensor_base64}。"""
    import base64
    import io
    import torch as _torch
    import app.api.speakers as speakers_mod

    # mock 真实 _random_speaker，返一段可解出 tensor 的 base64
    buf = io.BytesIO()
    _torch.save(_torch.tensor([1.0, 2.0, 3.0]), buf)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    # 注意：speakers.py 用 `from ..core.chat_tts import _random_speaker`，
    # 所以名字绑到了 speakers_mod 模块内，要 patch 这个。
    monkeypatch.setattr(speakers_mod, "_random_speaker", lambda: encoded)

    client, _ = _setup(tmp_path)
    res = client.get("/api/speakers/random")
    assert res.status_code == 200
    body = res.json()
    # speaker_id 应为 null（不写库）
    assert "speaker_id" in body
    assert body["speaker_id"] is None
    assert "tensor_base64" in body
    assert isinstance(body["tensor_base64"], str)
    assert len(body["tensor_base64"]) > 0
    # 能用 base64 解出 tensor
    raw = base64.b64decode(body["tensor_base64"])
    t = _torch.load(io.BytesIO(raw), map_location="cpu", weights_only=False)
    assert isinstance(t, _torch.Tensor)


# === 上传 .pt 文件 ===

def test_upload_pt_file(tmp_path: Path):
    """POST /upload 接受 .pt 文件 multipart，写库 + 写盘。"""
    client, speakers_dir = _setup(tmp_path)
    buf = io.BytesIO()
    torch.save(torch.tensor([7.0, 8.0]), buf)
    buf.seek(0)
    res = client.post(
        "/api/speakers/upload",
        files={"file": ("speaker.pt", buf, "application/octet-stream")},
        data={"name": "uploaded"},
    )
    assert res.status_code == 200
    body = res.json()
    sid = body["id"]
    assert isinstance(sid, int)
    assert (speakers_dir / f"{sid}.pt").exists()
    # GET 能拿到 tensor
    detail = client.get(f"/api/speakers/{sid}").json()
    assert "tensor_base64" in detail


def test_upload_pt_file_with_tags_and_favorite(tmp_path: Path):
    client, _ = _setup(tmp_path)
    buf = io.BytesIO()
    torch.save(torch.tensor([1.0]), buf)
    buf.seek(0)
    res = client.post(
        "/api/speakers/upload",
        files={"file": ("x.pt", buf, "application/octet-stream")},
        data={"name": "tagged", "tags": "[\"a\",\"b\"]", "is_favorited": "true"},
    )
    assert res.status_code == 200
    body = client.get(f"/api/speakers/{res.json()['id']}").json()
    assert body["tags"] == ["a", "b"]
    assert body["is_favorited"] is True


def test_upload_invalid_pt_file(tmp_path: Path):
    client, _ = _setup(tmp_path)
    buf = io.BytesIO(b"not a real pt file")
    buf.seek(0)
    res = client.post(
        "/api/speakers/upload",
        files={"file": ("bad.pt", buf, "application/octet-stream")},
        data={"name": "bad"},
    )
    assert res.status_code == 400
