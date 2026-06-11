"""测试 /api/health 路由：模型状态 + 队列状态。"""
from fastapi.testclient import TestClient

from app.core import chat_tts
from app.main import app


def test_health_returns_loading_when_model_not_loaded():
    """模型未加载时 status=loading, model_loaded=False。"""
    chat_tts._LOADED = False
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "loading"
    assert body["model_loaded"] is False
    assert body["queue_size"] == 0


def test_health_returns_ok_when_model_loaded():
    """模型已加载时 status=ok, model_loaded=True, queue_size>=0。"""
    chat_tts._LOADED = True
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert isinstance(body["queue_size"], int)
    assert body["queue_size"] >= 0
    chat_tts._LOADED = False  # 还原
