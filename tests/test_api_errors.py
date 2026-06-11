"""测试 AppError 异常 handler：业务异常应被统一转为 `{detail, code}` JSON 响应。"""
from fastapi.testclient import TestClient

from app.main import app


def test_apperror_returns_structured_json():
    """AppError 抛在路由里，客户端应拿到 {detail, code} 响应。"""
    from app.core.exceptions import CardNotFoundError

    def _trigger():
        raise CardNotFoundError("测试卡不存在")

    # 幂等注册：避免 pytest 多次 collect 时重复加路由报错
    if not any(getattr(r, "path", None) == "/__test_apperror" for r in app.routes):
        app.add_api_route("/__test_apperror", _trigger, methods=["GET"])

    client = TestClient(app)
    res = client.get("/__test_apperror")
    assert res.status_code == 404
    body = res.json()
    assert body == {"detail": "测试卡不存在", "code": "CARD_NOT_FOUND"}
