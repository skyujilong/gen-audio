"""测试 AppError 异常 handler：业务异常应被统一转为 `{detail, code}` JSON 响应。

通过访问不存在的 card 触发 CardNotFoundError 来验证。
"""
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.db.database import init_schema
from app.main import app


def test_apperror_returns_structured_json(tmp_path: Path):
    """AppError 抛在路由里，客户端应拿到 {detail, code} 响应。"""
    config.DATA_ROOT = tmp_path / "data"
    config.DB_PATH = config.DATA_ROOT / "gen-audio.db"
    init_schema(config.DB_PATH)

    client = TestClient(app)
    res = client.get("/api/cards/9999")
    assert res.status_code == 404
    body = res.json()
    assert body == {"detail": "参数卡 9999 不存在", "code": "CARD_NOT_FOUND"}
